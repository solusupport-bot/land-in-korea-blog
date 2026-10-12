#!/usr/bin/env python3
"""
Fills in a real photo for every post that has an `image_query` in its front
matter but no `image` yet. Tries the Korea Tourism Organization's Odii
(관광지 오디오 가이드정보) API FIRST — real official images of actual named
attractions — since that beats stock photography when it actually has a
match. Odii's database is attraction-specific (경복궁, 남산타워, etc.), so it
won't have anything for generic topics (eSIM, T-money, tax refund) — those
fall through to Pexels exactly as before. Mirrors the same Pexels safety
logic used for the SNS pipeline (lib/ingestion/pexels_image.js):

  - the actual search sent to Pexels always has " south korea" appended,
    regardless of what image_query says
  - a result is only accepted if Pexels' own photo description (alt)
    mentions korea/korean/seoul/incheon/busan/hanok
  - falls back to relevance-only matching (still Korea-query-biased) only
    if no alt-confirmed photo exists after checking multiple pages
  - requires PEXELS_API_KEY; skips (leaves image_query as-is) if missing

Also resolves inline `{{gallery:some search query}}` tokens anywhere in a
post's body into extra in-article photos (up to a few per post, wherever
the writer placed a token next to a concrete visual moment — a sticker, a
counter, a specific object). Each token is held to the *stricter* standard:
Korea-confirmed only, no relevance-only fallback. A query with no confirmed
match gets removed rather than filled with a guess — precision over count.

Usage:
  PEXELS_API_KEY=... python3 fetch_images.py
"""
import json
import os
import re
import time
import urllib.request
import urllib.parse

from watermark import apply_watermark
from github_host import upload_media_file

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS_SRC = os.path.join(BASE, "content", "posts")
SEARCH_URL = "https://api.pexels.com/v1/search"
# 2026-10-13 사용자 결정(2번): 한 단계 낮춤 — 화면 표시 크기보다 충분히 큼. 이보다 더 낮추지 말 것.
MIN_ORIGINAL_WIDTH = 2000
KOREA_SIGNAL = re.compile(r"korea|korean|seoul|incheon|busan|hanok", re.IGNORECASE)
# north korea/pyongyang은 "korea"를 포함해 위 검사를 통과하므로 따로 막는다.
OTHER_COUNTRY = re.compile(
    r"\b(north korea|pyongyang|china|chinese|japan|japanese|tokyo|osaka|kyoto|india|thailand|"
    r"vietnam|taiwan|hong kong|singapore|philippines|usa|america)\b", re.IGNORECASE)

# 2026-10-12 사용자 하드룰: "SNS고 블로그고 키워드에 맞는 정확한 이미지, 확인을 하고 올려",
# "화질 낮은 건 사용금지", "중복 이미지도 안 됨". SNS 파이프라인(desktop-tutorial/lib/ingestion/
# media_guard.js)과 같은 기준이다:
#  - 사진을 Groq 무료 비전 모델이 직접 보고 한국/주제 일치를 판정한다. 확인 못 하면 안 쓴다.
#    화질은 MIN_ORIGINAL_WIDTH(2000px)로 숫자로 거른다 — 모델에 화질을 물으면 멀쩡한 사진을 오판했다.
#  - 중복은 Pexels 사진 고유번호로 판정하고 used_media.json에 영구 기록한다. 예전엔 기존 글의
#    image: 값(워터마크 호스팅 주소)만 모아서 원본 사진과 비교가 안 돼 실행마다 중복이 가능했다.
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
USED_MEDIA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "used_media.json")
MAX_VISION_CHECKS_PER_QUERY = 4
# 사용자 하드룰 "화질 낮은 건 사용금지" — 관광공사 사진엔 해상도 하한이 없었다. SNS(media_guard.js)와 같은 값.
MIN_TOURAPI_WIDTH = 1080


def image_width(url):
    import io
    from PIL import Image
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; LandInKoreaBlogBot/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return Image.open(io.BytesIO(res.read())).size[0]


def media_key(url):
    m = re.search(r"pexels\.com/photos/(\d+)", url or "")
    return f"pexels:{m.group(1)}" if m else (url or "").split("?")[0]


def load_used_media():
    try:
        with open(USED_MEDIA_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def save_used_media(keys):
    with open(USED_MEDIA_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(keys), f, ensure_ascii=False, indent=1)


VISION_PROMPT = """You check photos for "Land in Korea", an English blog about SOUTH KOREA.
The article is: "{topic}"

This is ONE single photo. Answer strictly:
1. korea: Is it in South Korea? If it looks like another country, answer false.
2. topic: Does it clearly show the subject of the article (not just a generic city/street/scenery shot)? A season, place, or object that contradicts the article is false.

Respond ONLY with JSON: {{"korea": true|false, "topic": true|false, "what": "one short sentence of what the photo shows"}} /no_think"""


def verify_image(image_url, topic):
    """사진이 topic에 맞는 한국 사진이면 True. 확인 못 하면 False(안 쓴다)."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("  !! GROQ_API_KEY 없음 — 사진을 확인할 수 없어 쓰지 않습니다.")
        return False
    body = json.dumps({
        "model": VISION_MODEL,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": VISION_PROMPT.format(topic=topic)},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]}],
    }).encode("utf-8")
    # ponytail: 무료 등급은 분당 입력 7,000토큰(사진 1장 ≈ 2,200) — 한도에 걸리면 안내된 시간만큼 쉬고 재시도.
    for attempt in range(5):
        req = urllib.request.Request(GROQ_URL, data=body, headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; LandInKoreaBlogBot/1.0)"})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                text = json.loads(res.read().decode("utf-8"))["choices"][0]["message"]["content"]
            verdict = json.loads(re.sub(r"<think>[\s\S]*?</think>", "", text).strip())
            ok = verdict.get("korea") is True and verdict.get("topic") is True
            print(f"  vision {'PASS' if ok else 'FAIL'} korea={verdict.get('korea')} topic={verdict.get('topic')} "
                  f"— {verdict.get('what')}")
            return ok
        except urllib.error.HTTPError as err:
            msg = err.read().decode("utf-8", "ignore")
            wait = re.search(r"try again in ([\d.]+)s", msg)
            if err.code == 429 and wait and attempt < 4:
                time.sleep(min(float(wait.group(1)) + 1, 60))
                continue
            print(f"  !! 사진 확인 실패 → 쓰지 않음: {err.code} {msg[:200]}")
            return False
        except Exception as err:
            print(f"  !! 사진 확인 실패 → 쓰지 않음: {err}")
            return False
    return False

ODII_URL = "https://apis.data.go.kr/B551011/Odii/themeSearchList"


def normalize_tourapi_key(value):
    """data.go.kr의 일반 인증키는 포털에 Encoding(퍼센트 인코딩)으로 표시된다 —
    그대로 쓰면 urllib이 다시 인코딩해 이중 인코딩 오류가 난다. % 포함 시 한 번 decode."""
    key = (value or "").strip()
    if not key:
        return key
    if "%" in key:
        try:
            return urllib.parse.unquote(key)
        except Exception:
            return key
    return key


def find_tourapi_image(keyword):
    """한국관광공사 Odii 오디오가이드 API에서 keyword로 검색해 실제 imageUrl이
    있는 첫 결과를 반환한다. 이 API는 특정 관광지(경복궁 등) DB라 일반적인
    주제(esim, 세금환급 등)에는 매칭이 없는 게 정상이며, 그 경우 None을 반환해
    호출부가 Pexels로 자연스럽게 넘어가게 한다. 억지로 무관한 결과를 쓰지 않는다."""
    api_key = normalize_tourapi_key(os.environ.get("TOUR_AUDIO_GUIDE_API_KEY"))
    if not api_key:
        return None
    params = urllib.parse.urlencode({
        "serviceKey": api_key,
        "MobileOS": "ETC",
        "MobileApp": "LandInKoreaBlog",
        "_type": "json",
        "numOfRows": 10,
        "pageNo": 1,
        "keyword": keyword,
        "langCode": "ko",
    })
    req = urllib.request.Request(
        f"{ODII_URL}?{params}",
        headers={"User-Agent": "Mozilla/5.0 (compatible; LandInKoreaBlogBot/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
    except Exception as err:
        print(f"  !! TourAPI Odii 검색 실패(\"{keyword}\"): {err}")
        return None
    items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
    if isinstance(items, dict):
        items = [items]
    for item in items:
        url = item.get("imageUrl")
        if url:
            print(f"  -> TourAPI 실제 이미지 확보(\"{keyword}\", {item.get('title', '')}): {url}")
            return url
    return None


def search_pexels(api_key, query, page):
    params = urllib.parse.urlencode({"query": query, "per_page": 6, "page": page, "orientation": "landscape"})
    # Pexels rejects Python's default "Python-urllib/x.y" User-Agent with a 403,
    # so a normal browser-ish UA is required alongside the Authorization header.
    headers = {
        "Authorization": api_key,
        "User-Agent": "Mozilla/5.0 (compatible; LandInKoreaBlogBot/1.0)",
    }
    req = urllib.request.Request(f"{SEARCH_URL}?{params}", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as res:
        data = json.loads(res.read().decode("utf-8"))
    return data.get("photos", [])


# 2026-10-08: "relevance-only" 완화 재시도(한국 확인 없이 관련성만으로 채택)를 완전히
# 제거했다 — 두 호출부(커버 이미지, 갤러리 인라인 이미지) 모두 Korea-확인 없이는
# 이미지를 쓰지 않아야 하는데, 이 완화 경로가 바로 다른 나라 사진이 새어 들어오던
# 통로였다(사용자 실측 지적). 이제 Korea-확인 사진을 못 찾으면 항상 None — 호출부는
# 이미지 없이 텍스트만 발행한다(SNS 파이프라인의 findKoreaPhoto와 동일한 원칙).
def find_korea_photo(api_key, raw_query, used_urls, strict=True, topic=None):
    query = f"{raw_query} south korea"
    vision_checks = 0

    for page in range(1, 6):
        photos = search_pexels(api_key, query, page)
        if not photos:
            break
        for photo in photos:
            if photo["width"] < MIN_ORIGINAL_WIDTH:
                continue
            url = photo["src"]["large2x"]
            if media_key(url) in used_urls:
                continue
            alt = photo.get("alt") or ""
            if not (KOREA_SIGNAL.search(alt) or KOREA_SIGNAL.search(photo.get("url", ""))):
                continue
            if OTHER_COUNTRY.search(f"{alt} {photo.get('url', '').replace('-', ' ')}"):
                continue
            if vision_checks >= MAX_VISION_CHECKS_PER_QUERY:
                print(f"  !! \"{raw_query}\": 확인 {MAX_VISION_CHECKS_PER_QUERY}장 모두 탈락 — 사진 없이 진행")
                return None
            vision_checks += 1
            if not verify_image(photo["src"]["large"], topic or raw_query):
                continue
            print(f"  -> found (Korea-confirmed, {photo['width']}px, alt=\"{alt}\"): {photo['url']}")
            return url
    return None


def watermark_and_host(original_url, tag):
    """원본 이미지 URL에 브랜드 로고를 합성해 media-assets 브랜치에 올리고 그 새
    URL을 반환한다. GITHUB_TOKEN이 없거나 합성/호스팅이 실패하면 원본 URL을
    그대로 반환한다 — 워터마크 없이 쓰는 게 아예 이미지 없이 발행하는 것보다
    낫다는 SNS 파이프라인과 동일한 원칙(desktop-tutorial/lib/media/watermark_images.js)."""
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        print("  !! GITHUB_TOKEN이 없어 워터마크 합성을 건너뜁니다(원본 이미지 그대로 사용).")
        return original_url
    buf = apply_watermark(original_url)
    if not buf:
        return original_url
    try:
        return upload_media_file(github_token, buf, f"images/{tag}-{int(time.time() * 1000)}.jpg")
    except Exception as err:
        print(f"  !! 워터마크 이미지 호스팅 실패, 원본 사용: {err}")
        return original_url


GALLERY_TOKEN = re.compile(r"[ \t]*\{\{gallery:(.*?)\}\}[ \t]*\n?")


def resolve_gallery_tokens(api_key, body, used_urls, title):
    """Replaces each {{gallery:query}} token with a real image, or removes
    the token entirely if no Korea-confirmed match exists for that query."""
    changed = False

    def replace_one(match):
        nonlocal changed
        query = match.group(1).strip()
        print(f"  gallery: searching \"{query}\"")
        try:
            url = find_korea_photo(api_key, query, used_urls, strict=True, topic=f"{title} (photo of: {query})")
        except Exception as err:
            print(f"  !! gallery search failed for \"{query}\": {err}")
            return ""
        changed = True
        if not url:
            print(f"  !! no Korea-confirmed match for \"{query}\" — dropping this image, not guessing")
            return ""
        used_urls.add(media_key(url))  # 중복 체크는 원본 사진 고유번호 기준 — 워터마크 자산 URL은 매번 새로 생겨 의미가 없음
        hosted_url = watermark_and_host(url, re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-") or "gallery")
        print(f"  gallery: resolved \"{query}\"")
        return f"![{query}]({hosted_url})\n\n"

    new_body = GALLERY_TOKEN.sub(replace_one, body)
    return new_body, changed


def parse_front_matter(text):
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip()
            body = text[end + 4:]
            for line in block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
    return meta, body


def main():
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        print("PEXELS_API_KEY not set — skipping image fetch.")
        return

    # Dedupe within this run so two different posts don't end up with the
    # identical photo (already-used image: values from previous runs also
    # count, so re-running doesn't collide with posts fetched earlier).
    used_urls = load_used_media()
    for fn in sorted(os.listdir(POSTS_SRC)):
        if fn.endswith(".md"):
            with open(os.path.join(POSTS_SRC, fn), encoding="utf-8") as f:
                existing_meta, _ = parse_front_matter(f.read())
            if existing_meta.get("image"):
                used_urls.add(media_key(existing_meta["image"]))

    for fn in sorted(os.listdir(POSTS_SRC)):
        if not fn.endswith(".md"):
            continue
        path = os.path.join(POSTS_SRC, fn)
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        meta, body = parse_front_matter(raw)
        front_matter_changed = False

        if "image" not in meta and "image_query" in meta:
            print(f"{fn}: searching \"{meta['image_query']}\"")
            url = None
            try:
                url = find_tourapi_image(meta["image_query"])
            except Exception as err:
                print(f"  !! TourAPI search failed for {fn}: {err}")
            title = meta.get("title", meta["image_query"])
            if url and media_key(url) in used_urls:
                url = None  # 다른 글이 이미 쓴 관광공사 이미지면 중복 방지 원칙대로 건너뜀
            if url:
                try:
                    width = image_width(url)
                except Exception as err:
                    print(f"  !! 관광공사 사진 크기 확인 실패 → 쓰지 않음: {err}")
                    width = 0
                if width < MIN_TOURAPI_WIDTH:
                    print(f"  !! 관광공사 사진 저화질({width}px) → 쓰지 않음")
                    url = None
            if url and not verify_image(url, title):
                url = None
            if not url:
                try:
                    # 2026-10-08 수정: strict=True 없이 호출하면 find_korea_photo가
                    # Korea-확인 사진을 5페이지까지 다 뒤져도 못 찾을 때 "관련성만으로"
                    # 채택하는 완화 재시도로 넘어간다 — 이게 바로 외국 사진이 블로그
                    # 커버 이미지로 새어 들어오던 경로였다(사용자 실측 지적). 갤러리
                    # 인라인 이미지(strict=True)와 동일하게 Korea-확인 없으면 이미지
                    # 없이 글만 발행하도록 강제한다.
                    url = find_korea_photo(api_key, meta["image_query"], used_urls, strict=True, topic=title)
                except Exception as err:  # one post's failure shouldn't block the rest
                    print(f"  !! search failed for {fn}: {err}")
                    url = None
            if url:
                used_urls.add(media_key(url))  # 중복 체크는 원본 사진 고유번호 기준 — 워터마크 자산 URL은 매번 새로 생겨 의미가 없음
                hosted_url = watermark_and_host(url, fn.replace(".md", ""))
                end = raw.find("\n---", 3)
                raw = f"{raw[:end]}\nimage: {hosted_url}{raw[end:]}"
                front_matter_changed = True
                print(f"  wrote image: to {fn}")
            else:
                print(f"  !! no Korea-confirmed image found for {fn}, leaving as text-only for now")

        body_changed = False
        if GALLERY_TOKEN.search(body):
            print(f"{fn}: resolving inline {{{{gallery:...}}}} tokens")
            new_body, body_changed = resolve_gallery_tokens(api_key, body, used_urls, meta.get("title", fn))
            if body_changed:
                # raw may have just gained an `image:` line above, so re-split it fresh
                end = raw.find("\n---", 3)
                raw = raw[: end + 4] + "\n\n" + new_body.lstrip("\n")

        if front_matter_changed or body_changed:
            with open(path, "w", encoding="utf-8") as f:
                f.write(raw)
            save_used_media(used_urls)


if __name__ == "__main__":
    main()
