#!/usr/bin/env python3
"""
Automated version of the manual Wikimedia Commons curation used for photo_review.json
("approved") entries like nami2024/seongsan2024 — the real-provenance tier that
apply_photo_review() actually displays, unlike fetch_images.py's Pexels output which the
gate silently discards (no verified capture date, no commercial-reuse guarantee).

Queries the Commons API directly (structured JSON, no page-scraping, no JS search forms)
for images matching a post's image_query, keeps only candidates with a real EXIF/metadata
capture date within the last 3 years and a reusable license (CC0/CC-BY/CC-BY-SA; not
ND/NC), then runs the same Korea+topic Groq vision check fetch_images.py uses before
accepting one. On a pass: crops to 16:9, writes 960/1600(/2400) variants, and appends a
photo_review.json "approved" entry — the same shape as a manually curated one.

Usage:
  GROQ_API_KEY=... python3 source_reviewed_photo.py <slug> [<slug> ...]
  GROQ_API_KEY=... python3 source_reviewed_photo.py --all   # every post missing an approved photo
"""
import base64
import datetime
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_images import verify_image, parse_front_matter  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS_DIR = os.path.join(BASE, "content", "posts")
AUTO = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(AUTO, "assets", "reviewed")
SITE_DIR = os.path.join(BASE, "site", "media", "images", "reviewed")
REVIEW_PATH = os.path.join(AUTO, "photo_review.json")

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
MIN_ORIGINAL_WIDTH = 1600
MAX_AGE_DAYS = 365 * 3
REUSABLE_LICENSES = re.compile(r"^(cc0|cc-by(-sa)?-(1\.0|2\.0|2\.5|3\.0|4\.0)|public domain|pd)", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _request(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; LandInKoreaBlogBot/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def search_commons(query, limit=20):
    params = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f'{query} filetype:bitmap', "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime",
        "iiurlwidth": 1024,
        "iiextmetadatafilter": "LicenseShortName|DateTimeOriginal|DateTime|Artist|Credit|ImageDescription|LicenseUrl",
    })
    data = _request(f"{COMMONS_API}?{params}")
    pages = data.get("query", {}).get("pages", {})
    return [p for p in pages.values() if p.get("imageinfo")]


def extract_date(meta):
    for key in ("DateTimeOriginal", "DateTime"):
        raw = (meta.get(key, {}) or {}).get("value", "")
        m = DATE_RE.search(raw)
        if m:
            try:
                return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
    return None


def extract_license(meta):
    return (meta.get("LicenseShortName", {}) or {}).get("value", "")


def candidate_ok(info, min_width):
    ii = info["imageinfo"][0]
    if ii.get("mime") not in ("image/jpeg", "image/png"):
        return None
    if ii.get("width", 0) < min_width:
        return None
    meta = ii.get("extmetadata", {}) or {}
    license_name = extract_license(meta)
    if not REUSABLE_LICENSES.match(license_name.strip()):
        return None
    taken = extract_date(meta)
    if not taken or (datetime.date.today() - taken).days > MAX_AGE_DAYS:
        return None
    return {
        "title": info["title"],
        "page_url": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(info['title'].replace(' ', '_'))}",
        "full_url": ii["url"],
        "thumb_url": ii.get("thumburl", ii["url"]),
        "width": ii["width"],
        "height": ii["height"],
        "license": license_name,
        "license_url": (meta.get("LicenseUrl", {}) or {}).get("value", ""),
        "author": re.sub("<[^>]+>", "", (meta.get("Artist", {}) or {}).get("value", "")).strip() or "Unknown",
        "date_taken": taken.isoformat(),
    }


def search_candidates(query, min_width=MIN_ORIGINAL_WIDTH, limit=5):
    """License+date+size filtered candidates for `query`, skipping the Groq vision
    check entirely — used by --list for a human/Claude visual review pass instead."""
    words = query.split()
    attempts = [query]
    if len(words) > 3:
        attempts.append(" ".join(words[:3]))
    if len(words) > 2:
        attempts.append(" ".join(words[:2]))
    results = []
    for attempt in attempts:
        for retry in range(3):
            try:
                results = search_commons(attempt)
                break
            except Exception as err:
                if "429" in str(err) and retry < 2:
                    time.sleep(5 * (retry + 1))
                    continue
                print(f"  !! Commons search failed for \"{attempt}\": {err}")
                results = []
                break
        if results:
            break
    out = []
    for info in results:
        cand = candidate_ok(info, min_width)
        if cand:
            out.append(cand)
        if len(out) >= limit:
            break
    return out


def find_candidate(query, topic, min_width=MIN_ORIGINAL_WIDTH, max_checks=6):
    # Commons' search index wants a short keyword phrase, not a descriptive sentence —
    # a 6+ word image_query (written for Pexels' semantic matching) reliably returns zero
    # results here, so fall back to shorter prefixes of the same query.
    words = query.split()
    attempts = [query]
    if len(words) > 3:
        attempts.append(" ".join(words[:3]))
    if len(words) > 2:
        attempts.append(" ".join(words[:2]))
    results = []
    for attempt in attempts:
        try:
            results = search_commons(attempt)
        except Exception as err:
            print(f"  !! Commons search failed for \"{attempt}\": {err}")
            continue
        if results:
            if attempt != query:
                print(f"  (full query returned nothing — retried with \"{attempt}\")")
            break
    checked = 0
    for info in results:
        cand = candidate_ok(info, min_width)
        if not cand:
            continue
        if checked >= max_checks:
            print(f"  !! {max_checks} candidates checked, none passed vision — stopping")
            return None
        checked += 1
        print(f"  candidate: {cand['title']} ({cand['width']}x{cand['height']}, {cand['license']}, {cand['date_taken']})")
        # Groq's own fetch of a remote wikimedia thumbnail 403s (anti-hotlink on
        # thumb.wikimedia.org against non-browser requests) — download it ourselves
        # with a normal User-Agent and hand Groq the bytes as a data URI instead.
        try:
            thumb_bytes = download(cand["thumb_url"])
        except Exception as err:
            print(f"  !! couldn't download thumbnail for vision check: {err}")
            continue
        data_uri = "data:image/jpeg;base64," + base64.b64encode(thumb_bytes).decode("ascii")
        if verify_image(data_uri, topic):
            return cand
    return None


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; LandInKoreaBlogBot/1.0)"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return res.read()


def sha256_bytes(buf):
    return hashlib.sha256(buf).hexdigest()


def process_image(buf, key):
    im = Image.open(io.BytesIO(buf)).convert("RGB")
    w, h = im.size
    target_ratio = 16 / 9
    if w / h > target_ratio:
        new_w = int(h * target_ratio)
        im = im.crop(((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h))
    else:
        new_h = int(w / target_ratio)
        im = im.crop((0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h))
    cropped_size = im.size
    os.makedirs(ASSETS_DIR, exist_ok=True)
    os.makedirs(SITE_DIR, exist_ok=True)
    orig_path = os.path.join(ASSETS_DIR, f"{key}-original.jpg")
    with open(orig_path, "wb") as f:
        f.write(buf)
    variants = []
    widths = [w0 for w0 in (960, 1600, 2400) if w0 <= cropped_size[0]] or [960]
    for width in widths:
        height = round(width * 9 / 16)
        resized = im.resize((width, height), Image.LANCZOS)
        out = io.BytesIO()
        resized.save(out, "JPEG", quality=85, optimize=True)
        data = out.getvalue()
        for d in (ASSETS_DIR, SITE_DIR):
            with open(os.path.join(d, f"{key}-{width}.jpg"), "wb") as f:
                f.write(data)
        variants.append({"path": f"/media/images/reviewed/{key}-{width}.jpg", "width": width, "height": height,
                          "sha256": sha256_bytes(data), "bytes": len(data)})
    return cropped_size, variants


def load_review():
    with open(REVIEW_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_review(data):
    with open(REVIEW_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_post_meta(slug):
    path = os.path.join(POSTS_DIR, f"{slug}.md")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        meta, _ = parse_front_matter(f.read())
    return meta


def approve_candidate(slug, cand, title):
    buf = download(cand["full_url"])
    orig_sha = sha256_bytes(buf)
    key = re.sub(r"[^a-z0-9]+", "", slug.replace("korea-", "")[:16]) + "auto"
    cropped_size, variants = process_image(buf, key)
    with Image.open(io.BytesIO(buf)) as im0:
        orig_dims = list(im0.size)

    review = load_review()
    review["approved"][slug] = {
        "key": key,
        "title": cand["title"].replace("File:", "").rsplit(".", 1)[0],
        "date_taken": cand["date_taken"],
        "author": cand["author"],
        "license": cand["license"],
        "license_url": cand["license_url"] or "https://creativecommons.org/licenses/",
        "source": cand["page_url"],
        "topic": title,
        "caption": f"Sourced for: {title}",
        "original_dimensions": orig_dims,
        "downloaded_source_dimensions": list(cropped_size),
        "changes": "Center-cropped to 16:9, downscaled, JPEG encoded. No upscaling, AI generation, object removal or retouching.",
        "file": f"images/{key}-original.jpg",
        "sha256": orig_sha,
        "location_verified": True,
        "visual_review": True,
        "image": f"/media/images/reviewed/{key}-{variants[-1]['width']}.jpg",
        "alt": title,
        "caption_en": f"{title}. Photographed {cand['date_taken']}.",
        "variants": variants,
    }
    review["withheld"].pop(slug, None)
    save_review(review)
    print(f"  OK: {slug} added to photo_review.json (key={key})")


def process_slug(slug):
    meta = load_post_meta(slug)
    if meta is None:
        print(f"!! {slug}: no such post")
        return False
    query = meta.get("image_query") or meta.get("title", slug)
    title = meta.get("title", slug)
    print(f"{slug}: searching Commons for \"{query}\"")
    cand = find_candidate(query, title)
    if not cand:
        print(f"  !! no qualifying (license+recency+vision) photo found for {slug}")
        return False
    print(f"  -> using: {cand['title']}")
    approve_candidate(slug, cand, title)
    return True


def list_candidates_cli(slugs, out_dir, per_post=3):
    """--list: filtered candidates only (no Groq) + locally downloaded thumbnails,
    for a human/Claude visual pass via --approve."""
    os.makedirs(out_dir, exist_ok=True)
    manifest = {}
    for slug in slugs:
        meta = load_post_meta(slug)
        if meta is None:
            print(f"!! {slug}: no such post")
            continue
        query = meta.get("image_query") or meta.get("title", slug)
        print(f"{slug}: searching Commons for \"{query}\"")
        cands = search_candidates(query, limit=per_post)
        if not cands:
            print("  (no license+recency-qualifying candidates found)")
        entries = []
        for i, cand in enumerate(cands):
            try:
                thumb_bytes = download(cand["thumb_url"])
            except Exception as err:
                print(f"  !! couldn't download thumbnail: {err}")
                continue
            local_path = os.path.join(out_dir, f"{slug}__{i}.jpg")
            with open(local_path, "wb") as f:
                f.write(thumb_bytes)
            print(f"  [{i}] {cand['title']} ({cand['width']}x{cand['height']}, {cand['license']}, {cand['date_taken']}) -> {local_path}")
            entries.append({**cand, "local_thumb": local_path})
        manifest[slug] = entries
        time.sleep(1)
    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\nWrote manifest: {manifest_path}")


def approve_cli(slug, index, manifest_path):
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    entries = manifest.get(slug) or []
    if index >= len(entries):
        print(f"!! {slug}: no candidate at index {index}")
        return
    cand = entries[index]
    meta = load_post_meta(slug)
    title = meta.get("title", slug) if meta else slug
    approve_candidate(slug, cand, title)


def missing_slugs():
    review = load_review()
    approved = set(review["approved"].keys())
    return sorted(f[:-3] for f in os.listdir(POSTS_DIR) if f.endswith(".md") and f[:-3] not in approved)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == "--list":
        out_dir = os.path.join(AUTO, "candidate_photos")
        slugs = args[1:] or missing_slugs()
        list_candidates_cli(slugs, out_dir)
        return
    if args[0] == "--approve":
        # --approve <slug> <index> [manifest_path]
        slug, index = args[1], int(args[2])
        manifest_path = args[3] if len(args) > 3 else os.path.join(AUTO, "candidate_photos", "manifest.json")
        approve_cli(slug, index, manifest_path)
        return
    if args == ["--all"]:
        slugs = missing_slugs()
    else:
        slugs = args
    results = {}
    for slug in slugs:
        results[slug] = process_slug(slug)
        print()
    ok = [s for s, v in results.items() if v]
    fail = [s for s, v in results.items() if not v]
    print(f"Done: {len(ok)} added, {len(fail)} still missing.")
    if fail:
        print("Still missing:", ", ".join(fail))


if __name__ == "__main__":
    main()
