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
import urllib.request
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS_SRC = os.path.join(BASE, "content", "posts")
SEARCH_URL = "https://api.pexels.com/v1/search"
MIN_ORIGINAL_WIDTH = 3000
KOREA_SIGNAL = re.compile(r"korea|korean|seoul|incheon|busan|hanok", re.IGNORECASE)

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


def find_korea_photo(api_key, raw_query, used_urls, strict=False):
    query = f"{raw_query} south korea"

    passes = (True,) if strict else (True, False)
    for require_alt_match in passes:
        for page in range(1, 6):
            photos = search_pexels(api_key, query, page)
            if not photos:
                break
            for photo in photos:
                if photo["width"] < MIN_ORIGINAL_WIDTH:
                    continue
                url = photo["src"]["large2x"]
                if url in used_urls:
                    continue
                alt = photo.get("alt") or ""
                if require_alt_match and not (KOREA_SIGNAL.search(alt) or KOREA_SIGNAL.search(photo.get("url", ""))):
                    continue
                print(f"  -> found ({'Korea-confirmed' if require_alt_match else 'relevance-only'}, "
                      f"{photo['width']}px, alt=\"{alt}\"): {photo['url']}")
                return url
    return None


GALLERY_TOKEN = re.compile(r"[ \t]*\{\{gallery:(.*?)\}\}[ \t]*\n?")


def resolve_gallery_tokens(api_key, body, used_urls):
    """Replaces each {{gallery:query}} token with a real image, or removes
    the token entirely if no Korea-confirmed match exists for that query."""
    changed = False

    def replace_one(match):
        nonlocal changed
        query = match.group(1).strip()
        print(f"  gallery: searching \"{query}\"")
        try:
            url = find_korea_photo(api_key, query, used_urls, strict=True)
        except Exception as err:
            print(f"  !! gallery search failed for \"{query}\": {err}")
            return ""
        changed = True
        if not url:
            print(f"  !! no Korea-confirmed match for \"{query}\" — dropping this image, not guessing")
            return ""
        used_urls.add(url)
        print(f"  gallery: resolved \"{query}\"")
        return f"![{query}]({url})\n\n"

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
    used_urls = set()
    for fn in sorted(os.listdir(POSTS_SRC)):
        if fn.endswith(".md"):
            with open(os.path.join(POSTS_SRC, fn), encoding="utf-8") as f:
                existing_meta, _ = parse_front_matter(f.read())
            if existing_meta.get("image"):
                used_urls.add(existing_meta["image"])

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
            if url and url in used_urls:
                url = None  # 다른 글이 이미 쓴 관광공사 이미지면 중복 방지 원칙대로 건너뜀
            if not url:
                try:
                    url = find_korea_photo(api_key, meta["image_query"], used_urls)
                except Exception as err:  # one post's failure shouldn't block the rest
                    print(f"  !! search failed for {fn}: {err}")
                    url = None
            if url:
                used_urls.add(url)
                end = raw.find("\n---", 3)
                raw = f"{raw[:end]}\nimage: {url}{raw[end:]}"
                front_matter_changed = True
                print(f"  wrote image: to {fn}")
            else:
                print(f"  !! no Korea-confirmed image found for {fn}, leaving as text-only for now")

        body_changed = False
        if GALLERY_TOKEN.search(body):
            print(f"{fn}: resolving inline {{{{gallery:...}}}} tokens")
            new_body, body_changed = resolve_gallery_tokens(api_key, body, used_urls)
            if body_changed:
                # raw may have just gained an `image:` line above, so re-split it fresh
                end = raw.find("\n---", 3)
                raw = raw[: end + 4] + "\n\n" + new_body.lstrip("\n")

        if front_matter_changed or body_changed:
            with open(path, "w", encoding="utf-8") as f:
                f.write(raw)


if __name__ == "__main__":
    main()
