#!/usr/bin/env python3
"""
Fills in a real, high-resolution, Korea-confirmed photo for every post that
has an `image_query` in its front matter but no `image` yet. Mirrors the
same safety logic used for the SNS pipeline (lib/ingestion/pexels_image.js):

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
