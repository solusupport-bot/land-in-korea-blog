"""Render the approved Land in Korea homepage inside the existing stdlib build.

Only the homepage owns these styles. Article generation, collections, RSS,
canonical URLs, review decisions and deployment continue to use build.py.
"""
import copy
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
from datetime import date
from urllib.parse import quote

SOURCE = Path(__file__).with_name("homepage")
CATEGORIES = {"before-you-go": "before", "getting-around": "transport",
              "sim-and-apps": "apps", "money-and-shopping": "money",
              "food-and-culture": "food", "things-to-do": "things",
              "living-in-korea": "living"}
REGIONS = {"seoul", "busan", "daegu", "incheon", "gwangju", "daejeon",
           "ulsan", "sejong", "gyeonggi", "gangwon", "chungbuk", "chungnam",
           "jeonbuk", "jeonnam", "gyeongbuk", "gyeongnam", "jeju", "jinju",
           "islands", "common"}
PLACE_RULES = [
    (r"ulleung|dokdo|울릉|독도", ["islands", "gyeongbuk"]),
    (r"jinju|namgang|진주", ["jinju", "gyeongnam"]),
    (r"nami.island|남이섬", ["gangwon", "gyeonggi"]),
    (r"suwon|hwaseong|gapyeong|paju|everland|dmz|수원|가평|파주", ["gyeonggi"]),
    (r"busan|haeundae|gamcheon|gwangalli|부산|해운대|감천|광안리", ["busan"]),
    (r"jeju|seogwipo|seongsan|제주|서귀포|성산", ["jeju"]),
    (r"gyeongbokgung|bukchon|insadong|hongdae|myeongdong|namdaemun|namsan|경복궁|북촌|인사동", ["seoul"]),
]
# Exact named landmarks, not broad words such as "station" or "market".
AREA_RULES = [
    (r"gyeongbokgung|bukchon|insadong|경복궁|북촌|인사동", "seoul", "Jongno-gu"),
    (r"myeongdong|namdaemun|명동|남대문", "seoul", "Jung-gu"),
    (r"hongdae|홍대", "seoul", "Mapo-gu"),
    (r"n.seoul.tower|namsan|남산", "seoul", "Yongsan-gu"),
    (r"haeundae|해운대", "busan", "Haeundae-gu"),
    (r"gamcheon|감천", "busan", "Saha-gu"),
    (r"gwangalli|광안리", "busan", "Suyeong-gu"),
]


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _regions(post, seed):
    explicit = post.get("map_regions", post.get("regions"))
    if explicit:
        values = explicit.split(",") if isinstance(explicit, str) else explicit
        aliases = {"gyeonggi-do": "gyeonggi", "gangwon-do": "gangwon",
                   "chungcheongbuk-do": "chungbuk", "chungcheongnam-do": "chungnam",
                   "jeollabuk-do": "jeonbuk", "jeollanam-do": "jeonnam",
                   "gyeongsangbuk-do": "gyeongbuk", "gyeongsangnam-do": "gyeongnam",
                   "jeju-do": "jeju"}
        values = [v.lower().strip("[]\"' ") for v in values]
        values = [aliases.get(v, v) for v in values]
        if "map_regions" in post and not set(values) <= REGIONS:
            raise ValueError("Unknown map region for " + post["slug"])
        values = [v for v in values if v in REGIONS]
        if values:
            return list(dict.fromkeys(values))
    if seed:
        return list(seed["regions"])
    text = post["slug"] + " " + post["title"]
    for pattern, values in PLACE_RULES:
        if re.search(pattern, text, re.I):
            return values[:]
    # Generic national advice remains searchable, without assigning a city
    # merely because its name occurs as a departure point in the title.
    return ["common"]


def _areas(post):
    value = post.get("map_areas", "")
    if not value:
        return []
    if isinstance(value, str):
        return [tuple(v.strip().split(":", 1)) for v in value.split(",") if v.strip()]
    return [(v["map"], v["area"]) for v in value]


def build_index(posts, base):
    seeds = json.loads((SOURCE / "blog-index-seed.json").read_text(encoding="utf-8"))
    seeds = {p["url"].rsplit("/", 1)[-1]: p for p in seeds}
    maps = json.loads((SOURCE / "district-data-seed.json").read_text(encoding="utf-8"))
    maps = copy.deepcopy(maps)
    published = {p["slug"] for p in posts}
    # Deleted/unpublished guides must not survive in district associations.
    for atlas in maps.values():
        for area in atlas["paths"] + atlas.get("insets", []):
            area["slugs"] = [s for s in area.get("slugs", []) if s in published]
    revisions = json.loads((SOURCE / "photo-revisions.json").read_text(encoding="utf-8"))
    index = []
    for post in sorted(posts, key=lambda p: (p["date"], p["slug"]), reverse=True):
        slug = post["slug"]
        seed = seeds.get(slug)
        regions = _regions(post, seed)
        areas = _areas(post)
        for pattern, region, area in AREA_RULES:
            if region in regions and re.search(pattern, slug + " " + post["title"], re.I):
                areas.append((region, area))
        for pair in areas:
            if len(pair) != 2:
                raise ValueError("Use map_areas: region:district for " + slug)
            key, name = pair
            atlas = maps.get(key)
            target = next((a for a in atlas["paths"] if a["name"] == name), None) if atlas else None
            if not target:
                raise ValueError("Unknown map area " + key + ":" + name + " for " + slug)
            target["slugs"] = list(dict.fromkeys(target.get("slugs", []) + [slug]))
            parent = next((r for r in REGIONS if r in maps and
                           any(a.get("child") == key for a in maps[r]["paths"])), key)
            if parent in REGIONS and parent not in regions:
                regions.append(parent)
            # Keep parent district selectable when a dong has been assigned.
            if parent != key:
                for a in maps[parent]["paths"]:
                    if a.get("child") == key:
                        a["slugs"] = list(dict.fromkeys(a.get("slugs", []) + [slug]))
        item = {"title": post["title"], "url": base + "/posts/" + quote(slug, safe="-_"),
                "category": CATEGORIES.get(post.get("_topic"), "before"),
                "regions": regions, "publishedAt": post["date"]}
        image = post.get("image", "").replace(
            "https://raw.githubusercontent.com/solusupport-bot/land-in-korea-blog/media-assets/images/",
            base + "/media/images/")
        revision = revisions.get(slug)
        if revision and image in revision.get("replaces", []):
            image = base + "/home-ui/assets/" + revision["file"] if revision.get("file") else ""
            if image:
                item.update(imageAlt=revision["alt"], imageCreditUrl=base + "/#credits-dialog")
                if revision.get("small"):
                    item["imageSrcset"] = base + "/home-ui/assets/" + revision["small"] + " 1024w, " + image + " " + str(revision["width"]) + "w"
        if image:
            photo = post.get("_photo", {})
            item.update(image=image)
            item.setdefault("imageAlt", photo.get("alt", post["title"]))
            item.setdefault("imageCreditUrl", base + "/photo-credits")
        index.append(item)
    if len(index) != len(posts) or len({p["url"] for p in index}) != len(posts):
        raise ValueError("Duplicate or missing article in homepage index")
    return index, maps


def write_homepage(cfg, base, posts, metadata_html, site):
    """Reuse the production head; replace only the homepage presentation."""
    site = Path(site)
    output = site / "home-ui"
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE / "assets", output / "assets", dirs_exist_ok=True)
    index, maps = build_index(posts, base)
    prefix = base + "/home-ui/"
    for name in ("styles.css", "app.js", "explore.js", "editorial.js"):
        data = (SOURCE / name).read_text(encoding="utf-8")
        if name.endswith(".js"):
            data = data.replace("'assets/", "'" + prefix + "assets/")
            data = data.replace("https://landinkorea.com", base)
        (output / name).write_text(data, encoding="utf-8")
    (output / "blog-index.js").write_text("window.LIK_BLOG_INDEX=" + _json(index) + ";", encoding="utf-8")
    (output / "district-data.js").write_text("window.LIK_DISTRICTS=" + _json(maps) + ";", encoding="utf-8")
    # All generated files participate, including data; cache invalidates on publish.
    digest = hashlib.sha256()
    for filename in ("styles.css", "app.js", "explore.js", "editorial.js", "blog-index.js", "district-data.js"):
        digest.update((output / filename).read_bytes())
    version = digest.hexdigest()[:12]
    template = (SOURCE / "index.html").read_text(encoding="utf-8")
    body = re.search(r"<body>(.*)</body>", template, re.S).group(1)
    body = body.replace("https://landinkorea.com", base)
    body = re.sub(r'(src|href)="(assets/[^\"]+)"', lambda m: m[1] + '="' + prefix + m[2] + '"', body)
    body = body.replace('src="editorial.js"', 'src="' + prefix + 'editorial.js?v=' + version + '"')
    # True, build-time metadata and ordinary links remain available without JS.
    fallback = '<noscript><section class="content-width"><h2>All travel guides</h2><ul>'
    fallback += ''.join('<li><a href="' + html.escape(p["url"], quote=True) + '">' + html.escape(p["title"]) + '</a></li>' for p in index)
    fallback += '</ul></section></noscript>'
    body = body.replace('</main>', fallback + '</main>')
    body = re.sub(r'© 2026 Land in Korea', '© ' + str(date.today().year) + ' Land in Korea', body)
    # Keep the original canonical/JSON-LD/analytics/ads configuration. Do not
    # include the old global stylesheet: it owns conflicting header/map classes.
    head = re.search(r"<head>(.*)</head>", metadata_html, re.S).group(1)
    head = re.sub(r'<link[^>]+(?:style\.css|fonts\.googleapis\.com|fonts\.gstatic\.com)[^>]*>\s*', '', head)
    head += '\n<meta name="theme-color" content="#153e55">'
    head += '<link rel="icon" href="' + prefix + 'assets/logo-original.jpg">'
    head += '<link rel="preload" as="image" href="' + prefix + 'assets/busan.jpg">'
    head += '<link rel="stylesheet" href="' + prefix + 'styles.css?v=' + version + '">'
    for name in ("blog-index", "app", "district-data", "explore"):
        head += '<script src="' + prefix + name + '.js?v=' + version + '" defer></script>'
    home = '<!doctype html>\n<html lang="en"><head>' + head + '</head><body>' + body + '</body></html>'
    (site / 'index.html').write_text(home, encoding='utf-8')
    (output / 'build-info.json').write_text(_json({"version": version, "posts": len(index),
        "districtMaps": len(maps), "withPhotos": sum(bool(p.get('image')) for p in index)}), encoding='utf-8')
