#!/usr/bin/env python3
"""
Land in Korea - static blog build engine (Python stdlib only).

What it does:
  1) Reads content/posts/*.md (front matter + markdown)
  2) Converts each post to HTML at site/posts/<slug>.html
  3) Generates home, about, privacy, affiliate-disclosure, contact pages
  4) Generates sitemap.xml, robots.txt, style.css

Affiliate links: write {{klook}}, {{tripcom}}, or {{getyourguide}} anywhere
in a post's markdown and it gets replaced with the real URL from
automation/config.json's affiliate_links at build time. Update that one
file when real tracking links are ready and every post updates at once.

Usage:
  python3 build.py
"""
import json
import os
import re
import html
import urllib.parse
from datetime import datetime, timezone
from email.utils import format_datetime
import xml.etree.ElementTree as ET
from homepage import write_homepage

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO = os.path.join(BASE, "automation")
POSTS_SRC = os.path.join(BASE, "content", "posts")
SITE = os.path.join(BASE, "site")
POSTS_OUT = os.path.join(SITE, "posts")

# 정책 페이지(privacy/terms/disclosure)의 "Last updated" / "As of" 날짜.
# datetime.now()로 매 빌드마다 다시 계산하면 실제로는 문구가 안 바뀌었는데도
# 매일 "방금 수정됨"으로 보여 Google의 "날짜는 실제로 내용이 바뀐 시점이어야
# 한다"는 가이드와 어긋난다 (2026-09-29, 애드센스 재심사 점검 중 발견).
# 정책 문구가 실제로 바뀔 때만 이 값을 수동으로 갱신한다. 2026-09-18은
# "Update privacy/about wording ahead of AdSense application" 커밋 날짜.
POLICY_PAGE_LAST_UPDATED = "2026-09-18"


def load_config():
    with open(os.path.join(AUTO, "config.json"), encoding="utf-8") as f:
        return json.load(f)


# ---------- front matter parser ----------
def parse_front_matter(text):
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip()
            body = text[end + 4:].lstrip("\n")
            for line in block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
    return meta, body


def apply_affiliate_tokens(text, cfg):
    """
    2026-09-02: AdSense 신청을 위해 정보성 콘텐츠로 먼저 심사받기로 결정 —
    승인 전까지 제휴 링크를 사이트 전체에서 끈다(affiliate_links_enabled=false).
    마크다운 소스는 그대로 [Klook's listings]({{klook}}) 형태를 유지하되, 꺼져
    있으면 링크를 걷어내고 순수 텍스트만 남긴다 — 승인 후 config.json 값만
    true로 바꾸면 기존 문구 그대로 다시 링크가 살아난다(본문을 다시 쓸 필요 없음).
    """
    links = cfg.get("affiliate_links", {})
    enabled = cfg.get("affiliate_links_enabled", False)
    for key, url in links.items():
        token = f"{{{{{key}}}}}"
        if enabled:
            text = text.replace(token, url)
        else:
            text = re.sub(r"\[([^\]]+)\]\(" + re.escape(token) + r"\)", r"\1", text)
            text = text.replace(token, "")
    return text


# ---------- minimal markdown -> HTML ----------
AFFILIATE_DOMAINS = ("klook.com", "trip.com", "getyourguide.com")


def link_attrs(href, base_url):
    """
    2026-09-02: 처음엔 본문에 나오는 링크가 전부 제휴 링크였어서 모든 링크에
    rel="sponsored"를 붙여도 문제가 없었다. 이제 글 사이 내부 링크(SEO에 좋은
    관행)를 추가하면서, 내부 링크에까지 "sponsored"를 붙이면 검색엔진에 잘못된
    신호를 주게 된다 — 링크 목적지에 따라 속성을 구분한다.

    2026-09-29: 공식 출처(Korail, 관광공사 등) 인용 링크를 본문에 추가하면서
    이 구분을 한 번 더 좁혔다. "sponsored"는 실제로 금전 관계가 있는 제휴
    링크에만 붙어야 한다(Google 링크 속성 가이드) — 순수 편집상 인용 링크에
    sponsored를 붙이면 실제로는 아무 대가관계가 없는데 있는 것처럼 잘못
    표시하는 셈이라, About 페이지의 "출처로 이어진다"는 약속과도 어긋난다.
    """
    if href.startswith(base_url) or href.startswith("/"):
        return ""
    if any(domain in href for domain in AFFILIATE_DOMAINS):
        return ' rel="sponsored noopener" target="_blank"'
    return ' rel="noopener" target="_blank"'


def inline(text, base_url=""):
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(
        r"\[(.+?)\]\((.+?)\)",
        lambda m: f'<a href="{m.group(2)}"{link_attrs(m.group(2), base_url)}>{m.group(1)}</a>',
        text,
    )
    return text


def md_to_html(md, base_url=""):
    lines = md.split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if re.match(r"^---+$", s):
            out.append("<hr>")
            i += 1
            continue
        img_m = re.match(r"^!\[(.*?)\]\((.*?)\)$", s)
        if img_m:
            alt, src = img_m.groups()
            review = photo_review()
            replacement = review.get('inline_replacements', {}).get(src)
            item = review['approved'].get(replacement)
            if item:
                out.append(reviewed_photo_markup({'_photo': item, 'image': base_url + item['image']}, base_url,
                                                 'hero-img', caption=True))
            i += 1
            continue
        # {{map: place name}} -> Google Maps "place" embed (no API key, no
        # arbitrary HTML — the place text is escaped and only ever placed
        # into the fixed maps.google.com/maps?q=...&output=embed URL, so
        # this can't be used to inject attacker-controlled iframe src.
        map_m = re.match(r"^\{\{map:\s*(.+?)\s*\}\}$", s)
        if map_m:
            place = urllib.parse.quote(map_m.group(1))
            out.append(
                f'<iframe class="post-map" src="https://maps.google.com/maps?q={place}&output=embed" '
                f'width="100%" height="320" style="border:0" loading="lazy" '
                f'referrerpolicy="no-referrer-when-downgrade"></iframe>'
            )
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2), base_url)}</h{lvl}>")
            i += 1
            continue
        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(f"<blockquote>{inline(' '.join(buf), base_url)}</blockquote>")
            continue
        if s.startswith("|") and i + 1 < n and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            header = [c.strip() for c in s.strip("|").split("|")]
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{inline(c, base_url)}</th>" for c in header)
            body = ""
            for r in rows:
                body += "<tr>" + "".join(f"<td>{inline(c, base_url)}</td>" for c in r) + "</tr>"
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>")
            continue
        if re.match(r"^\d+\.\s+", s):
            buf = []
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                buf.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            out.append("<ol>" + "".join(f"<li>{inline(x, base_url)}</li>" for x in buf) + "</ol>")
            continue
        if re.match(r"^[-*]\s+", s):
            buf = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                buf.append(re.sub(r"^[-*]\s+", "", lines[i].strip()))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x, base_url)}</li>" for x in buf) + "</ul>")
            continue
        buf = []
        while i < n and lines[i].strip() and not re.match(r"^(#{1,4}\s|>|\||[-*]\s|\d+\.\s|---+$)", lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(buf), base_url)}</p>")
    return "\n".join(out)


# URL path, not a filesystem path — must stay "/"-joined even on a Windows
# build machine. os.path.join() previously produced "assets\logo.jpg" when
# built locally on Windows, which 404'd the logo on every single page.
LOGO_ASSET_PATH = "assets/logo.jpg"


def brand_markup(cfg, base):
    """
    Uses the real logo file at site/assets/logo.jpg once it's been dropped in
    (see automation/assets/logo.jpg -> copied by build()). The real logo
    already has the "Land in Korea" wordmark baked into the artwork, so when
    it's present the header shows ONLY the image (no separate text, which
    would otherwise duplicate the name). Until the real file exists, falls
    back to a small CSS-drawn gate mark plus the site name as text.
    """
    real_logo = os.path.join(SITE, LOGO_ASSET_PATH)
    if os.path.exists(real_logo):
        return f'<img class="brand-logo" src="{base}/{LOGO_ASSET_PATH}" alt="{html.escape(cfg["site_name"])}">'
    return f'<span class="brand-mark" aria-hidden="true">⌂</span>{html.escape(cfg["site_name"])}'


def goatcounter_script(cfg):
    """
    GoatCounter is a free, cookieless page-view counter. Blank until the
    user creates a free account at goatcounter.com and sets
    automation/config.json's goatcounter_code (the "yourcode" part of
    yourcode.goatcounter.com) — no signup means no tracking, not a broken page.
    """
    code = cfg.get("goatcounter_code", "").strip()
    if not code:
        return ""
    return (
        f'<script data-goatcounter="https://{code}.goatcounter.com/count" '
        f'async src="//gc.zgo.at/count.js"></script>\n'
    )


def adsense_script(cfg):
    """
    Blank until automation/config.json's adsense_publisher_id is set (the
    "ca-pub-..." client id AdSense issues on signup) — no id means no script,
    not a broken page.
    """
    pub_id = cfg.get("adsense_publisher_id", "").strip()
    if not pub_id:
        return ""
    return (
        f'<script async src="https://pagead2.googlesyndication.com/pagead/js/'
        f'adsbygoogle.js?client={pub_id}" crossorigin="anonymous"></script>\n'
    )


# ---------- SEO helpers (no extra services or dependencies) ----------
LAYOUT_CONTENT_UPDATED = "2026-10-20"


def canonical_url(base, url):
    """Match Cloudflare's observed HTML redirects without renaming source files."""
    parts = urllib.parse.urlsplit(url)
    if parts.netloc != urllib.parse.urlsplit(base).netloc:
        return url
    path = parts.path
    if path == "/index.html":
        path = "/"
    elif path.endswith(".html"):
        path = path[:-5]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def jsonld_data(graph):
    # JSON lives in a raw-text script element; HTML escaping would break JSON.
    return json.dumps({"@context": "https://schema.org", "@graph": graph},
                      ensure_ascii=False).replace("<", "\\u003c")


def site_entities(cfg, base):
    return [
        {"@type": "Organization", "@id": base + "/#organization",
         "name": cfg["site_name"], "url": base + "/"},
        {"@type": "Organization", "@id": base + "/#editorial-team",
         "name": cfg["author"], "url": base + "/about",
         "parentOrganization": {"@id": base + "/#organization"}},
        {"@type": "WebSite", "@id": base + "/#website",
         "name": cfg["site_name"], "url": base + "/",
         "inLanguage": cfg["language"],
         "publisher": {"@id": base + "/#organization"}},
    ]


def related_guides(post, posts, base):
    stop = {"korea", "korean", "the", "and", "for", "with", "how", "why", "from",
            "you", "your", "what", "that", "when", "guide", "travel", "visitors"}
    def words(p):
        return set(re.findall(r"[a-z]{3,}", p["title"].lower())) - stop
    keywords = words(post)
    candidates = [p for p in posts if p["slug"] != post["slug"]]
    curated = CURATED_RELATED.get(post["slug"], [])
    candidates.sort(key=lambda p: (100 - curated.index(p["slug"]) if p["slug"] in curated else 0,
                                   p["_topic"] == post["_topic"],
                                   bool(set(p["_regions"]) & set(post["_regions"])),
                                   len(keywords & words(p)), p["date"], p["slug"]),
                    reverse=True)
    links = "".join(
        f'<li><a href="{base}/posts/{html.escape(p["slug"])}.html">'
        f'{html.escape(p["title"])}</a></li>' for p in candidates[:3])
    return ('<section class="related-guides" aria-labelledby="related-heading">'
            '<h2 id="related-heading">More guides for your trip</h2><ul>' + links + '</ul></section>') if links else ""


def start_here(posts, base):
    choices = [
        ("Getting from the airport", "korea-airport-transfer-options"),
        ("Choosing an eSIM or SIM", "korea-esim-mobile-data"),
        ("Paying by card or cash", "korea-currency-card-payments"),
        ("Storing your luggage", "korea-luggage-storage-forwarding-services"),
    ]
    slugs = {p["slug"] for p in posts}
    links = "".join(f'<li><a href="{base}/posts/{slug}.html">{label}</a></li>'
                    for label, slug in choices if slug in slugs)
    return ('<section class="start-here" aria-labelledby="start-heading">'
            '<h2 id="start-heading">What do you need help with?</h2><ul>' + links + '</ul></section>') if links else ""


# ---------- topic and destination discovery ----------
# Source categories remain untouched: existing ingestion/validation keeps its contract.
# Optional topic/regions front matter can refine classification for future posts.
DISCOVERY_UPDATED = "2026-10-22"
TOPICS = {
    "before-you-go": ("Before You Go", "Start with entry preparation, seasonal packing and the practical details to check before departure."),
    "getting-around": ("Getting Around", "Compare airport transfers, trains, buses and taxis, then find the guides you need for the next leg of your trip."),
    "sim-and-apps": ("SIM & Apps", "Choose how to stay connected and find your way around Korea with guides to mobile data, maps and useful apps."),
    "money-and-shopping": ("Money & Shopping", "Prepare for payments, currency exchange, shopping and tax refunds before you reach the checkout."),
    "food-and-culture": ("Food & Culture", "Find help with restaurant ordering, reservations, everyday etiquette and unfamiliar cultural experiences."),
    "things-to-do": ("Things to Do", "Explore places, day trips and seasonal experiences, with practical details to help you plan your visit."),
    "living-in-korea": ("Living in Korea", "Staying longer? Start with banking, housing, laundry and the everyday systems that visitors rarely need to navigate."),
}
DESTINATIONS = {
    "seoul": ("Seoul & Nearby", "Plan your time in Seoul and nearby day-trip destinations. Start with a place guide, then check transport and opening details."),
    "busan": ("Busan", "Explore Busan through our current destination and festival guides. Check each guide for the details relevant to your visit."),
    "jeju": ("Jeju", "Start with the island guide and compare how to get there. Check transport and rental requirements before booking."),
}
CURATED_RELATED = {
    "korea-esim-mobile-data": ["korea-useful-travel-apps", "korea-why-google-maps-fails-in-korea-and-how-the-road-name-address-system-actually-works", "korea-airport-transfer-options"],
    "korea-airport-transfer-options": ["korea-t-money-transit-card", "korea-esim-mobile-data", "korea-luggage-storage-forwarding-services"],
    "korea-busan-haeundae-beach-gamcheon-culture-village": ["korea-busan-fireworks-festival-gwangalli-guide", "korea-ktx-vs-srt-vs-intercity-bus-for-long-distance-travel", "korea-t-money-transit-card"],
}


def discovery_meta(p):
    text = (p["title"] + " " + p["slug"]).lower()
    if p.get("topic") in TOPICS:
        topic = p["topic"]
    else:
        rules = [
            ("living-in-korea", r"bank.account|housing|laundry|recycling|trash|hangul|cafes.as.workspaces|working.from.cafes"),
            ("sim-and-apps", r"esim|mobile.data|useful.travel.apps|travel.apps|google.maps|addresses.work|address.system"),
            ("before-you-go", r"k-eta|visa.waiver|packing|weather.tips|travel.safety|travel.advisories|emergency|pharmac|hospital|public.holidays"),
            ("money-and-shopping", r"currency|card.payments|tax.refund|tax.free|shopping|myeongdong|namdaemun|daiso|olive.young|convenience.store"),
            ("getting-around", r"airport.transfer|t.money|subway|city.buses|bus.travel|taxi|luggage|ktx|srt|intercity|domestic.flights|renting.a.car|day.tripping"),
            ("food-and-culture", r"tipping|etiquette|mistakes|noraebang|jjimjilbang|restaurant|eating.alone|food.delivery|kiosk|chuseok"),
        ]
        # Place guides keep their destination intent even when their title mentions etiquette or subway access.
        place = re.search(r"bukchon|hiking|palace|gyeongbokgung|museum|jinju|fireworks|haeundae|gamcheon|jeju.island|n.seoul.tower|namsan|insadong|hongdae|suwon|nami.island|everland|lotte.world|cherry.blossom|fall.foliage|attraction.pass|dmz|jsa", text)
        topic = "things-to-do" if place else next((key for key, pattern in rules if re.search(pattern, text)), "before-you-go")
    p["_topic"] = topic
    p["_topic_label"] = TOPICS[topic][0]
    if "regions" in p:
        p["_regions"] = [r.strip() for r in p["regions"].split(",") if r.strip() in DESTINATIONS]
    elif re.search(r"jinju|namgang", text):
        p["_regions"] = []  # Departure from Seoul does not make Jinju a Seoul destination.
    else:
        p["_regions"] = [key for key, pattern in [
            ("seoul", r"seoul|incheon|gyeongbokgung|bukchon|insadong|hongdae|myeongdong|namdaemun|suwon|nami.island|everland|lotte.world|dmz|jsa"),
            ("busan", r"busan|haeundae|gamcheon|gwangalli"),
            ("jeju", r"jeju"),
        ] if re.search(pattern,text)]
    return p


def discovery_groups(posts):
    groups = [("topics/"+key, label, desc, [p for p in posts if p["_topic"] == key])
              for key,(label,desc) in TOPICS.items()]
    groups += [("destinations/"+key, label, desc, [p for p in posts if key in p["_regions"]])
               for key,(label,desc) in DESTINATIONS.items()]
    return [g for g in groups if g[3]]


def discovery_nav(base):
    return (f'<a href="{base}/#explore-map">Destinations</a>'
            f'<a href="{base}/#trip-planner">Plan a trip</a>'
            f'<a href="{base}/#interests">Interests</a>'
            f'<a href="{base}/guides">All guides</a>')


def guide_card(p, base):
    # Collection pages use the same existing localized media as article pages.
    image = p.get("image", "").replace("https://raw.githubusercontent.com/solusupport-bot/land-in-korea-blog/media-assets/images/", base+"/media/images/")
    thumb = reviewed_photo_markup(p, base, "card-thumb") if image else ""
    return (f'<a class="card" href="{base}/posts/{html.escape(p["slug"])}" data-guide '
            f'data-search="{html.escape(p["title"]+" "+p["description"]+" "+p["_topic_label"]+" "+" ".join(p["_regions"]), quote=True)}">'
            f'{thumb}<div class="card-copy"><span class="card-topic">{html.escape(p["_topic_label"])}</span>'
            f'<h2>{html.escape(p["title"])}</h2><p>{html.escape(p["description"])}</p>'
            f'<span class="date">{html.escape(p["date"])} · {p["_reading"]} min read</span></div></a>')


def discovery_search(label="Search these guides"):
    return (f'<div class="guide-search" hidden data-search-control><label for="guide-search">{label}</label>'
            '<div class="search-row"><input id="guide-search" type="search" placeholder="Try eSIM, Busan or airport" autocomplete="off">'
            '<button type="button" id="clear-search">Clear</button></div>'
            '<p id="search-status" role="status" aria-live="polite"></p></div>')


def collection_page(cfg, base, posts, title, description, path, all_posts=None):
    all_posts = all_posts if all_posts is not None else posts
    arrival = ''
    if path.startswith('destinations/'):
        available = {p['slug'] for p in all_posts}
        choices = [
            ('Compare trains & intercity buses', 'korea-ktx-vs-srt-vs-intercity-bus-for-long-distance-travel'),
            ('Use local buses', 'korea-bus-travel-inside-korean-cities-color-codes-tag-off-transfers-and-why-you-must-flag-the-bus'),
            ('Set up SIM & eSIM', 'korea-esim-mobile-data'),
        ]
        if path == 'destinations/jeju':
            choices[0] = ('Compare domestic flights & trains', 'korea-domestic-flights-vs-ktx-for-jeju-and-long-distance-trips')
        arrival_links = ''.join(f'<a href="{base}/posts/{slug}">{label} <span aria-hidden="true">→</span></a>' for label, slug in choices if slug in available)
        arrival = f'<aside class="arrival-guides" aria-label="Practical travel guides"><h2>Before you arrive</h2><p>Plan your transport and stay connected.</p><div>{arrival_links}</div></aside>'
        region_key = path.split('/', 1)[1] if path.startswith('destinations/') else None
        if region_key in REGION_MAPS:
            arrival += region_district_map(base, region_key, available)
    links = ''.join(f'<a href="{base}/topics/{key}"' + (' aria-current="page"' if path == "topics/"+key else "") + f'>{html.escape(value[0])}</a>' for key,value in TOPICS.items() if any(p["_topic"]==key for p in all_posts))
    body = (f'<nav class="breadcrumbs" aria-label="Breadcrumb"><a href="{base}/">Home</a> / '
            f'<span aria-current="page">{html.escape(title)}</span></nav>'
            f'<section class="collection-intro"><p class="eyebrow">THE GUIDE LIBRARY</p><h1>{html.escape(title)}</h1>'
            f'<p>{html.escape(description)}</p><p class="collection-count">{len(posts)} guides</p></section>'
            f'{arrival}<nav class="topic-pills" aria-label="Browse by topic">{links}</nav>'
            f'{discovery_search()}<section class="grid" aria-label="Guides">'+''.join(guide_card(p,base) for p in posts)+'</section>'
            '<p id="search-empty" hidden>No guides match that search. Try a place or a shorter phrase.</p>'
            f'<p class="back"><a href="{base}/guides">Browse all guides →</a></p>')
    url = base+"/"+path
    graph = site_entities(cfg,base) + [{"@type":"CollectionPage", "@id":url+"#collection", "url":url,
        "name":title,"description":description,"isPartOf":{"@id":base+"/#website"},
        "mainEntity":{"@type":"ItemList","itemListElement":[{"@type":"ListItem","position":i+1,"url":base+"/posts/"+p["slug"],"name":p["title"]} for i,p in enumerate(posts)]}},
        {"@type":"BreadcrumbList","itemListElement":[{"@type":"ListItem","position":1,"name":"Home","item":base+"/"},{"@type":"ListItem","position":2,"name":title,"item":url}]}]
    return page(cfg,base,title,description,body,url,jsonld=jsonld_data(graph))


# Simplified from Natural Earth 1:50m country geometry (public domain).
# https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_50m_admin_0_countries.geojson
KOREA_MAP_PATH = 'M100.4,111.1L102.5,109.5L102.7,99.9L108.8,94.7L126.7,73.0L132.3,69.4L137.9,67.7L146.6,67.0L163.3,67.7L178.2,66.5L189.4,68.1L198.7,67.4L207.8,63.2L211.6,58.4L219.8,42.5L222.2,41.3L239.3,78.4L255.7,102.4L269.6,119.8L289.5,153.2L295.3,171.1L295.8,182.2L299.1,197.4L296.3,206.2L297.2,219.9L295.9,227.0L293.5,232.2L294.2,254.7L295.8,257.4L298.1,258.5L301.7,255.9L306.1,254.8L305.3,263.3L295.3,300.7L289.0,314.4L281.0,326.9L271.4,331.8L264.6,333.6L251.7,334.2L241.0,332.1L231.8,333.6L228.1,336.2L225.3,340.7L227.3,347.6L227.1,352.8L223.1,352.4L215.3,349.4L206.7,349.0L202.6,347.5L198.5,340.2L194.4,340.4L187.1,344.8L176.0,345.8L172.1,348.1L170.8,351.2L172.4,355.0L178.0,360.0L176.1,365.2L170.3,367.7L165.6,361.4L162.7,355.3L159.4,354.9L154.3,356.7L153.3,363.3L159.5,373.1L154.1,379.1L152.6,383.4L148.7,386.5L138.1,379.7L139.6,374.8L144.2,370.1L144.8,365.2L143.3,362.3L128.2,374.6L118.8,388.6L113.8,387.6L111.8,384.0L108.8,382.5L98.8,391.5L96.9,398.6L93.2,398.9L91.6,395.9L91.5,389.4L89.7,384.0L79.3,376.1L74.5,369.1L77.1,365.2L85.8,367.3L92.7,367.1L91.3,363.8L89.1,362.2L93.7,360.4L97.5,356.6L94.4,355.5L89.5,357.7L85.4,356.7L83.8,347.6L78.9,338.3L76.4,329.2L81.2,324.0L88.2,304.2L90.5,300.4L96.8,297.6L99.0,294.6L90.1,291.7L90.2,288.3L106.2,278.2L108.7,269.6L106.4,267.5L101.3,265.4L104.5,257.8L103.8,255.9L93.8,245.2L95.0,239.4L94.4,219.4L91.5,210.4L90.1,201.4L83.2,205.0L68.7,201.6L67.2,194.9L71.2,186.7L80.6,179.5L86.0,178.6L90.1,175.4L96.4,174.4L104.1,179.3L110.9,180.3L114.7,188.8L117.0,190.6L117.5,187.4L123.1,183.8L124.4,181.0L123.2,179.5L116.8,178.0L108.2,159.9L111.3,151.5L104.7,141.9L101.5,138.9L102.0,130.3L96.6,121.7L95.4,116.5L96.4,114.2L99.4,113.3L100.4,111.1ZM247.9,358.7L241.3,363.8L232.4,357.0L230.2,353.2L242.8,341.3L246.5,340.9L247.9,358.7ZM200.6,358.1L199.8,366.2L194.9,366.6L191.9,361.4L188.8,364.0L187.1,364.0L184.7,357.5L184.3,352.4L190.1,348.6L193.6,350.9L198.7,352.1L200.6,358.1ZM72.4,394.2L67.9,395.5L63.6,391.9L64.6,388.2L73.3,378.2L80.1,379.8L82.6,383.7L79.5,389.6L72.4,394.2ZM92.4,114.8L92.1,125.8L85.6,124.2L81.9,111.9L84.8,107.7L90.5,111.0L92.4,114.8ZM108.7,396.4L109.9,400.4L104.2,399.7L101.2,395.9L101.6,392.6L105.0,392.1L108.7,396.4ZM181.9,374.0L181.1,376.5L177.6,372.6L181.1,368.4L181.9,374.0ZM85.2,218.1L84.3,223.5L79.6,219.9L78.3,208.2L83.1,211.6L85.2,218.1ZM68.0,364.3L67.1,366.3L64.1,365.7L59.6,355.5L56.5,353.0L61.5,349.1L67.8,356.1L68.0,364.3ZM400.1,136.3L396.9,138.8L393.2,136.3L392.7,133.7L394.7,131.4L399.3,130.0L401.4,132.0L400.1,136.3ZM78.9,489.4L75.7,491.3L72.8,490.2L67.6,482.1L70.0,477.5L79.6,469.8L104.7,462.4L109.2,462.1L119.1,465.2L121.2,471.2L117.1,479.7L105.6,485.5L96.7,488.2L78.9,489.4Z'


# Busan's 16 gu/gun (district) boundaries, traced from Wikimedia Commons
# File:Busan_districts.svg (CC BY-SA 3.0, author Kurykh). viewBox "-5.5 4.5 600 450".
# Path data only — no inline fill/stroke from the source file; styled via CSS below.
BUSAN_DISTRICT_PATHS = {
    'Buk-gu': 'M284.25,207.25c-4.375-2.125-0.25-10.75,1.875-13.75s-5.75-16.625-9.75-23.875 s4.375-16,6-18.25s0.985-7.084,0.985-7.084c-1.524,0.309-8.844,1.391-16.445,2.503c-7.601,1.112-14.749,1.94-15.732,2.756 c-0.668,0.555-1.659,1.478-1.97,3.083c-0.311,1.606-0.006,5.206,0.292,7.74c0.356,3.028,0.641,4.543,0.475,5.732 s-1.701,1.779-3.043,3.583c-4.318,5.804-6.722,8.9-8.537,15.812c-0.539,2.052-1.377,7.323-2.261,8.769 c-0.885,1.446-2.169,2.322-3.54,3.351c-1.716,1.288-4.661,2.693-7.582,4.265c0.572-0.064,1.303,0.652,0.984,5.116 c-0.5,7-3.25,14.25-3.25,18.75c0,4.028,2.303,16.949,1.15,19.998l2.6,2.252c0,0,14.25,4.875,18.75,7.375 c1.584,0.88,3.494,1.122,5.229,0.99c3.195-0.243,6.208-1.725,8.396-2.615c3.218-1.312,12.729-5.583,20.776-9.791 c0.392-0.205,0.966-0.631,1.349-0.834c8.25-4.375,9.75-12.875,11.25-14.25s4.945-10.651,4.82-12.651S288.625,209.375,284.25,207.25z',
    'Busanjin-gu': 'M324.012,291.91c-2.651-1.768-3.535-5.303-1.944-6.895 c1.591-1.59-0.354-7.601-1.768-10.783c-1.415-3.182-10.077-3.005-12.198-3.358s-3.182-4.243-5.656-6.364s-9.016-1.414-10.96-2.651 c-1.945-1.237-6.01-9.899-6.541-10.783s-5.293-7.116-5.293-7.116c-8.047,4.208-17.558,8.479-20.776,9.791 c-2.187,0.891-5.2,2.372-8.396,2.615c0,0,3.175,1.074,2.114,3.549c-1.061,2.475-4.419,13.789-4.242,19.092 s3.712,13.258,4.065,15.732c0.354,2.476-0.884,11.668-1.591,13.966s-2.652,7.602-4.243,8.132l16.016,7.718 c0.191-0.147,0.392-0.304,0.602-0.47c3.359-2.652,3.182-1.768,6.541-3.713c3.359-1.943,5.657-6.01,6.011-7.248 c0.354-1.236,7.248-1.768,9.192-1.768c1.944,0,4.065,1.238,5.48,2.121c1.414,0.885,8.574-0.441,8.574-0.441 c-0.53-1.768,1.148-0.973,1.855-1.68c0.708-0.707,1.769-3.005,3.536-3.535s9.192-1.061,14.318-4.42 c5.127-3.358,9.013-10.43,9.013-10.43C326.35,292.691,325.045,292.599,324.012,291.91z',
    'Dong-gu': 'M300.324,338.049c1.944-1.944,0.707-11.667,1.061-14.849 c0.354-3.183-1.855-8.397-2.387-10.165c0,0-7.16,1.326-8.574,0.441c-1.415-0.883-3.536-2.121-5.48-2.121 c-1.945,0-8.839,0.531-9.192,1.768c-0.354,1.238-2.652,5.305-6.011,7.248c-3.358,1.945-3.182,1.061-6.541,3.713 c-0.21,0.166-0.411,0.322-0.602,0.47h0h0c-2.864,2.213-3.641,2.513-3.641,4.833c0,2.476-1.414,5.48-1.591,7.072 c-0.177,1.59-0.884,4.242,1.591,4.596c2.475,0.354,2.298,0.53,4.419,2.828c0.53,0.574,1.069,1.104,1.207,1.535 c0,0,8.869-0.298,14.879,0.586c5.136,0.756,11.609,2.539,13.39,3.043c0.275-1.241,1.06-1.969,2.792-3.331 c1.087-0.855,2.287-1.595,3.515-2.121c-0.614-0.426-1.763-1.337-2.02-2.363C296.788,339.816,298.379,339.994,300.324,338.049z',
    'Dongnae-gu': 'M296.258,255.494c5.304-0.884,6.011-1.591,12.551-6.541 c6.541-4.949,13.082-6.717,14.85-4.242s4.065,5.834,8.132,6.011c4.065,0.177,9.369-1.944,11.667-1.769 c2.298,0.178,10.076,1.945,11.49,2.299c1.415,0.354,4.04,0.004,4.04,0.004c-0.013-3.774,0.257-7.864-0.15-10.61 c-0.707-4.773-1.237-10.607-1.237-10.607c-2.652,0.884-4.597-1.06-7.071-2.475c-2.475-1.414-2.651-3.712-5.48-8.485 c-2.828-4.773-7.955-4.243-13.965-3.712c-6.011,0.53-8.309-2.298-10.783-4.419c-2.476-2.122-6.188-2.122-13.436,0.884 c-7.247,3.005-9.794,4.394-9.794,4.394c0.125,2-3.32,11.276-4.82,12.651s-3,9.875-11.25,14.25c-0.383,0.203-0.957,0.629-1.349,0.834 c0,0,4.763,6.232,5.293,7.116C284.944,251.075,290.955,256.379,296.258,255.494z',
    'Gangseo-gu': 'M178.25,334.25c-0.25-1.75,4.5-5.5,7-8.25s3.5-15.5,4-18.25 s1.75-12,1.5-15.25s2.25-15.25,2.5-22.25s1-9.25,2-11.25s10-5.25,13-6.75s12.75-5.25,15-5.75c0.236-0.053,0.516-0.396,0.65-0.752 c1.153-3.049-1.15-15.97-1.15-19.998c0-4.5,2.75-11.75,3.25-18.75c0.319-4.464-0.412-5.181-0.984-5.116 c-0.2,0.107-0.4,0.214-0.599,0.323c-3.477,1.905-6.9,4.007-10.133,5.412c-6.125,2.662-10.068,3.562-16.721,5.182 c-12.272,2.988-14.175,3.61-22.757,2.403c-4.553-0.64-7.315-2.063-9.667-1.757c-2.352,0.305-5.609,1.978-9.772,3.749 c-4.76,2.025-8.947,4.102-9.951,4.102c-0.502,0-1.277-0.326-1.965-1.11c-0.688-0.785-0.625-1.563-1.133-2.778 c-0.836-2.001-0.328-3.471-1.44-4.352c-1.113-0.88-4.133-1.57-6.414-1.57c-1.149,0-2.88-0.151-3.888,0.034s-1.291,0.704-1.93,1.249 c-1.279,1.09-2.368,2.9-4.437,5.781c-2.199,3.061-2.065,4.489-3.403,5.33s-4.322,1.27-7.365,1.27c-3.453,0-7.628,0.28-9.145,1.055 c-3.473,1.773-14.84,12.707-14.84,14.272c0,0.643,4.128,5.313,6.906,8.326c4.95,5.371,4.155,6.314,4.155,15.539 c0,8.935,0.621,7.736,3.897,10.445c3.396,2.807,5.086,7.207,3.852,10.988c-0.727,2.225-3.218,2.061-9.471,2.061 c-4.164,0-8.048-0.352-10.541-1.393c-1.29-0.539-1.12-1.467-3.951-1.945c-2.831-0.479-9.663-0.574-12.208-0.574 c-4.085,0-4.516-0.826-6.66-0.096c-2.144,0.73-3.876,2.828-5.314,4.801c-3.296,4.52-6.708,3.139-14.254,5.42 c-3.023,0.915-6.561,1.979-10.219,2.968l-6.674-4.189c-4.97-3.272-16.962-2.653-21.456-0.249c-4.766,2.551-3.742,4.992,0.843,15.053 c3.849,8.446,5.576,9.934,12.53,14.226c5.341,3.297,12.653,5.403,16.041,5.416c3.792,0.015,4.901,0.159,6.152,1.437 c1.251,1.276,2.379,3.996,3.912,7.836c1.505,3.771,1.827,6.873,3.522,8.998c0.574,0.721,1.934,2.469,2.837,3.135 c0.903,0.664,1.349,0.244,1.864,1.045c1.031,1.602,0.215,3.577-0.008,5.1c-0.336,2.291-0.439,3.428-2.047,4.159 s-4.355,0.862-10.719,1.411l-8.853,0.766c-2.154,1.914-2.071,4.572-2.575,7.037c-0.423,2.068-1.371,3.303-1.4,4.832 c-0.029,1.531,0.862,3.357,1.287,3.782c0.698,0.698,8.752,3.462,17.737,6.845c7.751,2.918,9.821,4.756,16.231,5.932 c3.205,0.588,7.495,1.01,10.984,1.121c3.489,0.111,6.267,0.439,6.271-0.219c0.003-0.602,0.908-3.602,2.642-5.582l2.15-2.455 c1.546-0.252,2.1,1.949,3.064,4.254c1.315,3.142,2.308,5.053,3.463,5.656c1.155,0.604,5.08-1.073,7.043-3.163 c0.905-0.964,0.696-1.015,1.862-2.317s1.365-3.371,1.76-5.096c0.396-1.727,0.755-3.572,0.818-5.937s0.491-5.2,0.757-8.64 c0.309-3.97,0.35-7.454,0.874-10.592s1.53-5.93,2.706-8.689l3.362-7.891h6.031c5.575,0,5.899,0.678,5.899,3.564 c0,1.719-2.315,7.002-4.424,12.192l-2.876,7.08l-0.484,3.306l1.991,8.08c0.652,4.932,1.824,9.976,2.807,10.791 c1.128,0.938,5.262,1.422,9.463,0.566c2.101-0.429,2.186-0.75,4.108-1.365c1.922-0.613,4.926-1.269,5.852-1.792 c1.333-0.753,3.133-3.304,4.167-6.387c1.035-3.084,1.569-7.586,1.256-10.123c-0.173-1.407,2.986-4.606,6.148-8.489 c3.185-3.91,4.871-6.259,6.278-6.959c0.599-0.299,0.59-0.062,0.849,0.258c0,0,9.806-7.512,10.806-8.762s2.75-2.75,5.75-4 c1.528-0.637,2.195-1.144,2.339-1.719C178.728,335.727,178.373,335.108,178.25,334.25z',
    'Geumjeong-gu': 'M388.5,174.75c-2.5-1.75-2.75-5.25-3.75-7.75s-8.25-1.5-13-2.75 s-2.5-7.75-2.5-11.25s2.75-8.5,5-12s3-11.75,2.75-16s-4.75-6.25-9.5-9.25c-3.871-2.445-8.902-6.548-10.633-7.984 c-0.323,0.203-0.699,0.419-1.159,0.671c-2.888,1.583-8.786,4.87-17.785,9.082c-5.703,2.67-9.406,4.864-12.858,6.074 c-3.453,1.21-6.092,0.938-10.792,1.423c-6.14,0.635-11.547,0.291-12.855,0.991c-1.308,0.7-4.782,3.62-8.07,7.708 c-1.567,1.948-2.788,3.659-4.923,6.879c-1.578,2.38-4.301,3.542-5.063,3.697c0,0,0.64,4.834-0.985,7.084s-10,11-6,18.25 s11.875,20.875,9.75,23.875s-6.25,11.625-1.875,13.75s12.695,6.974,12.82,8.974c0,0,2.547-1.388,9.794-4.394 c7.248-3.005,10.96-3.005,13.436-0.884c2.475,2.121,4.772,4.95,10.783,4.419c6.01-0.53,11.137-1.061,13.965,3.712 c2.829,4.773,3.006,7.071,5.48,8.485c2.475,1.415,4.419,3.358,7.071,2.475c2.651-0.884,3.182-2.828,6.363-4.419 c3.183-1.591,3.536-3.005,3.713-6.187s0.707-9.899,3.182-10.253c2.475-0.354,9.723-4.065,10.43-5.48 c0.707-1.414,4.773-10.783,6.541-11.313s2.651-6.718,4.243-7.425c1.431-0.636,2.908-2.835,3.191-3.273 C391.975,179.046,390.58,176.206,388.5,174.75z',
    'Haeundae-gu': 'M469.75,262.25c-2.25-0.75-2.25-7.75-3.5-10.5s-7.75-6.5-9.5-7 s-6,2-7,3.25s-3.75,5.75-5.5,6.75s-2-1.25-2.75-3.25s-2.5-7.75-3.25-10s-2.75-5.5-4.25-6.75s-11.5,2.25-14.75,2s-6.75-3.25-7.75-5 s3-3.25,4.25-5.25s3.5-12.25,3.5-16.75s-2.5-4.75-3-8.5s-6.5-10.25-7.5-12.25s-6.25-2.75-11.25-5.75 c-0.84-0.504-1.573-1.029-2.237-1.563c-0.283,0.438-1.761,2.637-3.191,3.273c-1.592,0.707-2.476,6.895-4.243,7.425 s-5.834,9.899-6.541,11.313c-0.707,1.415-7.955,5.126-10.43,5.48c-2.475,0.354-3.005,7.071-3.182,10.253s-0.53,4.596-3.713,6.187 c-3.182,1.591-3.712,3.535-6.363,4.419c0,0,0.53,5.834,1.237,10.607c0.407,2.746,0.138,6.836,0.15,10.61 c0.01,2.784,0.16,5.318,0.91,7.42c1.271,3.558,1.887,6.276,3.111,8.687c0.479,0.943,1.143,2.009,1.839,2.805 c2.475,2.828,9.369,7.778,9.899,10.43s2.651,6.541,2.828,10.253c0.146,3.051,1.271,6.47,1.67,7.594 c0.294-0.012,0.576-0.025,0.882-0.035c3.419-0.111,5.597-0.164,7.226,0.086s2.634,1.231,3.774,2.416 c1.44,1.495,3.213,3.291,4.711,3.633c1.714,0.392,6.617,0.176,9.478-0.97s3.622-3.026,5.252-4.761 c1.827-1.945,4.111-2.503,7.207-2.334c3.096,0.168,3.966,0.51,6.831,2.258c2.766,1.686,3.999,3.056,4.817,3.53 c0.819,0.475,1.225,0.054,3.44-1.839c0.979-0.836,5.233-4.142,7.304-5.555c2.07-1.414,1.957-0.938,2.406-1.152 c1.005-0.482,2.145-0.447,4.94-1.765c1.6-0.754,3.222-1.493,4.413-2.554c1.228-1.134,1.644-2.402,1.611-3.747l-0.638-5.207 c-0.014-2.072,0.002-3.784,0.279-5.333c0.277-1.55,0.665-2.699,1.058-3.172c0.393-0.474,1.195-1.559,3.16-2.295 c1.966-0.737,5.646-1.283,8.64-2.257c2.546-0.829,3.946-1.89,5.91-2.557c0.553-0.188,1.104-0.331,1.645-0.463 C473.132,264.006,471.622,262.874,469.75,262.25z',
    'Jung-gu': 'M269.741,375.525c4.066,0.531,17.854-0.354,18.738-2.121 s2.475-6.895,3.889-8.484c1.415-1.592,2.77-3.908,2.77-3.908c-1.47-2.107-1.877-4.182-2.149-7.011 c-0.239-2.489-0.368-3.904-0.135-4.954c-1.781-0.504-8.254-2.287-13.39-3.043c-6.01-0.884-14.879-0.586-14.879-0.586 c0.414,1.293-1.119,2.133-4.036,4.121c-3.889,2.652-3.889,3.006-4.773,6.012c-0.884,3.004-3.182,4.065-0.53,7.601 c2.652,3.536,5.303,6.718,6.364,9.016c1.061,2.298,2.245,5.577,2.245,5.577S265.675,374.996,269.741,375.525z',
    'Nam-gu': 'M376.684,346.051c-1.533-1.402-2.659-4.598-3.125-7.195 c-0.591-3.295-2.046-8.676-3.884-11.259c-1.837-2.582-4.057-2.364-5.293-2.418c-1.138-0.049-1.848-0.809-3.065-2.312 c-1.218-1.502-2.235-3.305-2.576-4.557c-0.084-0.31-0.016-0.62,0.149-0.935c-0.938,0.145-2.389,0.25-3.589-0.187 c-1.944-0.707-6.363-4.596-7.954-5.656c-1.592-1.061-3.183-14.143-4.773-15.203c-0.796-0.53-2.177-1.403-3.789-1.955 c-1.613-0.553-3.459-0.785-4.873-0.697c-1.727,0.108-4.042-0.27-6.19-0.707c0,0-3.886,7.071-9.013,10.43 c-5.126,3.359-12.551,3.89-14.318,4.42s-2.828,2.828-3.536,3.535c-0.707,0.707-2.386-0.088-1.855,1.68 c0.531,1.768,2.74,6.982,2.387,10.165c-0.354,3.182,0.884,12.904-1.061,14.849c-1.945,1.945-3.536,1.768-3.183,3.183 c0.257,1.026,1.405,1.938,2.02,2.363c1.375-0.589,2.783-0.907,4.107-0.777c3.478,0.341,3.569,0.569,3.545,4.945 c-0.024,4.365,1.31,6.2,5.81,7.792c3.826,1.355,5.03,1.773,11.38-0.23c3.067-0.969,4.661-2.031,6.938-2.229 c2.278-0.197,3.716-0.033,5.209,0.518c1.493,0.55,2.424,1.529,3.157,2.873c0.734,1.345,0.548,3.332,0.299,5.502 c-0.521,4.559,0.624,6.148,4.001,6.736c1.607,0.279,4.491,0.668,5.153,0.195c0.662-0.475,1.189-1.944,2.313-3.486 c1.589-2.182,1.322-2.506,2.646-2.436c1.323,0.069,1.17-1.088,2.777,1.957c2.475,4.689,6.871,6.451,10.073,5.647 c2.514-0.63,5.604-1.638,6.791-3.242c0.594-0.803,1.521-1.976,1.922-3.523s0.271-3.473,1.404-6.661 c0.801-2.256,1.516-3.201,1.947-4.452c0.433-1.252,0.583-2.812,0.607-3.467C379.294,347.945,378.217,347.453,376.684,346.051z',
    'Saha-gu': 'M248.882,393.734c2.121-1.062,0.177-14.319-0.354-17.324 c-0.531-3.006-3.889-4.773-5.304-5.304c-1.414-0.53-1.591-12.552-3.182-16.794c-1.591-4.242-9.016-4.596-11.314-5.303 c-2.298-0.707-1.768-0.884-1.944-2.829c0,0-6.718-1.944-10.43-2.651c-3.712-0.707-8.839,3.889-13.612,4.066 c-4.773,0.176-9.546-3.713-13.082-4.95c-3.536-1.237-11.073-6.364-11.073-6.364c-0.144,0.575-0.811,1.082-2.339,1.719 c-3,1.25-4.75,2.75-5.75,4s-10.806,8.762-10.806,8.762c0.349,0.432,0.819,1.206,0.484,2.849c-0.39,1.909-1.716,10.066-2.839,17.316 c-1.943,12.541-2.021,13.262,0.129,14.834c2.706,1.979,6.496,1.683,7.578-0.069c0.435-0.703,2.038-5.037,3.4-10.062 c3.517-12.972,3.709-15.888,6.58-16.798c2.472-0.785,4.599-0.362,5.625,3.133c1.025,3.496,0.86,10.505,1.576,23.069 c0.752,13.192,1.342,27.887,1.636,32.437c0.249,3.861,0.262,6.104,0.67,7.931c0.408,1.827,1.166,3.504,2.992,5.704l1.591,5.436 l4.243,7.16c1.669,2.011,4.528,0.41,7.83-1.356c1.887-1.01,1.69-3.153,1.69-7.78c0-4.934,1.213-8.193,3.734-10.129 c2.521-1.935,7.234-1.794,11.757-0.543c1.515,0.419,2.428,1.318,2.805,2.04s-0.059,2.701-0.32,4.156 c-0.68,3.785,0.856,4.049,1.111,4.46c1.244,2,9.518,2.865,10.853,0.734c0.879-1.403,0.832-5.921,0.374-11.145 c-0.458-5.223-0.93-10.444-2.222-12.548c-2.063-3.358-4.787-5.777-5.948-7.911c-1.161-2.134-2.128-4.337-2.358-5.85 c-0.459-3.026,0.397-4.352,2.952-7.979c2.183-3.099,2.79-3.809,6.873-3.073c3.495,0.629,5.935,0.881,7.158,2.356 c0.188,0.227,0.318,0.325,0.415,0.362c0.156-0.742,0.368,0.141,0,0c-0.069,0.328-0.126,0.973-0.116,2.18 c0.028,3.166-0.494,5.403-0.418,8.15c0,0,1.4-0.083,2.284-0.613S246.76,394.795,248.882,393.734z',
    'Sasang-gu': 'M250.826,308.704c0.707-2.298,1.944-11.49,1.591-13.966 c-0.354-2.475-3.889-10.43-4.065-15.732s3.182-16.617,4.242-19.092c1.061-2.475-2.114-3.549-2.114-3.549 c-1.736,0.132-3.646-0.11-5.229-0.99c-4.5-2.5-18.75-7.375-18.75-7.375l-2.6-2.252c-0.135,0.356-0.415,0.699-0.65,0.752 c-2.25,0.5-12,4.25-15,5.75s-12,4.75-13,6.75s-1.75,4.25-2,11.25s-2.75,19-2.5,22.25s-1,12.5-1.5,15.25s-1.5,15.5-4,18.25 s-7.25,6.5-7,8.25c0.123,0.858,0.478,1.477,0.339,2.031c0,0,7.537,5.127,11.073,6.364c3.535,1.237,8.308,5.126,13.082,4.95 c4.773-0.178,9.899-4.773,13.612-4.066c3.712,0.707,10.43,2.651,10.43,2.651c-0.177-1.944,0.707-5.479,3.182-6.187 c2.475-0.707,2.298-6.718,4.596-10.961c2.298-4.242,2.122-7.955,2.652-9.545c0.53-1.592,7.778-2.122,9.369-2.652 S250.119,311.002,250.826,308.704z',
    'Seo-gu': 'M237.214,319.488c-0.53,1.59-0.354,5.303-2.652,9.545 c-2.298,4.243-2.121,10.254-4.596,10.961c-2.475,0.707-3.358,4.242-3.182,6.187c0.177,1.945-0.354,2.122,1.944,2.829 c2.298,0.707,9.723,1.061,11.314,5.303c1.591,4.242,1.768,16.264,3.182,16.794c1.415,0.53,4.773,2.298,5.304,5.304 c0.53,3.005,2.475,16.263,0.354,17.324c-2.122,1.061-6.188,4.949-7.071,5.479s-2.284,0.613-2.284,0.613 c0.049,1.798,0.564,3.963,0.927,6.232c0.458,2.869-0.021,0.796,0.784,3.647c0.402,1.426,0.043,5.298,0.659,7.635 c0.616,2.338,2.56,3.36,2.795,3.596c0.54,0.54,0.501,1.361,1.333,1.264c0.833-0.098,2.442-1.169,4-2.174 c1.835-1.184,3.968-1.872,5.483-3.589l-1.233-7.225l-1.012-6.915l3.436-3.103l5.955-3.059l0.105-5.204 c-0.343-1.498-0.628-2.977-1.31-4.586c-0.794-1.877-0.811-4.107-0.119-5.639c0.594-1.316,1.529-2.582,2.526-2.965 c0,0-1.184-3.279-2.245-5.577c-1.061-2.298-3.712-5.479-6.364-9.016c-2.652-3.535-0.354-4.597,0.53-7.601 c0.884-3.006,0.884-3.359,4.773-6.012c2.917-1.988,4.45-2.828,4.036-4.121c-0.138-0.431-0.677-0.961-1.207-1.535 c-2.122-2.298-1.945-2.475-4.419-2.828c-2.475-0.354-1.768-3.006-1.591-4.596c0.177-1.592,1.591-4.597,1.591-7.072 c0-2.32,0.777-2.62,3.641-4.833l-16.016-7.718C244.993,317.366,237.745,317.896,237.214,319.488z',
    'Suyeong-gu': 'M377.575,290.85c-0.177-3.712-2.298-7.602-2.828-10.253 s-7.425-7.602-9.899-10.43c-0.696-0.796-1.359-1.861-1.839-2.805c0,0-7.885-0.554-10.359,1.921s-3.712,5.834-6.011,6.188 c-2.298,0.354-4.772,1.414-5.126,3.535s-0.707,6.363-1.414,6.363s-1.314,9.006-1.314,9.006c1.612,0.552,2.993,1.425,3.789,1.955 c1.591,1.061,3.182,14.143,4.773,15.203c1.591,1.061,6.01,4.949,7.954,5.656c1.2,0.437,2.651,0.331,3.589,0.187 c0.426-0.809,1.561-1.652,2.887-2.632c1.939-1.434,3.599-6.377,3.846-8.854c0.581-5.813,4.339-7.079,13.623-7.447 C378.847,297.319,377.721,293.9,377.575,290.85z',
    'Yeongdo-gu': 'M337.23,416.471c-8.031-7.23-10.009-9.766-10.009-15.522 c0-2.373,0.063-5.413-0.708-7.541c-0.771-2.129-2.374-3.347-4.999-6.496c-3.357-4.028-11.36-12.605-17.136-18.032 c-4.93-4.633-7.771-5.759-9.24-7.867c0,0-1.355,2.316-2.77,3.908c-1.414,1.59-3.005,6.717-3.889,8.484s-14.672,2.652-18.738,2.121 c-4.066-0.529-5.887,2.219-5.887,2.219c2.934-1.126,5.362,3.209,5.362,7.831c0,1.477-0.758,2.359,0.977,3.862 c1.734,1.504,6.094,3.495,11.115,6.217c15.836,8.585,21.05,11.898,22.039,16.399c0.454,2.065,2.058,4.39,3.563,5.166 c1.506,0.775,5.66,4.065,9.286,6.826c6.56,4.992,15.698,8.113,19.757,6.825c1.17-0.371,3.105-5.331,4.03-7.889 C341.602,418.508,340.732,419.624,337.23,416.471z',
    'Yeonje-gu': 'M359.897,258.676c-0.75-2.102-0.9-4.636-0.91-7.42 c0,0-2.625,0.35-4.04-0.004c-1.414-0.354-9.192-2.121-11.49-2.299c-2.298-0.176-7.602,1.945-11.667,1.769 c-4.066-0.177-6.364-3.536-8.132-6.011s-8.309-0.707-14.85,4.242c-6.54,4.95-7.247,5.657-12.551,6.541 c-5.303,0.885-11.313-4.419-11.313-4.419c0.53,0.884,4.596,9.546,6.541,10.783c1.944,1.237,8.486,0.53,10.96,2.651 s3.535,6.011,5.656,6.364s10.783,0.177,12.198,3.358c1.414,3.183,3.358,9.193,1.768,10.783c-1.591,1.592-0.707,5.127,1.944,6.895 c1.033,0.688,2.338,0.781,3.709,1.061c2.148,0.438,4.464,0.815,6.19,0.707c1.414-0.088,3.26,0.145,4.873,0.697 c0,0,0.607-9.006,1.314-9.006s1.061-4.242,1.414-6.363s2.828-3.182,5.126-3.535c2.299-0.354,3.536-3.713,6.011-6.188 s10.359-1.921,10.359-1.921C361.784,264.952,361.168,262.233,359.897,258.676z',
    'Gijang-gun': 'M573.166,93.016c-0.629-0.388-0.542-0.071-0.328-2.738l0.295-3.694 l0.631-3.471l-6.206-3.887c-1.374-0.86-3.735-1.976-5.344-2.636c-1.608-0.66-2.465-0.865-3.729-1.913 c-2.531-2.096-4.294-4.597-5.694-6.31c-2.801-3.426-2.535-4.202-1.442-7.849c1.231-4.111,2.975-4.186,1.247-7.527 c-0.687-1.328-2.983-5.827-3.149-7.141s-0.1-1.675,0.146-3.209c0.339-2.12,1.729-3.46,1.364-4.857s-2.486-3.298-5.152-5.87 c-1.598-1.542-4.558-3.347-5.885-5.179c-1.326-1.833-2.527-3.693-3.176-5.123c-0.56-1.233-1.676-3.306-1.73-4.953 c-0.047-1.413-0.94-2.037-2.415-3.175c-2.148-1.658-5.609-1.162-8.153-1.383c-1.131-0.098-10.57,2.075-14.796,3.517 c-3.879,1.324-2.326,0.437-4.763,0.851c-4.873,0.828,0.933-0.336-7.479,1.509c-5.637,1.236-10.831,2.831-14.824,3.448 c-3.993,0.618-6.73,0.873-7.343,0.659c-0.612-0.214-2.531-0.762-4.043-2.097c-1.511-1.335-2.671-3.347-3.887-4.481 c-1.443-1.348-1.113-1.203-2.889-2.356c-1.774-1.153-5.209-2.042-6.817-2.557c-3.218-1.029-5.268-0.447-6.391,1.069 c-0.593,0.801-0.813,1.976-1.106,2.847s-0.661,1.885-0.434,2.588c0.454,1.406,2.172,2.239,4.252,3.777 c2.114,1.563,3.563,2.56,3.858,4.278c0.294,1.718,0.047,1.48-1.343,4.146c-0.26,0.498-0.843,0.774-1.193,1.386 c-0.352,0.612-0.472,1.56-0.699,2.283c-0.455,1.447-0.837,4.173-0.839,4.93c-0.007,2.69-2.487,6.718-4.472,6.718 c-0.55,0-3.144,1.704-5.765,3.788c-4.128,3.28-6.925,3.661-13.608,3.65c-4.54-0.007-9.229-1.605-14.125-3.838 c-2.448-1.117-3.755-1.527-5.567-2.989c-2.426-1.956-3.738-4.265-5.17-5.788c-3.147-3.35-3.368-1.947-8.473-1.947 c-8.265,0-18.7,2.153-18.7,4.045c0,0.43-0.958,0.723-0.573,1.597s0.986,1.705,1.854,2.459c2.005,1.743,4.948,3.804,5.011,5.166 c0.063,1.362-0.255,1.899-2.523,5.095c-1.108,1.562-2.084,1.673-3.527,4.468c-1.449,2.807-3.051,9.487-3.247,12.313 c-0.449,6.451-4.093,9.479-7.688,15.648c-1.274,2.188-3.144,2.85-4.363,5.1c-1.221,2.25-2.917,8.275-3.349,9.817 c-0.801,2.859-0.848,3.497-2.557,4.568c1.73,1.436,6.762,5.539,10.633,7.984c4.75,3,9.25,5,9.5,9.25s-0.5,12.5-2.75,16s-5,8.5-5,12 s-2.25,10,2.5,11.25s12,0.25,13,2.75s1.25,6,3.75,7.75c2.105,1.474,3.439,4.187,6.815,6.855c0.632,0.5,1.396,1.171,2.185,1.645 c5,3,10.25,3.75,11.25,5.75s7,8.5,7.5,12.25s3,4,3,8.5s-2.25,14.75-3.5,16.75s-5.25,3.5-4.25,5.25s4.5,4.75,7.75,5 s13.25-3.25,14.75-2s3.5,4.5,4.25,6.75s2.5,8,3.25,10s1,4.25,2.75,3.25s4.5-5.5,5.5-6.75s5.25-3.75,7-3.25s8.25,4.25,9.5,7 s1.25,9.75,3.5,10.5c1.872,0.624,3.382,1.756,3.842,2.122c1.379-0.338,2.669-0.6,3.623-1.312c2.655-1.982,3.983-5.096,5.663-7.809 c2.51-4.055,1.933-4.449,3.21-5.41c1.276-0.959,2.355-0.905,4.072-0.905c3.948,0,5.771-1.591,4.763-6.972 c-0.577-3.076-0.746-7.089-2.909-7.911c-0.964-0.366-3.024,0.204-3.52-0.138c-0.494-0.342-0.93-0.369-0.986-1.04 c-0.114-1.344,1.283-3.757,3.168-6.541c1.639-2.418,3.214-3.331,3.706-4.894c0.255-0.812,0.328-3.744,0.502-4.206 c0.175-0.462,0.45-0.741,1.112-0.82c1.325-0.158,3.303,0.929,5.087,2.278c1.976,1.495,4.939,2.957,5.569,2.131 c0.314-0.413-0.401-0.798-0.247-2.205c0.155-1.406-0.383-4.225-0.383-6.268c0-3.643,0.02-5.663,0.688-7.415s0.003-1.442,2.688-3.693 c2.445-2.051,4.741-4.427,6.668-7.599l0.971-10.165c0.273-2.859,0.247-7.402-0.133-9.658c-0.379-2.255-1.112-2.225-1.361-2.854 c-0.427-1.083-1.056-2.947-0.946-3.361c0.01-0.038,0.024-0.07,0.04-0.1c-0.177,0.127-0.556,0.343-1.297,0.651 c-2.069,0.861-3.913,4.666-5.43,4.93c-0.79,0.138-1.374,0.135-2.4-0.103c-1.025-0.238-1.997-0.645-2.576-1.224 c-2.139-2.139-1.772-5.155,0.27-7.714c2.063-2.587,4.655-4.865,8.928-5.652c1.663-0.306,2.978,0.011,3.642-0.334 c0.96-0.498,2.629-1.006,3.427-1.519c1.597-1.026,2.685-3.596,4.024-5.643c2.449-3.74,3.709-4.496,4.092-6.424 c0.191-0.964,0.149-3.7-0.128-4.905c-0.278-1.205-0.29-1.382-1.042-2.026c-0.854-0.73-2.67-0.693-3.091-0.893 c-0.422-0.2-0.618-0.357-0.574-0.921c0.087-1.127,1.137-2.371,3.055-3.627c0.764-0.5,2.57-1.913,3.121-2.402 c0.551-0.489,0.281-1.933,0.33-3c0.09-1.989-0.389-3.948-0.278-10.841c0.153-9.58,0.456-15.905,1.396-18.507 s2.433-1.763,6.214-2.265c2.449-0.325,5.229-0.278,6.826-1.159s1.957-3.301,2.804-4.655c1.179-1.886,1.021-1.311,1.217-1.585 c0.195-0.275,1.007-1.212,3.58,2.168c2.399,3.152,4.642,5.184,6.942,6.156c1.149,0.487,2.342,0.681,3.716,0.701 c1.373,0.02,6.655,0.973,8.538,0.888c5.509-0.249,2.562-0.486,3.748-0.961c0.618-0.247,1.938-1.856,1.854-2.516 S573.917,93.48,573.166,93.016z',
}

# Seoul's 25 gu, traced from Wikimedia Commons File:Seoul_districts.svg
# (CC BY-SA 3.0, Kurykh — same author/series as the Busan map above). viewBox "0 0 1400 1400".
SEOUL_DISTRICT_PATHS = {
    'Dobong-gu': 'M964.064,164.667 c-1.447,9.018-0.285,18.105-2.002,27.506c-2.068,11.332-9.018,22.101-11.502,33.507c-0.867,3.979-0.977,9.201-1.5,14.003 c-0.508,4.656-1.969,10.129-1.5,14.003c0.779,6.456,5.756,14.04,8.502,21.005c2.287,5.805,7.385,15.948,8.002,21.505 c0.539,4.856-0.953,11.628-1.502,17.504c-0.547,5.879-1.484,11.904-2,17.504c-1.135,12.346-0.799,25.368-3,36.008 c-1.582,7.641-5.57,14.402-7.002,21.505c-1.725,8.558-1.271,18.438-3,27.506c-6.813,0.022-13.387,0.283-19.506,1 c-14.793-19.111-31.705-39.509-48.51-58.013c-4.902-5.398-11.217-16.078-17.504-18.504c-7.016-2.707-17.623,0.042-22.006-7.001 c-4.459-4.876-9.127-9.544-14.002-14.003c-0.148-1.02-1.354-0.98-1.502-2c-3.959-4.376-8.127-8.544-12.502-12.503 c-2.484-9.723,2.434-16.186,3.5-24.005c1.156-1.678,0.176-5.493,0.5-8.001c0-4.168,0-8.335,0-12.503 c-1.914-13.504,2.932-25.383,2.502-37.009c-0.459-12.384-5.236-23.798-6.002-36.508c11.756-1.325,7.563-21.182,9.002-35.508 c0.838-8.333,5.449-13.907,6.502-19.504c9.998-4.506,22.598-6.408,38.008-5.501c2.178-0.323,5.654,0.652,7.002-0.5 c3.451,0.612,7.951-0.803,10.502,0c9.178,2.887,3.551,20.857,10.002,25.005c4.402,2.831,14.043,1.217,20.506,1.5 c7.441,0.328,14.299,0.634,21.004,1C944.035,158.024,948.826,166.568,964.064,164.667z',
    'Dongdaemun-gu': 'M943.059,562.258 c17.314-12.86,45.85-14.498,69.016-21.505c4.727,50.787,10.273,100.752,15.504,151.034c-6.555,14.951-12.34,30.67-19.004,45.511 c-22.848-9.494-47.135-17.548-71.518-25.506c-1.996-7.838-4.219-15.451-7-22.505c-1.004-1.498-4.105-0.896-6.502-1 c-0.693-1.14-3.48-0.187-5.002-0.5c-4.93,0.306-9.943-0.296-15.002-0.5c-5.111-0.206-11.324-1.679-15.004-0.5 c-4.971,1.592-4.482,8.52-9.002,11.003c-1.5,0-3.002,0-4.502,0c-0.693-1.141-3.48-0.187-5-0.5 c-6.898-0.604-9.074-5.929-13.504-9.002c0-2,0-4.001,0-6.001c1.15-1.184,0.18-4.488,0.5-6.501c0-3.334,0-6.668,0-10.002 c-1.777-8.816,6.691-12.109,9.502-18.004c3.188-6.685,2.475-12.889,5.502-20.004c13.135-9.705,25.787-19.891,43.01-25.506 c5.984-9.353,9.824-20.849,17.004-29.006C935.932,569.802,939.6,566.134,943.059,562.258z',
    'Dongjak-gu': 'M549.469,914.338 c12.88,1.814,26.066,2.353,40.509,1.5c5.67-0.331,12.667,0.665,17.504-0.5c1.864,0.031,3.163-0.504,4.501-1 c6.505,6.898,12.973,14.271,19.505,22.006c6.095,7.215,12.891,16.18,21.505,20.504c7.69,3.861,17.505,3.021,28.006,5.502 c1.606,6.563,2.639,13.698,5.001,19.504c6.734,1.602,15.933,0.738,23.005,2c4.186,7.818,7.715,16.29,11.502,24.506 c-4.217,11.309-7.695,25.375-9.502,41.01c-2.027,17.546-1.936,31.865,2,45.01c-7.719,3.617-13.839,8.832-27.005,7.002 c-3,0-6.001,0-9.002,0c-4.278-3.027-8.12-6.408-12.502-10.002c-3.631-2.978-9.757-6.318-12.003-10.503 c-3.533-6.582-4.023-16.151-6.001-24.005c-2.02-8.021-4.385-15.643-6.501-23.506c-12.507-4.498-23.557-10.451-37.009-14.004 c-7.835,0-15.67,0-23.505,0c-4.837,0.332-10.999-0.662-15.003,0.501c-6.668,0-13.336,0-20.004,0 c-5.503,0.331-12.333-0.664-17.004,0.5c-4.723,4.112-5.059,12.612-8.502,18.004c-8.232,6.149-19.541,2.86-29.507,6.001 c-9.423,2.971-16.667,13.793-26.506,13.504c1.216-7.453,2.1-15.238,3.5-22.506c4.8-3.152,7.619-8.869,13.503-11.502 c8.821-3.948,20.193-3.832,30.507-7.002c2.514-8.961,8.113-16.396,10.002-25.506c1.771-8.537-0.226-17.053,1.5-26.506 c0.636-3.48,3.329-7.236,5.001-11.002C533.56,938.992,541.504,925.207,549.469,914.338z',
    'Eunpyeong-gu': 'M575.476,330.705 c7.587,0.952,9.29-2.505,13.003-6.501c7.224-7.775,13.519-17.169,18.004-25.505c14.693,12.132,30.782,26.277,46.01,39.509 c3.894,3.383,13.752,9.375,15.003,13.503c0.862,2.844-0.673,6.792,0,9.002c0,4.834,0,9.669,0,14.503 c0.324,2.343-0.654,5.989,0.5,7.501c1.953,14.456-6.109,18.896-9.502,28.007c0.628,0.205,1.022,0.645,1,1.5 c-5.143,9.597-10.142,17.47-17.004,25.006c-3.037,3.335-7.588,6.393-9.002,10.002c-2.79,7.125-1.877,15.108-4.501,24.006 c-1.152,1.348-0.178,4.823-0.5,7.001c0,3.667,0,7.335,0,11.002c0,3.334,0,6.668,0,10.002c-1.152,1.348-0.178,4.824-0.5,7.001 c0,3.667,0,7.335,0,11.002c0,4.167,0,8.335,0,12.503c-9.909-2.624-14.437,0.35-20.505,4.001c-2.967,1.785-6.638,3.607-8.502,5.501 c-6.809,6.919-9.348,18.968-12.003,28.006c-9.573,3.93-19.012,7.994-29.506,11.003c-2.983,8.353-6.446,16.227-11.502,22.505 c-1.074,0.927-3.664,0.338-5.501,0.5c-3.173,0.328-7.661-0.659-10.002,0.5c-4.668,0-9.335,0-14.003,0 c-5.337,0.332-12-0.664-16.504,0.5c-3.186-0.352-4.295,1.374-5.501,3c-2.043,1.625-3.877,3.458-5.501,5.501 c-1.876,1.458-3.543,3.125-5.001,5.001c0,0.167,0,0.333,0,0.5c-2.043,1.624-3.876,3.458-5.501,5.501 c-1.874,6.295-2.348,13.989-3.501,21.005c-7.333-4.537-13.729-10.497-21.005-16.004c-7.046-5.333-15.879-9.708-21.505-16.003 c-3.167-3.544-4.184-8.449-8.001-11.502c-9.872,0.035-11.732-7.939-16.003-13.503c10.749,5.088,25.466,6.208,41.509,6.001 c5.736-6.742,14.377-14.342,20.005-22.505c5.505-7.985,9.425-16.801,8.002-31.007c0-3.167,0-6.334,0-9.502 c0-7.168,0-14.336,0-21.505c-0.331-5.17,0.664-11.666-0.5-16.003c0-10.335,0-20.671,0-31.007c2.321-3.633,8.118-5.423,9.502-9.502 c1.134-3.341,0.625-8.999,1-13.503c2.579-30.969,5.016-65.498,8.002-94.521c11.516,0.432,24.255,0.549,34.508,1 c3.694,0.163,8,1.385,11.503,0c7.246-2.866,6.368-17.586,19.004-13.503c0.693,1.141,3.481,0.187,5.001,0.5 C569.141,330.705,572.308,330.705,575.476,330.705z',
    'Gangbuk-gu': 'M809.029,188.173 c0.912,12.448,6.383,24.226,6.5,36.508c0.084,8.802-3.998,17.245-3,27.006c-1.156,1.678-0.176,5.492-0.5,8.001 c0,4.167,0,8.335,0,12.503c1.809,14.38-6.305,20.132-3.5,34.008c1.307,1.693,2.83,3.171,4,5.001 c4.125,4.543,8.461,8.877,13.002,13.003c4.461,4.876,9.127,9.544,14.004,14.003c4.174,4.995,14.822,3.516,22.006,5.501 c29.617,32.396,58.822,65.205,81.518,104.524c-9.205,15.968-17.861,32.483-26.506,49.011c-4.91,2.591-8.998,6.004-14.004,8.502 c-3.006,0.327-7.326-0.659-9.502,0.5c-3,0-6,0-9.002,0c-3.5,0-7.002,0-10.502,0c-2.178,0.323-5.652-0.652-7.002,0.5 c-3,0-6,0-9.002,0c-3.5,0-7.002,0-10.502,0c-2.5,0-5.002,0-7.502,0c-3.18-1.987-7.064-3.271-9.502-6.001 c-4.273-15.565-7.566-32.108-12.002-47.511c-12.75-10.672-26.533-20.346-39.51-30.507c-4.32-3.382-8.381-8.007-13.004-10.502 c-6.852-3.699-15.078-4.146-22.504-8.002c-3.998-4.838-7.506-10.165-11.502-15.003c1.264-2.903,3.693-4.642,4.5-8.002 c1.158-2.01,0.174-6.161,0.5-9.002c-0.324-2.509,0.656-6.324-0.5-8.002c0-5.168,0-10.336,0-15.504c0-5.001,0-10.002,0-15.003 c-0.324-2.344,0.654-5.989-0.5-7.502c0-5.168,0-10.336,0-15.503c0-5.001,0-10.002,0-15.003c0-2.334,0-4.668,0-7.002 c6.334-11.432,14.252-23.222,21.004-34.508c2.314-3.867,5.734-8.378,7.002-12.002c4.479-12.813,3.801-26.4,8.502-40.509 C784.264,197.082,796.582,192.563,809.029,188.173z',
    'Gangdong-gu': 'M1169.111,741.299 c5.732-6.754,13.379-10.111,22.004-15.504c8.936-5.586,14.174-12.908,26.506-13.003c10.67-0.333,22.672,0.667,32.508-0.5 c18.525-4.354,29.406-16.417,44.01-25.006c8.764-5.154,19.996-8.898,28.506-14.003c0,1.667,0,3.334,0,5.001 c0.326,2.509-0.654,6.323,0.502,8.002c-0.258,11.092,11.025,10.646,17.004,15.504c1.512,1.154,5.158,0.176,7.5,0.5 c1.086,7.083,1.484,14.853,4.502,20.005c-1.932,10.422-10.932,19.746-9.502,32.007c0.439,3.773,5.059,8.253,6,13.503 c1.15,6.411-0.707,13.824,0.502,19.504c0.762,3.582,4.42,6.434,5,9.503c-2.693,4.808-5.02,9.982-8.002,14.503 c-14.064,3.812-27.102,8.573-43.51,14.004c-6.725,2.226-13.926,4.617-21.504,7.001c-6.23,1.96-18.705,4.1-21.506,7.502 c-2.205,2.679-1.711,10.053-2,16.003c-0.27,5.521-0.115,11.744-0.5,16.004c-0.494,5.479-4.258,10.002-7.002,14.504 c-7.244,11.887-13.189,21.056-20.504,33.008c-5.291-1.752-9.098-0.548-13.504-2.501c-4.646-2.06-6.66-7.547-11.002-9.003 c-5.998-2.009-12.977-0.372-21.006-2c-4.625-8.117-5.539-17.827-10.502-25.006c-4.063-5.875-11.83-7.93-15.504-14.003 c5.16-14.568,16.49-35.228,7.002-51.011c-3.82-0.68-7.887-1.115-12.502-1.001c-3.299-2.87-5.486-6.851-8.502-10.003 c4.039-21.131,4.615-45.729,18.504-57.012C1164.984,745.842,1167.152,743.675,1169.111,741.299z',
    'Gangseo-gu': 'M134.875,530.751 c14.186,7.902,28.88,17.473,41.509,27.006c4.251,3.209,9.459,6.075,13.003,9.502c4.232,4.093,7.689,10.18,11.502,15.004 c3.918,4.956,7.421,10.604,11.503,15.003c6.56,7.07,15.108,12.175,21.505,19.005c8.05,8.595,12.902,20.3,21.505,29.006 c6.374,6.451,17.3,10.128,26.006,15.003c3.432,1.921,6.93,4.513,10.502,6.501c3.87,2.155,8.53,3.593,11.502,6.001 c3.497,2.833,6.386,8.233,10.002,12.503c3.64,4.297,8.201,8.06,10.502,12.002c3.5,5.994,4.969,13.018,8.502,18.504 c5.596,8.689,15.438,15.27,23.005,22.005c8.075,7.188,16.7,13.607,24.506,21.005c2.256,5.079,2.541,12.129,3.001,19.004 c0,0.334,0,0.667,0,1c-6.002,0-12.003,0-18.004,0c-5.92-1.747-10.951-4.385-18.504-4.5c-3.843,4.658-6.47,10.533-10.002,15.503 c-0.172,10.497-1.497,19.842-1.5,30.507c-5.514,8.156-10.331,17.01-15.003,26.006c-8.665,1.709-16.358,9.035-25.006,9.502 c-3.351,0.182-9.662-1.734-13.503-2.5c-10.699-2.131-20.229-4.968-25.006-12.003c-1.979-2.915-3.274-7.977-5.001-12.003 c-3.733-8.706-7.112-16.434-10.502-24.506c-1.854-4.414-2.894-9.518-5.001-12.502c-1.992-2.822-7.871-7.548-10.502-8.002 c-6.062-1.047-14.354,2.875-20.005,2c-3.302,5.2-6.064,10.939-9.002,16.504c-8.57-0.594-16.527-0.893-25.005-1.5 c-6.744-0.483-19.111-2.885-25.506-1.5c-5.025,1.087-10.524,10.202-14.003,14.003c-4.763,5.202-9.093,9.708-14.003,14.003 c-2.676-5.246-5.24-10.084-7.502-16.004c-1.854-4.855-3.532-13.175-7.001-16.504c-2.526-2.423-9.272-3.868-13.503-5.501 c-14.528-5.605-28.749-11.121-42.009-16.503c-0.499-13.672-5.639-22.701-10.002-32.508c4.057-5.3,11.855-4.416,15.504-10.002 c1.666-2.551,1.634-7.865,2.5-12.004c2.518-12.024,6.03-25.201,9.002-35.508c5.497-2.554,11.032-4.787,17.504-7.501 c4.729-1.983,13.795-4.065,16.503-8.502c3.111-5.097,2.565-13.703,3.5-21.004c0.943-7.357,1.846-14.689,2.501-22.005 c3.839-3.569,7.796-7.497,13.003-12.002c6.48-5.608,15.725-9.435,13.003-23.005c1.155-1.678,0.175-5.493,0.5-8.002 c1.876-13.255-8.925-25.063-6.001-37.508C126.606,538.5,132.789,536.269,134.875,530.751z',
    'Geumcheon-gu': 'M409.438,1062.872 c10.055-0.948,14.109-7.896,22.005-11.003c8.709,3.959,17.785,18.691,29.007,11.503c1.936,4.731,4.911,8.425,7.001,13.003 c4.371,1.964,8.918,3.752,13.003,6.002c-0.113,3.721-0.805,6.863-0.5,11.002c-0.828,0.7-0.828,4.801,0,5.502 c1.265,19.843,4.213,42.334,6.502,61.514c0.842,7.057,0.685,15.172,2.5,20.504c1.959,5.756,8.856,10.419,10.502,16.004 c4.774,1.228,8.83,3.173,13.503,4.501c1.205,6.297,3.346,11.657,5.001,17.505c-0.607,2.56-3.158,3.176-4.501,5 c-16.374,3.465-20.527,19.148-25.006,34.508c-8.089,0.664-14.721,8.753-23.005,10.003c-9.996,1.508-19.743-2.559-31.507-1.501 c-6.953-26.869-16.129-59.129-24.006-88.02c-2.003-7.346-3.826-24.808-9.002-28.506c-3.041-2.174-9.421-1.811-13.503-2.001 c-2.895-34.114-17.084-56.934-26.506-84.52c2.5,0,5.001,0,7.501,0c3.009,0.833,9.495,0.833,12.503,0c2.834,0,5.668,0,8.502,0 c1.849-0.319,4.982,0.647,6.002-0.5c2.667,0,5.334,0,8.001,0C405.284,1063.053,408.418,1064.02,409.438,1062.872z',
    'Guro-gu': 'M428.942,1040.367c0.317,1.684-0.644,4.645,0.5,5.501 c0,1,0,2.001,0,3.001c-7.014,3.32-11.124,9.547-20.504,10.502c-1.849,0.319-4.982-0.647-6.001,0.5c-2.667,0-5.334,0-8.002,0 c-1.848,0.319-4.982-0.647-6.001,0.5c-2.834,0-5.668,0-8.502,0c-3.834,0-7.668,0-11.502,0c-3.07-0.264-7.24,0.572-9.502-0.5 c-2.025-8.383,1.501-17.846-2-24.506c-1.383-2.629-7.673-5.575-10.002-7.001c-3.662-2.241-8.242-5.894-11.502-6.001 c-2.817-0.094-7.527,2.728-10.002,4c-2.836,1.459-7.151,3.227-9.502,5.002c-2.417,1.824-7.969,9.016-9.002,12.002 c-2.259,6.529,0.466,16.63-5.001,19.005c-2.709,2.292-5.21,4.792-7.502,7.501c-3.542,3.126-6.877,6.46-10.002,10.003 c-1.09,1.744-2.248,3.421-4.001,4.501c-2.709,2.291-5.21,4.792-7.502,7.502c-3.542,3.125-6.877,6.459-10.002,10.002 c-2.603,2.77-4.024,6.548-7.502,7.502c-6.091,1.67-16.19,0.877-22.005,0c-5.793-0.873-10.356-4.833-15.504-5.002 c-10.37-0.339-19.73,5.449-29.006,6.002c-5.163,0.307-9.707-1.09-15.503-1.5c5.927-12.122,11.899-26.067,18.004-39.01 c2.118-4.488,5.993-9.389,6.001-13.502c0.012-5.465-5.336-12.942-8.502-20.006c-2.061-4.598-8.586-15.993-8.502-20.004 c0.044-2.09,2.84-6.572,4.501-10.002c8.828-18.236,18.595-35.627,21.505-57.014c-0.289-3.212-3.403-3.599-4.501-6.001 c7.568,0.767,14.526,2.144,21.505,3.501c2.272,5.395,3.418,11.918,7.001,16.004c0.54,0.827,4.462,0.827,5.001,0 c3.5,0,7.001,0,10.502,0c4.004-0.33,9.33,0.662,12.503-0.5c5.631-2.188,10.712-8.438,16.504-13.504 c2.238-1.957,5.196-3.902,8.001-6.501c4.358-4.038,6.289-8.403,13.503-7.002c0.855,1.145,3.817,0.184,5.501,0.501 c4.167,0,8.335,0,12.503,0c2.144,7.523,5.065,14.271,7.001,22.004c4.975,1.3,9.393,5.851,15.503,6.502 c7.731,0.824,16.25-1.998,25.506-1c1.52-0.314,4.308,0.641,5.001-0.5c3.791-2.215,6.551-9.283,9.002-14.504 c2.473-5.266,3.817-10.847,7.002-14.503c4.698,2.081,8.631,5.198,13.503,8.001c3.752,2.16,11.111,4.273,13.503,8.002 c1.4,2.185,2.071,7.009,3.001,10.503c2.963,11.132,5.162,22.878,6.501,35.509c3.465,4.703,9.131,7.205,7.501,17.003 c0.317,1.685-0.644,4.646,0.5,5.501c0,2.834,0,5.668,0,8.502c0.317,1.684-0.644,4.646,0.5,5.502 C428.942,1035.032,428.942,1037.699,428.942,1040.367z',
    'Gwanak-gu': 'M694.002,1210.906 c-4.465,4.478-9.49,9.332-15.003,14.503c-4.905,4.601-9.783,10.629-15.504,14.503c-5.579,3.778-12.692,6.807-20.004,11.003 c-6.592,3.783-13.672,10.269-20.004,11.503c-4.604,0.896-10.121,0.33-15.004,0.5c-4.958,0.172-10.222,0.289-15.503,0.5 c-8.362,0.334-23.305,2.309-31.007,1c-6.05-1.028-10.412-7.248-16.503-9.502c-4.21-6.324-3.53-20.211-8.502-25.006 c-4.548-4.387-12.656-2.06-19.505-5.002c8.225-5.589-0.902-17.209-2-25.506c-3.932-2.236-8.583-3.752-13.503-5 c-1.827-5.164-8.366-9.896-10.002-15.504c-1.574-5.393-1.633-13.365-2.5-20.505c-0.814-6.708-1.677-13.724-2.5-20.505 c-2.561-21.088-3.9-39.887-4.001-58.013c-4.064-2.271-8.587-4.082-13.003-6.001c-2.081-4.088-4.802-7.535-6.501-12.004 c11.121-1.375,18.907-12.017,27.506-14.502c11.104-3.211,22.65,0.189,31.007-7.002c2.241-5.428,4.121-11.217,6.502-16.504 c6.001,0,12.002,0,18.004,0c4.67-0.331,10.665,0.663,14.503-0.5c6.668,0,13.337,0,20.005,0c8.168,0,16.337,0,24.506,0 c10.86,4.143,21.86,8.146,32.007,13.002c4.08,15.592,8.194,31.148,12.003,47.012c8.873,7.131,17.411,14.596,26.506,21.504 c2.674,1.161,7.497,0.172,11.003,0.501c1.848-0.319,4.981,0.647,6.001-0.501c9.897,1.395,14.094-5.574,20.004-6.501 c-1.588,13.631,1.807,20.663,6.002,29.007c7.521,14.96,15.389,32.314,24.006,43.51c-3.738,0.93-8.176,1.16-11.502,2.5 C718.799,1185.691,706.297,1198.194,694.002,1210.906z',
    'Gwangjin-gu': 'M968.564,871.328 c2.316-14.521,7.197-26.477,7.502-43.01c8.822-11.86,18.256-21.808,24.006-37.008c5.83-15.418,6.246-33.637,11.002-51.012 c2.184-7.975,6.811-15.682,10.002-23.506c3.381-8.283,5.756-16.752,10.002-23.505c13.057-2.375,26.496,0.72,38.01-2.501 c10.371-2.901,19.273-13.59,34.508-10.502c3.646-0.813,1.371,4.297,2,6.501c-1.842,8.604-12.258,15.216-13.002,24.006 c-0.449,5.296,4.145,11.839,5.5,17.504c2.047,8.548-0.248,18.539,5.502,24.005c2.166,0,4.334,0,6.502,0 c1.678,1.156,5.492,0.176,8.002,0.5c2.834,0,5.668,0,8.502,0c-2.664,6.541-4.318,12.607-5.502,20.005 c-1.111,6.94-1.309,15.409-3.5,22.505c-2.055,6.65-7.506,15.23-11.504,23.006c-3.957,7.696-9.898,14.141-14.502,20.004 c-4.736,6.031-10.125,12.135-15.504,19.005c-4.619,5.896-10.355,16.442-15.504,19.005c-6.918,3.441-16.094,3.563-25.006,4 c-2.334,0-4.668,0-7.002,0c-3.334,0-6.668,0-10.002,0c-1.52,0.314-4.309-0.641-5.002,0.5 C995.094,881.143,980.262,877.802,968.564,871.328z',
    'Gangnam-gu': 'M809.529,885.332 c3.344-3.51,7.164-6.168,11.002-9.502c3.58-3.111,6.844-7.933,11.002-9.503c3.145-1.187,8.471-1.302,12.504-2.001 c10.121-1.754,17.617-2.169,29.006-1.5c15.992,0.939,26.063,3.173,37.008,8.002c26.447,11.669,47.611,32.773,78.518,36.008 c1.088,2.58,0.227,7.111,0.502,10.503c0,3.334,0,6.668,0,10.003c0.32,2.012-0.65,5.317,0.5,6.501c0,2.834,0,5.668,0,8.502 c0,3.334,0,6.668,0,10.002c0.318,1.849-0.648,4.981,0.5,6.001c-1.123,9.712,2.514,13.063,5.5,19.005 c1.992,3.962,3.227,8.579,5.002,11.003c2.117,2.891,7.289,6.038,11.002,9.002c3.914,3.123,7.424,6.66,11.504,9.002 c4.016,2.305,9.201,4.087,14.002,6.501c4.635,2.33,9.215,5.753,13.504,7.002c12.207,3.557,27.744,3.449,39.508,7.001 c2.563,3.273,5.932,5.738,8.502,9.002c1.391,4.277,2.246,9.092,4.002,13.004c9.594,7.076,20.686,12.655,30.006,20.004 c0.875,6.341,2.055,12.245,3.002,18.004c0.984,6.002,1.041,12.711,3,18.004c1.092,2.949,4.381,6.836,6.502,10.003 c9.504,14.194,19.082,27.798,25.006,44.511c-5.541,1.964-14.252-0.62-20.006,2c-4.949,2.254-6.008,10.943-10.002,16.004 c-2.055,2.604-7.475,6.924-11.002,8.002c-5.742,1.754-12.551,0.227-18.004,1.5c-4.266,0.995-7.09,4.738-11.002,5.501 c-4.352,0.849-9.047-0.96-15.004-0.5c-4.955-4.302-7.02-10.321-12.504-15.003c-4.303-3.674-10.471-5.346-14.502-9.002 c-2.607-2.365-9.137-11.533-10.504-15.504c0-2.668,0-5.335,0-8.002c-0.313-1.52,0.641-4.308-0.5-5.002 c0.389-4.389-0.732-7.268-0.5-11.502c-4.393-2.852-7.592-7.5-13.002-9.502c-14.945-5.529-33.725,0.521-46.012,6.001 c-4.537,7.298-8.525,15.146-14.002,21.505c-16.326,0.345-28.281,5.061-40.51,9.502c-12.758-17.582-15.137-45.543-21.004-70.016 c-4.961-5.376-10.129-10.544-15.504-15.504c-6.775-3.227-14.982-5.021-20.006-10.002c-2.291-2.709-4.791-5.21-7.5-7.502 c-0.178-0.656-0.881-0.786-1.002-1.5c-2.125-2.543-4.457-4.877-7-7.002c-7.23-7.581-6.807-23.209-8.002-38.508 c-0.609-7.787,0.043-16.854-2.002-23.006c-2.199-6.624-12.922-11.019-15.502-18.004c-2.012-5.447-1.949-13.102-3.002-19.504 c-1.125-6.854-1.74-14.938-3.5-20.506c-2.148-6.789-6.955-12.942-10.002-19.004C815.576,898.467,812.641,891.889,809.529,885.332z',
    'Jongno-gu': 'M720.008,682.285 c-13.643,8.694-29.509,15.167-47.511,19.505c-6.417-5.753-13.255-11.084-19.004-17.504c-1.625-2.043-3.458-3.876-5.501-5.501 c-4.496-4.668-9.113-12.79-9.002-20.004c0.059-3.846,2.49-9.455,4.001-14.504c3.006-10.049,6.144-18.898,9.002-28.506 c1.488-5.001,3.81-10.584,4.001-15.003c0.284-6.57-2.044-13.576-3.501-19.504c-1.444-5.877-3.262-11.502-5.001-18.004 c-5.435-2.734-11.846-4.491-16.504-8.002c0-0.667,0-1.333,0-2c1.153-1.348,0.177-4.823,0.5-7.001c0-3.667,0-7.335,0-11.002 c0-3.334,0-6.668,0-10.002c1.152-1.348,0.177-4.824,0.5-7.001c0-3.668,0-7.335,0-11.003c0-3.334,0-6.668,0-10.002 c1.152-1.348,0.177-4.824,0.5-7.002c0-3.667,0-7.335,0-11.002c0.908-12.657,0.718-22.671,4.501-31.007 c3.217-7.087,11.845-11.738,16.504-18.504c3.345-4.858,4.499-9.644,8.002-14.503c12.175-1.161,22.316-4.356,34.508-5.501 c4.252,4.75,9.984,8.02,15.503,11.502c6.941,16.762,8.516,34.323,13.504,52.012c5.047,17.906,16.736,33.542,17.004,55.512 c-2.867,5.802-5.285,12.053-8.002,18.004c-5.268,1.902-10.518,3.819-15.504,6.001c-0.797,6.729-2.854,14.783-2,21.505 c9.846,8.849,27.449,6.847,42.51,11.002c7.674,2.118,13.383,6.375,20.004,8.002c11.162,22.178,19.406,47.276,35.008,65.015 c13.176,0.995,26.723,1.617,39.51,3c0,1.167,0,2.334,0,3.501c-1.16,2.341-0.172,6.829-0.5,10.002c0,4.334,0,8.668,0,13.003 c-4.334,0-8.67,0-13.004,0c-1.848,0.319-4.982-0.648-6.002,0.5c-3.166,0-6.334,0-9.502,0c-1.848,0.319-4.98-0.647-6,0.5 c-3.002,0-6.002,0-9.002,0c-2.014,0.321-5.318-0.65-6.502,0.5c-12.729,0.941-23.139,4.201-35.508,5.501c-8.336,0-16.672,0-25.006,0 c-4.959-3.876-8.289-9.382-14.004-12.503C726.826,681.454,721.691,681.454,720.008,682.285z',
    'Jung-gu': 'M852.039,722.795 c-3.846,7.49-7.141,15.533-13.504,20.504c-2.877,2.459-5.543,5.126-8.002,8.002c-0.404,0.096-0.498,0.502-1,0.5 c-2.299,2.201-5.035,3.969-6.502,7.002c-4.045,11.457-9.639,21.369-14.502,32.008c-7.918-3.086-13.068-8.938-21.506-11.503 c-1.02-1.147-4.152-0.181-6.002-0.501c-3,0-6,0-9.002,0c-1.02-1.146-4.152-0.18-6-0.5c-17.834,2.664-19.93-10.41-29.508-16.003 c-2.342-1.159-6.83-0.173-10.002-0.501c-2.344,0.324-5.988-0.653-7.502,0.501c-3.334,0-6.668,0-10.002,0 c-2.676,0.325-6.659-0.657-8.502,0.5c-13.527,1.144-27.306,2.034-40.51,3.501c-3.02-13.316-7.289-25.385-12.002-37.009 c9.184-7.319,19.5-13.507,25.005-24.505c12.842-3.283,24.639-6.314,35.509-12.003c6.629-3.47,9.121-8.251,19.504-7.001 c3.959,4.376,8.127,8.544,12.504,12.503c24.184,3.167,42.299-4.035,63.514-5.501c1.848-0.319,4.982,0.648,6.002-0.5c3,0,6,0,9.002,0 c2.012-0.321,5.318,0.65,6.5-0.5c3.002,0,6.002,0,9.004,0c2.012-0.32,5.316,0.65,6.5-0.5c3.002,0,6.002,0,9.002,0 c0.834,0,1.668,0,2.502,0c0,2.834,0,5.668,0,8.502c0,3.001,0,6.002,0,9.002c-1.148,1.02-0.182,4.154-0.5,6.002 C852.039,717.46,852.039,720.127,852.039,722.795z',
    'Jungnang-gu': 'M1120.1,474.238 c4.662,7.841,18.324,6.681,27.006,10.502c-2.287,12.067,11.305,15.833,13.004,24.506c1.773,9.058-4.662,14.91-5.502,24.005 c-4.322,4.18-9.531,7.472-13.004,12.502c0,0.334,0,0.667,0,1c-0.832,3.84-0.832,11.164,0,15.003c0.725,3.943,3.436,5.9,4.502,9.502 c0,3.334,0,6.668,0,10.002c-10.604,7.43-3.115,20.208-6.502,32.507c-1.73,6.284-15.746,14.712-19.004,20.505 c-1.561,2.773-1.363,6.369-2.5,8.502c-3.455,6.476-15.533,10.445-16.504,17.504c-0.502,3.64,2.662,6.176,4,9.002 c0,2.334,0,4.668,0,7.002c-0.727,0.273-2.584-0.585-2.5,0.5c-16.391-2.901-23.908,8.259-36.008,11.002 c-11.813,2.678-22.666-0.268-36.51,2.5c-4.686-62.996-16.182-119.183-15.502-187.543c11.811-5.232,23.043-11.489,35.008-17.504 c6.033-3.034,10.494-5.943,19.504-5.501c1.684-0.317,4.645,0.645,5.502-0.5c2.666,0,5.334,0,8.002,0 c1.684-0.317,4.645,0.644,5.5-0.5c2.668,0,5.334,0,8.002,0c1.684-0.317,4.645,0.644,5.502-0.5 C1110.217,479.025,1114.6,476.073,1120.1,474.238z',
    'Mapo-gu': 'M402.936,583.763 c6.628,6.041,6.576,18.764,20.004,18.004c5.968,13.406,17.593,19.237,29.007,28.007c7.357,5.653,15.368,10.769,22.005,16.504 c12.672,10.951,22.254,25.911,33.508,36.008c5.59,5.017,12.528,8.948,18.004,14.503c2.292,2.709,4.792,5.21,7.502,7.501 c0.501-0.001,0.595,0.405,1,0.501c2.458,2.875,5.126,5.543,8.002,8.001c2.292,2.71,4.792,5.21,7.501,7.502 c0.501-0.001,0.595,0.405,1,0.5c2.292,2.71,4.792,5.21,7.501,7.502c2.202,2.3,3.968,5.034,7.001,6.501 c13.151,1.679,33.148,3.431,48.011,5.002c5.674,0.6,11.949,2.383,16.003,1.5c6.439-1.402,9.308-9.631,16.004-10.502 c1.58,5.924,4.993,11.412,7.001,17.504c1.616,4.899,5.107,15.426,5.001,20.004c-0.106,4.607-4.983,19.26-6.502,23.006 c-5.032,12.414-13.34,21.697-21.005,31.007c-10.313,12.528-22.811,24.427-34.008,34.007c-2.909-2.459-5.033-5.625-8.001-8.001 c-3.006-2.405-6-4.657-9.002-7.002c-5.841-4.562-11.654-9.769-18.004-13.503c-19.939-11.727-41.419-19.489-63.014-27.506 c-10.111-3.754-20.545-9.33-31.007-14.504c-10.133-5.011-21.63-9.223-30.507-15.003c-6.704-4.366-13.309-11.702-20.004-17.505 c-17.035-14.76-33.581-28.587-50.011-43.51c-3.288-2.985-7.931-6.23-10.002-9.001c-3.899-5.214-4.916-12.548-8.002-18.004 c-2.809-4.965-7.841-10.148-12.002-15.003c-6.841-7.979-12.272-16.188-21.505-20.505c0-0.167,0-0.333,0-0.5 c8.283-2.733,16.484-15.161,25.005-17.004c9.141-1.977,18.662,0.807,29.507-1c3.474-3.274,7.478-7.106,11.502-11.503 c3.063-3.346,9.383-8.809,10.502-12.503c0.708-2.338-0.158-4.918,0.5-7.001C393.398,591.544,399.557,589.176,402.936,583.763z',
    'Nowon-gu': 'M1083.592,305.699 c-0.51,4.537-3.662,5.613-4.502,10.002c-0.775,4.05,0.262,18.509,1.5,23.005c0.885,3.208,6.793,14.643,9.002,16.004 c5.967,3.672,16.053,1.403,23.506,3c8.109,1.738,14.93,5.567,22.006,7.501c2.74,11.227,0.236,20.766,3,30.507 c2.139,7.545,9.795,12.583,7.002,24.006c-3.18,4.49-5.305,10.033-9.002,14.003c-4.596,1.663-8.748,0.512-12.504,2.501 c-3.117,1.651-7.98,9.404-8.502,14.003c-0.748,6.625,2.275,12.594,6.002,16.504c-0.713,1.287-1.615,2.386-1.5,4.501 c-5.883,1.452-10.193,4.478-18.504,3.501c-1.684,0.316-4.646-0.645-5.502,0.5c-2.668,0-5.334,0-8.002,0 c-1.684,0.316-4.645-0.645-5.502,0.5c-2.666,0-5.334,0-8.002,0c-1.684,0.316-4.645-0.645-5.5,0.5 c-12.211,0.197-21.813,7.38-31.008,12.003c-8.523,4.285-16.541,7.522-24.006,12.003c-11.572-2.931-23.703-5.303-35.008-8.502 c-17.121-22.554-34.861-47.157-48.51-71.517c6.645-0.522,14.094-0.241,20.004-1.5c3.156-9.429,1.619-20.374,3.5-29.507 c1.412-6.854,5.945-13.942,7.502-21.505c2.24-10.87,1.836-24.517,3.002-37.009c0.533-5.733,1.441-11.911,2-18.004 c0.537-5.876,1.732-12.743,1-17.504c-1.043-6.773-5.283-14.015-8.002-21.005c-2.588-6.651-7.232-14.544-8.002-21.005 c-0.441-3.712,0.98-8.423,1.5-13.003c0.482-4.25,0.182-9.028,1-13.003c2.141-10.392,9.006-21.348,11.504-33.008 c2.17-10.125,0.41-21.711,3-30.507c5.758-9.167,15.742-8.554,26.506-14.503c3.541-1.957,8.979-6.144,12.004-9.002 c3.121-2.951,5.645-8.404,8.502-9.502c3.232-1.244,10.299-1.729,14.002-1c3.074,0.605,6.523,4.829,10.002,7.502 c7.916,6.081,13.107,9.773,20.506,15.503c3.42,2.65,6.877,6.662,10.002,8.002c8.432,3.616,15.584,1.682,25.506,5.501 c4.303,15.436-2.008,19.555-8.002,28.506c-3.225,4.817-8.416,10.662-8.502,15.503c-0.078,4.409,5.43,13.495,7.502,16.504 c3.346,4.858,12.357,11.303,14.004,17.004c2.277,7.894-0.465,15.084,1.5,26.006c-3.293,4.746-15.668,6.805-18.004,12.003 c-1.809,4.022,0.287,9.412-0.5,16.003C1083.416,300.207,1082.436,304.021,1083.592,305.699z',
    'Seocho-gu': 'M806.527,888.332 c4.836,12.346,15.342,23.65,19.506,37.509c1.785,5.944,2.385,13.452,3.5,20.005c1.16,6.825,0.615,14.923,3,20.504 c2.943,6.886,13.414,11.089,15.504,17.504c2.102,6.455,0.887,15.961,1.5,24.006c1.275,16.718,2.035,32.602,11.004,41.01 c2.125,2.543,4.457,4.877,7,7.001c2.293,2.71,4.793,5.21,7.502,7.502c5.152,4.351,13.154,5.851,19.004,9.502 c4.793,5.21,9.795,10.211,15.004,15.003c6.199,24.977,8.586,53.764,22.506,71.018c12.578-3.768,27.584-9.42,44.51-10.503 c2.496-3.239,4.85-6.384,7.502-10.503c2.125-3.299,3.473-8.528,7.002-11.002c7.916-5.549,30.465-10.368,42.51-5.502 c3.742,1.513,5.676,5.699,9.002,7.002c1.172,0.827,1.082,2.918,1,5.002c0.313,1.52-0.641,4.307,0.5,5c0,2.168,0,4.335,0,6.502 c0.324,2.51-0.656,6.323,0.5,8.002c0,0.666,0,1.334,0,2c3.568,5.435,8.084,9.92,11.502,15.504 c10.914,5.424,19.607,13.066,26.006,23.006c-5.477,3.372-17.676,2.81-21.004,8.001c-2.535,3.952,0.248,8.76-0.5,14.003 c-0.789,5.527-5.758,10.554-5.502,15.504c-6.285,1.717-15.844,0.159-19.504,4.501c-2.889,8.221-3.168,16.153-10.502,20.005 c-4.592,2.41-14.232,3.557-20.506,5.001c-5.934,1.366-17.129,2.228-20.004,5.501c-3.428,3.902-2.934,13.344-4,19.004 c-1.336,7.069-2.83,12.834-4.502,18.505c-6.668,0-13.336,0-20.004,0c-8.256-0.581-14.83-2.841-23.006-3.501 c-9.994-6.312-21.668-14.544-32.508-21.505c-3.898-2.504-12.398-4.849-11.002-10.503c0.832-1.848,0.832-7.153,0-9.002 c-2.578-16.594-7.609-30.731-11.502-46.01c0-1.834,0-3.668,0-5.502c0-1.666,0-3.334,0-5c0-5.836,0-11.67,0-17.504 c1.156-1.845,0.174-5.828,0.5-8.502c-0.33-3.672,0.66-8.664-0.5-11.504c0.693-8.93-8.209-7.638-15.004-9.002 c-6.039-1.211-10.387-3.737-15.504-3c-4.658,0.671-11.443,6.838-16.504,10.002c-5.42,3.389-12.389,6.385-16.504,10.002 c-7.127,6.268-11.402,16.109-17.004,20.506c-4.719,0.825-12.785,0.825-17.504,0c-5.65-5.113-1.145-14.549-2.5-22.006 c-0.734-4.045-7.217-10.967-10.002-12.002c-2.207-0.821-8.766-1.677-13.004,0c-4.18,1.652-4.135,6.661-8.002,9.502 c-12.281-15.393-19.408-35.938-29.006-54.013c-0.332-6.669,0.664-14.668-0.5-20.505c-4.701-15.217-4.1-29.432-1.5-47.511 c1.387-9.655,2.531-19.489,4.5-27.006c1.068-4.075,4.164-8.104,4.002-12.003c-0.203-4.844-4.217-9.573-6.002-13.503 c-2.219-4.882-3.709-9.038-6.002-13.503c-7.08-1.088-14.927-1.41-23.005-1.5c-1.562-5.441-2.924-11.08-4.001-17.004 c19.89-0.508,35.938,1.268,50.012-4.502c6.559-2.688,13.355-10.818,19.004-16.004C772.443,924.47,790.729,906.379,806.527,888.332z',
    'Seodaemun-gu': 'M651.993,687.787 c5.392,5.944,12.344,10.328,17.504,16.503c-9.036,16.137-28.058,22.287-41.509,34.008c-13.573-1.495-30.166-3.397-45.511-5.001 c-8.969-0.938-16.812-0.158-20.504-6.002c-2.458-2.875-5.125-5.543-8.002-8.001c-2.292-2.71-4.792-5.21-7.501-7.502 c-0.405-0.095-0.499-0.501-1-0.5c-2.459-2.876-5.126-5.544-8.002-8.002c-2.292-2.709-4.792-5.21-7.502-7.502 c-13.18-8.723-25.485-21.902-36.508-34.008c-4.069-4.468-14.517-11.841-15.503-17.003c-0.5-2.617,1.692-16.675,3-20.004 c0.917-2.335,2.949-3.667,4.501-5.001c1.876-1.458,3.543-3.125,5.001-5.001c0-0.167,0-0.333,0-0.5 c2.043-1.624,3.876-3.458,5.501-5.501c1.278-1.556,2.11-3.558,5.501-3.001c5.502,0,11.003,0,16.504,0 c3.173-0.328,7.661,0.66,10.002-0.5c6.487,0.152,12.591-0.079,18.504-0.5c6.326-6.176,10.388-14.618,13.503-24.005 c10.889-2.615,19.731-7.274,29.507-11.002c2.503-10.667,6.462-19.877,11.502-28.007c8.276-3.82,13.486-11.91,27.006-9.002 c0,0.5,0,1,0,1.5c-1.161,2.673-0.171,7.497-0.5,11.002c0,3.834,0,7.669,0,11.503c4.386,4.449,11.701,5.969,17.504,9.002 c2.945,11.225,5.782,22.557,8.002,34.508c-4.303,13.218-8.536,28.992-13.003,44.01c-1.528,5.135-4.425,10.297-4.501,14.504 c-0.115,6.354,4.344,11.713,5.501,18.004c1.625,2.043,3.458,3.876,5.501,5.501C648.116,684.328,649.95,686.162,651.993,687.787z',
    'Seongbuk-gu': 'M729.51,392.219 c4.775,3.428,6.586,11.528,11.504,15.003c6.434,4.547,15.797,4.11,23.004,8.002c4.41,2.381,8.689,6.688,13.004,10.002 c13.127,10.086,26.316,20.602,38.508,30.507c3.721,15.952,7.818,31.525,11.504,47.511c6.191,3.309,10.23,8.773,21.004,7.501 c3.502,0,7.002,0,10.502,0c2.014-0.321,5.318,0.65,6.502-0.5c3.168,0,6.336,0,9.502,0c3.502,0,7.002,0,10.502,0 c2.014-0.321,5.318,0.65,6.502-0.5c14.43,1.926,18.871-6.135,27.506-10.002c8.346-16.495,17.166-32.512,26.006-48.511 c5.18,6.521,9.941,14.057,15.504,22.005c3.65,5.213,11.018,18.608,16.504,21.505c9.67,5.107,23.432,3.499,34.508,9.002 c0,2.334,0,4.667,0,7.001c-1.166,7.836-0.168,17.837-0.5,26.506c-24.066,7.941-54.93,9.086-72.016,24.006 c-3.877,3.459-7.545,7.126-11.004,11.002c-5.734,8.601-10.057,18.616-15.502,27.506c-14.291,4.829-22.807,10.759-33.508,19.004 c-3.418,2.634-8.207,4.543-10.002,7.502c-3.072,5.061-2.602,13.886-5.502,20.005c-1.129,2.381-7.93,10.95-10.002,11.502 c-5.072,1.351-12.443-1.066-18.504-1.5c-6.207-0.444-12.348-0.835-19.006-1c-15.918-17.589-23.059-43.956-35.508-65.015 c-7.059-1.465-12.77-6.458-21.004-8.502c-14.41-3.577-30.959-1.897-40.01-9.502c0.285-6.216,1.23-11.773,2-17.504 c5.316-1.52,10.008-3.664,15.004-5.501c1.957-7.628,7.678-12.839,8.502-20.505c1.564-14.538-5.063-26.023-9.002-36.508 c-2.35-6.257-5.504-12.853-7.502-19.505c-5.582-18.59-7.576-37.961-14.002-53.512c-4.844-3.493-10.78-5.892-14.504-10.503 c6.551-1.802,13.174,0.948,18.004-1C724.17,405.237,724.545,395.769,729.51,392.219z',
    'Seongdong-gu': 'M874.543,701.29c2.168,0,4.334,0,6.502,0 c5.318-3.184,4.223-12.782,14.504-11.002c0.693,1.141,3.48,0.187,5,0.5c9.426-0.09,18.045,0.627,27.006,1 c2.555,7.281,4.742,14.93,7.002,22.506c24.572,8.603,50.193,16.154,73.018,26.506c-4.559,27.309-8.412,49.525-20.506,68.516 c-4.107,6.451-11.859,11.41-14.002,18.004c-1.691,5.203-0.707,11.029-1.5,16.004c-1.453,9.091-4.732,16.918-6.502,26.006 c-21.135-10.752-42.318-26.766-69.516-33.008c-5.611-1.287-13.537-1.091-20.506-1.5c-20.389-1.197-34.918,0.928-52.512,5.001 c-3.926-15.579-7.852-31.157-12.002-46.511c6.348-10.322,10.541-22.799,16.504-33.508c2.709-2.291,5.209-4.792,7.502-7.502 c2.875-2.458,5.543-5.125,8-8.002c5.986-5.184,8.691-13.647,13.004-20.504c1.156-1.679,0.176-5.493,0.5-8.002 c0-2.834,0-5.668,0-8.502c1.15-1.184,0.18-4.489,0.5-6.501c0-2.5,0-5.001,0-7.501c4.824,2.01,6.006,7.664,13.004,7.501 C870.236,701.93,873.023,700.976,874.543,701.29z',
    'Songpa-gu': 'M1142.104,808.314 c3.291,2.545,5.451,6.219,8.002,9.502c3.914,0.421,8.018,0.652,12.504,0.5c6.393,10.426,0.719,26.423-3.502,37.508 c-1.508,3.963-4.914,9.193-4.5,12.004c0.85,5.775,13.672,9.944,16.004,15.003c4.35,8.153,6.883,18.123,12.002,25.506 c5.994,2.008,14.322,1.683,21.506,2.501c5.795,5.874,12.436,10.902,23.504,11.502c-2.807,6.306-6.541,11.023-10.502,17.504 c-2.289,3.744-10.203,13.9-9.502,19.004c0.586,4.262,7.207,5.046,8.502,10.003c1.482,5.668-1.051,8.273,0,15.503 c10.061,14.612,33.895,15.451,51.512,22.506c2.176,1.158,6.496,0.173,9.502,0.5c1.162,3.505,0.17,9.166,0.5,13.503 c-11.891,3.552-17.787,8.655-21.004,20.505c-2.311,8.503-1.09,19.406-4.002,27.506c-2.273,6.328-7.365,9.609-10.002,14.504 c-5.816,2.695-12.551,1.388-16.504,5.001c-2.094,1.915-5.465,12.249-7.002,17.504c-1.119,3.833,1.193,8.827,0.5,12.003 c-0.814,3.739-5.057,5.746-7,8.002c-2.51,0.324-6.324-0.656-8.002,0.5c-7.412,0.801-13.502-1.392-18.004,1 c-3.674,1.951-5.508,9.667-7.502,14.003c-2.639,5.731-4.676,10.651-7.002,14.503c-5.922-14.547-13.602-28.167-22.006-40.509 c-2.869-4.215-7.066-8.269-8.502-12.503c-3.799-11.217-2.223-24.072-6.5-36.508c-9.463-7.208-20.561-12.782-30.008-20.005 c-1.467-4.368-2.533-9.136-4-13.503c-2.793-3.209-5.793-6.21-9.002-9.002c-13.086-4.025-29.4-3.43-41.51-7.502 c-4.102-1.379-8.607-4.791-13.004-7.002c-10.031-5.043-16.197-8.297-24.006-14.503c-3.416-2.716-8.016-5.854-10.002-8.502 c-4.135-5.515-5.576-12.743-9.502-19.505c-0.322-2.178,0.652-5.652-0.5-7.001c0-2.834,0-5.668,0-8.502c0-3.335,0-6.669,0-10.003 c-0.318-1.848,0.648-4.981-0.5-6.001c0-3.001,0-6.001,0-9.002c0-3.334,0-6.668,0-10.003c-0.318-1.848,0.648-4.981-0.5-6 c0-0.834,0-1.668,0-2.502c3.814,0.688,7.262,1.74,12.002,1.501c2.334,0,4.668,0,7.002,0c3.334,0,6.668,0,10.002,0 c1.521-0.313,4.309,0.64,5.002-0.5c4.5,0,9.002,0,13.504,0c1.684-0.317,4.645,0.645,5.5-0.5c8.596-2.126,18.393-2.595,27.006-6.001 c7.645-3.023,11.229-8.839,16.004-15.004c8.344-10.771,16.424-21.131,24.506-31.508c5.502-7.063,12.068-13.736,16.004-21.004 C1133.998,825.777,1137.637,816.13,1142.104,808.314z',
    'Yangcheon-gu': 'M199.39,804.313 c2.805-5.029,4.873-10.797,8.502-15.003c6.626,0.688,11.923-2.626,17.004-1.501c2.33,0.518,7.231,5.06,8.502,7.002 c1.986,3.038,3.1,8.013,5.001,12.504c4.896,11.561,11.807,26.589,15.504,37.008c6.607,6.648,15.021,9.528,27.006,12.002 c5.238,1.082,10.646,2.888,15.003,2.502c10.121-0.897,18.733-8.26,27.006-10.503c2.177-9.443,12.685-18.485,15.003-27.007 c2.679-9.844-0.558-21.725,2.001-30.506c1.62-5.564,5.628-8.434,8.502-13.004c6.39,0.445,10.828,2.842,16.003,4.502 c5.52,1.48,14.479-0.477,20.004,1c6.588,12.133,10.032,26.038,16.504,38.008c1.717,3.177,5.399,5.818,6.501,9.503 c1.723,5.761-0.143,11.556,1.5,17.504c-8.795,8.376-18.912,15.43-27.507,24.005c0,5.002,0,10.003,0,15.004 c-1.163,4.171-0.169,10.5-0.5,15.504c0,8.335,0,16.67,0,25.006c-2.254,12.416-8.858,20.48-13.003,31.006 c-0.757,1.244-4.308-0.307-5.001,1.001c-14.251,0.537-29.897,2.867-38.009-4.501c-2.647-7.188-4.854-14.817-7.501-22.006 c-4.339-0.828-9.2-1.135-14.503-1c-0.856-1.145-3.817-0.184-5.501-0.5c-2.501,0-5.001,0-7.502,0 c-10.922,8.749-19.826,19.518-32.007,27.006c-4.167,0-8.335,0-12.502,0c-2.344,0.324-5.989-0.653-7.502,0.5c-2,0-4.001,0-6.001,0 c-1.747-5.422-3.626-10.71-6.001-15.503c-9.487-1.849-19.228-3.443-29.007-5.001c1.961-7.553,11.313-18.918,12.503-27.006 c1.75-11.883,0-27.833,0-43.01c0-15.213,2.144-31.803,0-43.51C207.808,816.662,200.691,810.254,199.39,804.313z',
    'Yeongdeungpo-gu': 'M385.432,872.828 c3.914-3.196,8.258-7.184,13.503-11.502c3.549-2.922,11.847-7.633,13.003-12.003c1.547-5.849-1.958-11.689-1-19.505 c-7.127-8.824-10.49-18.873-15.503-30.506c-4.992-11.584-10.961-20.336-10.503-36.009c12.586,8.73,21.889,21.758,35.008,30.507 c6.313,4.211,14.327,6.894,21.505,10.502c21.057,10.588,42.679,22.491,66.516,29.508c4.151,20.021,4.892,40.504,12.503,56.012 c5.411,11.027,16.118,15.754,25.506,22.506c0,0.166,0,0.333,0,0.5c-9.933,15.573-19.521,31.49-27.006,49.512 c0.1,21.104-4.123,37.887-11.002,52.012c-9.291,2.553-22.559,2.559-30.507,6.501c-2.952,1.464-13.389,10.814-14.503,13.503 c-3.051,7.357-0.555,17.23-4.001,26.006c-2.515,0.152-3.951,1.384-6.001,2.001c-6.938-4.23-13.496-8.843-20.004-13.503 c0-1.168,0-2.334,0-3.501c-0.317-1.685,0.644-4.646-0.5-5.501c0-2.668,0-5.335,0-8.002c-0.316-1.684,0.645-4.646-0.5-5.502 c0-2.834,0-5.668,0-8.502c-0.316-1.684,0.645-4.645-0.5-5.501c2.547-10.949-5.78-12.446-8.002-19.005 c-2.365-6.984-2.019-15.568-4.001-23.505c-1.954-7.823-3.791-15.308-5.501-22.505c-9.452-5.885-19.485-11.189-29.007-17.004 c0-8.502,0-17.004,0-25.506c0-7.335,0-14.67,0-22.005C386.091,880.49,385.104,876.002,385.432,872.828z M597.48,900.335 c-20.856-23.653-42.328-46.692-74.017-59.514c2.068,15.437,4.544,30.464,9.502,43.01c5.697,5.112,13,14.698,21.004,17.004 c10.17,2.929,21.25,0.715,34.008,1.001C589.714,900.138,598.159,902.816,597.48,900.335z',
    'Yongsan-gu': 'M737.012,767.305 c8.662,5.808,11.73,17.15,28.506,14.504c1.02,1.146,4.154,0.18,6.002,0.5c3.002,0,6.002,0,9.002,0c1.02,1.146,4.154,0.181,6.002,0.5 c8.139,3.197,14.717,7.955,21.504,12.502c2.967,15.711,8.664,31.341,11.004,46.011c-17.625,10.849-33.219,28.17-48.512,45.011 c-11.588,12.76-23.234,22.858-35.508,34.508c-7.422,7.045-10.861,11.924-22.006,13.003c-12.885,1.247-36.42,1.669-46.01-2.001 c-5.798-2.218-10.817-9.58-15.003-14.503c-4.786-5.628-9.421-10.618-13.503-16.004c-2.026-3.475-5.16-5.843-8.001-8.502 c-2.187-0.646-3.144-2.523-4.501-4.001c-1.958-2.376-4.125-4.543-6.501-6.501c-1.792-2.21-3.792-4.21-6.001-6.001 c-5.487-5.35-10.367-11.305-15.503-17.004c25.86-24.984,53.596-48.095,62.514-90.021c13.819-0.684,26.499-2.507,40.509-3 c2.834,0,5.668,0,8.502,0c2.344-0.324,5.988,0.654,7.502-0.5c3.334,0,6.668,0,10.002,0 C730.596,766.053,735.563,764.919,737.012,767.305z',
}

# Each region's drill-down map: path data, SVG viewBox, transit-authority intro line,
# Wikimedia source credit, and which districts have a published guide — id -> list of
# (short label, post slug, how to get there by transit). A district can list more than
# one place (e.g. Jongno-gu has several). The "how to get there" text must match what
# the linked post itself says. Add entries (and ship the post) to light up more of a
# map — see project_landinkorea_operating_baseline_2026_10_22 memory for rollout order.
REGION_MAPS = {
    'busan': {
        'paths': BUSAN_DISTRICT_PATHS,
        'viewbox': '-5.5 4.5 600 450',
        'label': 'Busan',
        'intro': ('Busan Metro takes the same T-money card as Seoul, on its own fare table — '
                   'base fare around 1,600 won on a transit card '
                   '(<a href="https://work.humetro.busan.kr" rel="noopener">Busan Transportation Corporation</a>).'),
        'source_url': 'https://commons.wikimedia.org/wiki/File:Busan_districts.svg',
        'source_file': 'Busan_districts.svg',
        'info': {
            'Haeundae-gu': [('Haeundae Beach', 'korea-busan-haeundae-beach-gamcheon-culture-village',
                              'Haeundae Station, Busan Metro Line 2 — easy walk to the beach.')],
            'Saha-gu': [('Gamcheon Culture Village', 'korea-busan-haeundae-beach-gamcheon-culture-village',
                         'Toseong Station (Line 1), then village bus 1-1 or 2 (~1,400 won) or a short taxi up the hill.')],
            'Suyeong-gu': [('Gwangalli Beach', 'korea-busan-fireworks-festival-gwangalli-guide',
                            'Gwangan Station, Busan Metro Line 2 — a short walk from the sand.')],
        },
    },
    'seoul': {
        'paths': SEOUL_DISTRICT_PATHS,
        'viewbox': '0 0 1400 1400',
        'label': 'Seoul',
        'intro': ('Seoul Metro base fare is around 1,550 won on a transit card (confirm current fare before you budget '
                   'tightly) — see our <a href="/posts/korea-t-money-transit-card">T-money guide</a> for how fares and '
                   'transfers work.'),
        'source_url': 'https://commons.wikimedia.org/wiki/File:Seoul_districts.svg',
        'source_file': 'Seoul_districts.svg',
        'info': {
            'Jongno-gu': [
                ('Gyeongbokgung Palace', 'korea-gyeongbokgung-palace',
                 'Gyeongbokgung Station, Line 3, Exit 5 — right at the palace\'s main gate.'),
                ('Bukchon Hanok Village', 'korea-bukchon-hanok-village',
                 'Anguk Station, Line 3, Exit 2 — about a 10-minute walk uphill.'),
                ('Insadong', 'korea-insadong-traditional-culture-street',
                 'Anguk Station, Line 3, Exit 6 — straight onto Insadong-gil.'),
            ],
            'Jung-gu': [
                ('Myeongdong Shopping District', 'korea-myeongdong-shopping-district',
                 'Myeongdong Station, Line 4, Exit 6 — right into the main shopping street.'),
                ('Namdaemun Market', 'korea-namdaemun-market',
                 'Hoehyeon Station, Line 4, Exit 5 — a short walk to the market gate.'),
            ],
            'Mapo-gu': [('Hongdae Nightlife District', 'korea-hongdae-nightlife-and-youth-culture-district',
                         'Hongik Univ. Station, Line 2, Exit 9 — the main Hongdae strip.')],
            'Yongsan-gu': [('N Seoul Tower & Namsan', 'korea-n-seoul-tower-namsan',
                            'Myeongdong Station, Line 4, then the Namsan cable car or a cablecar-adjacent bus up the hill.')],
        },
    },
}


def region_district_map(base, region_key, available_slugs):
    region = REGION_MAPS[region_key]
    parts, choices, cards = [], [], []
    for district_id, d in region['paths'].items():
        entries = [e for e in region['info'].get(district_id, []) if e[1] in available_slugs]
        name = district_id.replace('-gu', '').replace('-gun', '').replace('-si', '')
        if entries:
            district_label = entries[0][0] if len(entries) == 1 else name
            parts.append(
                f'<path class="region-district is-active" data-map-region="{district_id}" tabindex="0" '
                f'role="button" aria-label="Preview {html.escape(district_label)}" d="{d}">'
                f'<title>{html.escape(district_label)} — {html.escape(name)}</title></path>'
            )
            choices.append(
                f'<button type="button" class="region-preview-btn" data-preview-region="{district_id}" '
                f'aria-pressed="false">{html.escape(district_label)}</button>'
            )
            places = ''.join(
                f'<div class="region-place"><h4>{html.escape(label)}</h4>'
                f'<p class="region-transit">🚇 {html.escape(transit)}</p>'
                f'<a class="region-cta" href="{base}/posts/{html.escape(slug, quote=True)}">View guide <span aria-hidden="true">→</span></a></div>'
                for label, slug, transit in entries
            )
            cards.append(
                f'<article class="region-preview-card" data-preview-card="{district_id}">'
                f'<h3>{html.escape(name)}</h3>{places}</article>'
            )
        else:
            parts.append(
                f'<path class="region-district is-muted" d="{d}" aria-hidden="true">'
                f'<title>{html.escape(name)} — guide coming soon</title></path>'
            )
    return (
        f'<div class="region-map region-explorer"><p class="map-title">Explore {html.escape(region["label"])} by district</p>'
        f'<p class="map-intro">{region["intro"]}</p>'
        f'<svg viewBox="{region["viewbox"]}" role="group" aria-label="Map of {html.escape(region["label"])}\'s districts — select a '
        'highlighted district to preview it">'
        + ''.join(parts) +
        '</svg><div class="region-preview-choices" hidden>'+''.join(choices)+'</div>'
        '<div class="region-preview-cards">'+''.join(cards)+'</div>'
        '<p class="map-note">Highlighted districts have a guide — the rest are shown for '
        'orientation while more are written.</p>'
        f'<small class="map-credit">Map: <a href="{region["source_url"]}" '
        f'rel="noopener">Wikimedia Commons</a> (CC BY-SA 3.0, Kurykh)</small></div>'
    )


def destination_map(base):
    pins = []
    for key, label, subtitle, x, y in [('seoul','Seoul','CITY & CULTURE',124.5,129.0), ('busan','Busan','COAST & MARKETS',271.3,327.0), ('jeju','Jeju','ISLAND ESCAPES',93.2,466.4)]:
        pins.append(f'<a class="destination-pin" data-map-region="{key}" href="{base}/destinations/{key}" aria-label="Explore {label}" tabindex="0">'
                    f'<rect class="pin-hit" x="{x-22}" y="{y-25}" width="165" height="52" rx="4"/>'
                    f'<path class="pin-leader" d="M{x+14},{y}H{x+24}"/>'
                    f'<rect class="pin-label" x="{x+22}" y="{y-24}" width="124" height="49" rx="4"/>'
                    f'<circle class="pin-wave" cx="{x}" cy="{y}" r="18" aria-hidden="true"/>'
                    f'<circle class="pin-halo" cx="{x}" cy="{y}" r="13"/>'
                    f'<circle class="pin-dot" cx="{x}" cy="{y}" r="5"/>'
                    f'<text class="pin-name" x="{x+33}" y="{y+7}">{label}</text>'
                    '</a>')
    dokdo = ('<a class="map-island dokdo-marker" href="{base}/posts/korea-ulleungdo-island-guide" aria-label="Ulleungdo: view guide">'
             '<circle class="island-touch" cx="397" cy="134" r="30"/>'
             '<circle cx="397" cy="134" r="5"/><text x="391" y="113" text-anchor="middle">Ulleungdo</text></a>'
             '<a class="map-island dokdo-marker" href="#dokdo-note" data-island-note aria-label="Dokdo: view island information">'
             '<circle class="island-touch" cx="467" cy="156" r="38"/>'
             '<circle class="island-wave" cx="467" cy="156" r="13"/>'
             '<circle cx="464" cy="155" r="3.5"/><circle cx="470" cy="158" r="3"/>'
             '<text x="467" y="185" text-anchor="middle">Dokdo</text>'
             '<text x="467" y="204" text-anchor="middle" class="island-korean">독도</text></a>').format(base=base)
    return ('<div class="korea-map"><div class="atlas-heading"><p class="map-title">Choose a destination</p></div>'
            '<svg viewBox="0 0 540 510" role="group" aria-labelledby="korea-map-title korea-map-desc">'
            '<title id="korea-map-title">Explore South Korea</title>'
            '<desc id="korea-map-desc">Select Seoul, Busan or Jeju to preview a destination. Follow the View guide link to read its travel information. Ulleungdo and Dokdo are marked in the East Sea and link to their own guides; small island markers are enlarged for visibility.</desc>'
            '<path class="korea-coast-shadow" d="'+KOREA_MAP_PATH+'" transform="translate(0 4)" aria-hidden="true"/>'
            '<path class="korea-land" d="'+KOREA_MAP_PATH+'" aria-hidden="true"/>'
            +''.join(pins)+dokdo+'</svg><div class="atlas-footer"><p class="map-note">Select a place, then open its guide.</p></div>'
            f'<details id="dokdo-note" class="island-note"><summary>Dokdo · 독도</summary><p>Islands east of Ulleungdo, in the East Sea.</p>'
            f'<a href="{base}/posts/korea-dokdo-islets-visitor-guide">Read our Dokdo visitor guide <span aria-hidden="true">→</span></a> '
            '<a href="https://www.dokdo.re.kr/home/cms/cmsCont.do?cntnts_sn=63" rel="noopener" target="_blank">Official island information ↗</a></details>'
            '<small class="map-credit">Map: <a href="https://www.naturalearthdata.com/" rel="noopener">Natural Earth</a></small></div>')


def preparation_icon(index):
    paths = {
        1: '<rect x="11" y="5" width="26" height="38" rx="3"/><circle cx="24" cy="21" r="7"/><path d="M17 21h14M24 14c-4 5-4 9 0 14M24 14c4 5 4 9 0 14M17 35h14"/>',
        2: '<path d="m8 27 13-3 8-15c1-2 5-2 5 1l-4 14 10 7-1 4-12-4-7 8-4-1 4-10-11 3z"/>',
        3: '<path d="M14 5h14l8 8v30H12V7z"/><rect x="19" y="20" width="11" height="14" rx="2"/><path d="M22 20v14M19 25h11M19 30h11"/>',
        4: '<rect x="5" y="11" width="38" height="27" rx="4"/><path d="M5 20h38M12 30h8m9-3 3 3-3 3m6-9 3 6-3 6"/>',
        5: '<rect x="5" y="12" width="38" height="27" rx="4"/><path d="M5 20h38M12 30h7m10 0h6M11 7h25"/>',
        6: '<rect x="11" y="13" width="26" height="29" rx="4"/><path d="M19 13V7h10v6M18 21v13m12-13v13M17 42v3m14-3v3"/>',
    }
    return '<span class="prep-visual" aria-hidden="true"><svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'+paths[index]+'</svg></span>'


def discovery_home(cfg, base, posts):
    """Editorial travel homepage. All cards resolve to existing published guides."""
    lookup = {p['slug']: p for p in posts}

    def url(slug):
        return base + '/posts/' + html.escape(slug, quote=True)

    def photo(slug, alt='', eager=False, sizes=None):
        p = lookup.get(slug)
        return reviewed_photo_markup(p, base, eager=eager, sizes=sizes) if p else ""

    busan = 'korea-busan-haeundae-beach-gamcheon-culture-village'
    feature = 'korea-nami-island-and-petite-france-day-trip'
    hero = ''
    if feature in lookup:
        hero = (f'<a class="lead-story" href="{url(feature)}">'
                f'{photo(feature, eager=True, sizes="(max-width: 760px) calc(100vw - 40px), (max-width: 1240px) 64vw, 780px")}'
                '<div class="lead-caption"><span class="story-kicker">Nami Island, Gangwon-do</span>'
                '<h2>A day trip to Nami Island</h2>'
                '<span class="story-action">Ferry, transport and what to expect <span aria-hidden="true">→</span></span></div></a>')

    season = ''
    for slug, label, detail in [
        ('korea-timing-cherry-blossoms-and-fall-foliage-in-korea-without-hitting-peak-crowds', 'Blossoms & autumn colours', 'When to go, and how to avoid the busiest days.'),
        ('korea-seasonal-packing-weather-tips', 'Pack for the season', 'Weather, layers and what to bring.'),
        ('korea-korean-public-holidays-that-disrupt-travel-plans', 'Plan around public holidays', 'Check the dates that can change your trip.'),
    ]:
        if slug in lookup:
            season += f'<a class="season-pick" href="{url(slug)}"><h3>{label}</h3><p>{detail}</p><span aria-hidden="true">→</span></a>'

    destinations = ''
    for key, label, detail, slug in [
        ('seoul', 'Seoul', 'Palaces, neighbourhoods & city life', 'korea-gyeongbokgung-palace'),
        ('busan', 'Busan', 'Beaches, markets & coastal walks', busan),
        ('jeju', 'Jeju', 'Island landscapes & getting around', 'korea-jeju-island'),
    ]:
        count = sum(key in p['_regions'] for p in posts)
        if count:
            photo_slug = 'korea-busan-fireworks-festival-gwangalli-guide' if key == 'busan' else slug
            destinations += (f'<article class="place-card" id="destination-card-{key}" data-destination-card="{key}">{photo(photo_slug, sizes="(max-width: 760px) calc(100vw - 40px), (max-width: 1240px) 46vw, 540px")}'
                             f'<div><h3>{label}</h3><span>{count} guides</span></div><p>{detail}</p><a class="destination-cta" href="{base}/destinations/{key}">View {label} guide <span aria-hidden="true">→</span></a></article>')

    preparation = ''
    for i, (label, detail, slug) in enumerate([
        ('Entry requirements', 'Start before you book', 'korea-k-eta-visa-waiver-requirements'),
        ('Airport transfers', 'Choose your way into the city', 'korea-airport-transfer-options'),
        ('SIM & eSIM', 'Stay connected on arrival', 'korea-esim-mobile-data'),
        ('Transit cards', 'Get ready to move around', 'korea-t-money-transit-card'),
        ('Money & payments', 'Know when you need cash', 'korea-currency-card-payments'),
        ('Luggage storage', 'Make the most of arrival day', 'korea-luggage-storage-forwarding-services'),
    ], 1):
        if slug in lookup:
            preparation += (f'<a class="prep-link" href="{url(slug)}">{preparation_icon(i)}'
                            f'<div><h3>{label}</h3><p>{detail}</p></div><span class="prep-arrow" aria-hidden="true">↗</span></a>')

    interests = ''
    for label, detail, slug in [
        ('Food & local life', 'Ramyeon, shops and easy everyday meals', 'korea-convenience-store-hacks'),
        ('Culture & heritage', 'Insadong’s shops, galleries and traditional streets', 'korea-insadong-traditional-culture-street'),
        ('Day trips', 'Walk the walls of Suwon Hwaseong', 'korea-suwon-hwaseong-fortress'),
        ('Shopping', 'Myeongdong’s shops and evening market', 'korea-myeongdong-shopping-district'),
    ]:
        if slug in lookup:
            interests += (f'<a class="interest-story" href="{url(slug)}">{photo(slug, sizes="(max-width: 1000px) 46vw, (max-width: 1240px) 23vw, 278px")}'
                          f'<h3>{label}</h3><p>{detail}</p></a>')

    routes = ''
    for i, (label, detail, slug) in enumerate([
        ('Nami Island & Petite France', 'A day trip from Seoul', 'korea-nami-island-and-petite-france-day-trip'),
        ('Suwon Hwaseong Fortress', 'History beyond the capital', 'korea-suwon-hwaseong-fortress'),
        ('Haeundae & Gamcheon', 'Plan your journey across Busan', busan),
    ], 1):
        if slug in lookup:
            routes += f'<a class="route-link" href="{url(slug)}"><span>{i:02}</span><div><h3>{label}</h3><p>{detail}</p></div><b aria-hidden="true">→</b></a>'

    services = ''
    for label, detail, cta, slug in [
        ('SIM & eSIM', 'Check phone compatibility, data and setup before choosing.', 'Compare connections', 'korea-esim-mobile-data'),
        ('Airport transfers', 'Compare the train, airport bus and taxi for your arrival.', 'Compare transfers', 'korea-airport-transfer-options'),
        ('Attraction passes', 'Work out whether a pass fits the places you want to visit.', 'Compare Seoul passes', 'korea-seoul-attraction-pass-comparison'),
    ]:
        if slug in lookup:
            services += (f'<a class="service-link" href="{url(slug)}"><h3>{label}</h3><p>{detail}</p>'
                         f'<span>{cta} <span aria-hidden="true">→</span></span></a>')

    topic_links = ''.join(f'<a href="{base}/topics/{key}">{html.escape(label)}</a>'
                          for key, (label, _) in TOPICS.items() if any(p['_topic'] == key for p in posts))
    newest = ''.join(f'<a class="recent-story" href="{url(p["slug"])}">{photo(p["slug"], sizes="(max-width: 760px) calc(100vw - 40px), (max-width: 1240px) 30vw, 360px")}<div class="recent-story-copy"><span>{html.escape(p["_topic_label"])}</span>'
                     f'<h3>{html.escape(p["title"])}</h3><time datetime="{html.escape(p["date"], quote=True)}">{html.escape(p["date"])}</time></div></a>'
                     for p in posts[:3])
    search = (f'<form id="home-guide-search-form" class="home-search" role="search" action="{base}/guides">'
              '<label class="sr-only" for="home-guide-search">Search Korea travel guides</label>'
              '<input id="home-guide-search" type="search" placeholder="Search Korea: Busan, eSIM, trains…" aria-describedby="home-search-help">'
              '<button type="submit" aria-label="Search guides"><span aria-hidden="true">→</span></button>'
              '<span class="sr-only" id="home-search-help">Search our guide library by destination or travel question.</span></form>')
    quick_answers = ''.join(
        f'<a href="{url(slug)}">{question}<span aria-hidden="true">→</span></a>'
        for question, slug in [
            ('How do I leave the airport?', 'korea-airport-transfer-options'),
            ('Which SIM or eSIM do I need?', 'korea-esim-mobile-data'),
            ('Will my card work in Korea?', 'korea-currency-card-payments'),
            ('Where can I leave my luggage?', 'korea-luggage-storage-forwarding-services'),
        ] if slug in lookup)
    hero_photo = photo('korea-gyeongbokgung-palace', eager=True, sizes="100vw")
    home_hero = (f'<section class="home-hero">{hero_photo}<div class="home-hero-scrim"></div>'
                 '<div class="home-hero-copy"><h1>Plan a trip to Korea</h1>'
                 '<p>Transport, places to visit and the practical details.</p>'
                 '<p class="editorial-location">Based in Busan · Guides to Seoul, Busan and Jeju</p>'
                 f'{search}</div></section>')
    essentials_intro = (f'<div class="essentials-intro">{photo("korea-gyeongbokgung-palace", sizes="(max-width: 760px) calc(100vw - 40px), 280px")}'
                         '<div class="essentials-intro-copy"><p class="eyebrow">Before you land</p>'
                         '<h3>Travel essentials</h3><p>The six things worth sorting out before your trip, not after.</p>'
                         f'<a href="#plan-your-trip">See the essentials <span aria-hidden="true">→</span></a></div></div>')
    return (
        '<div class="travel-home">'
        f'{home_hero}'
        f'<nav class="quick-answers" aria-label="Common Korea travel questions"><p>Need a quick answer?</p><div>{quick_answers}</div></nav>'
        f'<div class="travel-lead">{hero}<aside class="season-panel" aria-labelledby="season-title"><h2 id="season-title">Seasonal travel</h2>{season}<a class="season-start" href="#plan-your-trip">First visit? Start with the essentials <span aria-hidden="true">→</span></a></aside></div>'
        f'<section id="plan-your-trip" class="travel-section preparation"><div class="section-heading"><div><h2>Plan your first trip</h2><p>Six useful starting points, before you land.</p></div></div><div class="preparation-layout">{essentials_intro}<div class="prep-grid">{preparation}</div></div></section>'
        f'<section id="destinations" class="travel-section"><div class="section-heading"><div><h2>Explore Korea</h2><p>Three places to start. A guide for each.</p></div><a href="{base}/guides">Browse all guides <span aria-hidden="true">→</span></a></div><div class="destination-explorer"><div class="destination-preview"><div class="destination-choices" role="group" aria-label="Preview a destination" hidden><button type="button" data-preview-region="seoul" aria-pressed="false">Seoul</button><button type="button" data-preview-region="busan" aria-pressed="false">Busan</button><button type="button" data-preview-region="jeju" aria-pressed="false">Jeju</button></div><div class="place-grid">{destinations}</div></div>{destination_map(base)}</div></section>'
        f'<section id="interests" class="travel-section"><div class="section-heading"><div><h2>Food, culture and days out</h2></div></div><div class="interest-grid">{interests}</div></section>'
        f'<section class="travel-section"><div class="section-heading"><div><h2>Recent guides</h2></div><a href="{base}/guides">All {len(posts)} guides <span aria-hidden="true">→</span></a></div><div class="recent-grid">{newest}</div></section>'
        f'<nav id="topics" class="topic-directory" aria-label="All guide topics"><h2>Browse by topic</h2><div>{topic_links}</div></nav>'
        '</div>')



def write_discovery(cfg,base,posts):
    groups = [("guides", "Korea Travel Guide Library", "Browse all our Korea guides, or search by place, topic and the problem you need to solve.",posts)] + discovery_groups(posts)
    for path,title,desc,group in groups:
        dest = os.path.join(SITE,*path.split('/'))+".html"
        os.makedirs(os.path.dirname(dest),exist_ok=True)
        with open(dest,'w',encoding='utf-8') as f:
            f.write(collection_page(cfg,base,group,title,desc,path,posts))
    with open(os.path.join(SITE,'guide-search.js'),'w',encoding='utf-8') as f:
        f.write('''(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const revealItems = [...document.querySelectorAll('.travel-home .interest-story, .travel-home .prep-link, .travel-home .section-heading')];
  if (!reduceMotion.matches && 'IntersectionObserver' in window) {
    try {
      const observer = new IntersectionObserver(entries => entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          entry.target.classList.remove('motion-pending');
          observer.unobserve(entry.target);
        }
      }), {threshold: 0.12});
      revealItems.forEach((item, index) => {
        item.style.setProperty('--reveal-delay', ((index % 3) * 70) + 'ms');
        if (item.getBoundingClientRect().top > window.innerHeight) item.classList.add('motion-pending');
        observer.observe(item);
      });
      const showAll = () => revealItems.forEach(item => item.classList.remove('motion-pending'));
      reduceMotion.addEventListener('change', showAll);
      window.addEventListener('beforeprint', showAll);
    } catch (_) { revealItems.forEach(item => item.classList.remove('motion-pending')); }
  }
  document.querySelectorAll('[data-island-note]').forEach(marker => marker.addEventListener('click', event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const note = document.getElementById('dokdo-note');
    if (note) {
      event.preventDefault(); note.open = !note.open;
      marker.classList.remove('is-activating');
      window.requestAnimationFrame(() => marker.classList.add('is-activating'));
    }
  }));
  const mobileNav = document.querySelector('details.mobile-nav');
  if (mobileNav) {
    mobileNav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => { mobileNav.open = false; }));
    mobileNav.addEventListener('keydown', event => {
      if (event.key === 'Escape') { mobileNav.open = false; mobileNav.querySelector('summary').focus(); }
    });
  }
  const explorer = document.querySelector('.destination-explorer');
  if (explorer) {
    const cards = [...explorer.querySelectorAll('[data-destination-card]')];
    const pins = [...explorer.querySelectorAll('[data-map-region]')];
    const buttons = [...explorer.querySelectorAll('[data-preview-region]')];
    const select = region => {
      if (!cards.some(card => card.dataset.destinationCard === region)) return;
      cards.forEach(card => { card.hidden = card.dataset.destinationCard !== region; });
      pins.forEach(pin => { pin.classList.toggle('is-selected', pin.dataset.mapRegion === region); });
      buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.previewRegion === region)));
    };
    const activate = region => {
      select(region);
      pins.forEach(pin => pin.classList.remove('is-activating'));
      const pin = pins.find(pin => pin.dataset.mapRegion === region);
      if (pin) window.requestAnimationFrame(() => pin.classList.add('is-activating'));
    };
    buttons.forEach(button => button.addEventListener('click', () => {
      activate(button.dataset.previewRegion);
    }));
    pins.forEach(pin => {
      pin.setAttribute('aria-label', 'Preview ' + pin.dataset.mapRegion[0].toUpperCase() + pin.dataset.mapRegion.slice(1));
      pin.addEventListener('click', event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        activate(pin.dataset.mapRegion);
      });
    });
    explorer.querySelector('.destination-choices').hidden = false;
    explorer.classList.add('is-enhanced');
    select('busan');
  }
  const regionExplorer = document.querySelector('.region-explorer');
  if (regionExplorer) {
    const cards = [...regionExplorer.querySelectorAll('[data-preview-card]')];
    const districts = [...regionExplorer.querySelectorAll('[data-map-region]')];
    const buttons = [...regionExplorer.querySelectorAll('[data-preview-region]')];
    const select = region => {
      if (!cards.some(card => card.dataset.previewCard === region)) return;
      cards.forEach(card => { card.hidden = card.dataset.previewCard !== region; });
      districts.forEach(d => { d.classList.toggle('is-selected', d.dataset.mapRegion === region); });
      buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.previewRegion === region)));
    };
    buttons.forEach(button => button.addEventListener('click', () => select(button.dataset.previewRegion)));
    districts.forEach(d => d.addEventListener('click', () => select(d.dataset.mapRegion)));
    districts.forEach(d => d.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(d.dataset.mapRegion); }
    }));
    regionExplorer.querySelector('.region-preview-choices').hidden = false;
    if (districts.length) select(districts[0].dataset.mapRegion);
  }
  const homeForm = document.getElementById('home-guide-search-form');
  if (homeForm) homeForm.addEventListener('submit', event => {
    event.preventDefault();
    const term = document.getElementById('home-guide-search').value.trim();
    window.location.assign(homeForm.action + (term ? '#search=' + encodeURIComponent(term) : ''));
  });
  const input = document.getElementById('guide-search');
  if (!input) return;
  const controls = document.querySelector('[data-search-control]');
  const cards = [...document.querySelectorAll('[data-guide]')];
  const status = document.getElementById('search-status');
  const empty = document.getElementById('search-empty');
  const filter = () => {
    const words = input.value.toLocaleLowerCase().trim().split(/\\s+/).filter(Boolean);
    let count = 0;
    cards.forEach(card => {
      const text = card.dataset.search.toLocaleLowerCase();
      const match = words.every(word => text.includes(word));
      card.hidden = !match;
      if (match) count++;
    });
    status.textContent = `${count} of ${cards.length} guides`;
    empty.hidden = count !== 0;
  };
  input.addEventListener('input', filter);
  document.getElementById('clear-search').addEventListener('click', () => {
    input.value = ''; filter(); input.focus();
  });
  if (window.location.hash.startsWith('#search=')) {
    try { input.value = decodeURIComponent(window.location.hash.slice(8)); } catch (_) { input.value = ''; }
  }
  controls.hidden = false;
  filter();
})();''')


DISCOVERY_CSS = """
/* Shared editorial typography and collection layouts. */
:root{--fg:#211f1a;--muted:#6d6a60;--bg:#faf6ec;--card:#fff;--soft:#f1e9d8;--blue:#1f7a5c;--red:#d9603a;--gold:#1f7a5c;--line:#e3dac2;--font-body:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;--font-display:'Manrope',Arial,'Helvetica Neue',sans-serif}
[hidden]{display:none!important}
html{scroll-padding-top:20px}
body{font-family:var(--font-body);line-height:1.6;background:var(--bg);font-size:15px}
h1,h2,h3{font-family:var(--font-body);letter-spacing:0}
a{color:var(--fg)}a:hover{color:var(--red)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.skip-link{position:absolute;top:-100px;left:16px;background:var(--fg);color:white;padding:12px;z-index:100}.skip-link:focus{top:8px}
.site-header{max-width:none;padding:8px max(20px,calc((100vw - 1120px)/2));display:flex;align-items:center;justify-content:space-between;gap:24px;text-align:left;background:white;border-bottom:1px solid var(--line);position:relative}
.brand{display:block;line-height:0}.brand-logo{height:104px;width:104px;object-fit:contain;border-radius:0;box-shadow:none}
.site-header nav{display:flex;align-items:center;gap:32px;flex-wrap:wrap}.site-header nav a{margin:0;padding:12px 0;font-size:13px;font-weight:500}
.mobile-nav{display:none}.tagline{display:none}
main.page{max-width:1160px;padding:40px 20px 48px}main.post{max-width:800px;padding:32px 24px}
main.page:not(:has(.home-intro,.collection-intro)){max-width:800px}
.home-intro{display:flex;align-items:center;justify-content:space-between;gap:24px;margin-bottom:40px}
.home-intro h1{font-size:32px;line-height:42px;margin:0 0 8px;font-weight:700}.home-intro p{font-size:15px;line-height:24px;margin:0;color:var(--muted)}
.home-search{width:350px;flex-shrink:0;position:relative}.home-search input{width:100%;height:48px;padding:12px 60px 12px 16px;font:inherit;font-size:15px;border:1px solid var(--line);background:white;color:var(--fg);border-radius:0;min-width:0}
input:focus-visible,summary:focus-visible{outline:2px solid var(--red);outline-offset:3px}
.home-section{margin-bottom:40px}.home-section>h2{font-size:24px;line-height:32px;margin:0 0 24px;font-weight:700}
.topic-links{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px 24px}.topic-link{display:block;padding:12px 0 16px}.topic-link:hover{text-decoration:none}.topic-link:hover h3{text-decoration:underline;text-underline-offset:4px}
.topic-link h3{font-size:15px;line-height:24px;margin:0 0 8px;font-weight:700}.topic-link p{font-size:12px;line-height:18px;color:var(--muted);margin:0}
.destination-stories{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px}.destination-story{display:block}.destination-story:hover{text-decoration:none}.destination-story:hover h3{text-decoration:underline;text-underline-offset:4px}
.destination-story img{display:block;width:100%;height:220px;object-fit:contain;background:white}.destination-story h3{font-size:18px;line-height:26px;margin:12px 0;font-weight:700}.destination-story p{font-size:15px;line-height:24px;margin:0;color:var(--muted)}
.practical-stories{padding-top:40px;border-top:1px solid var(--line)}.guide-entries{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:32px}.guide-entry{display:block}.guide-entry:hover{text-decoration:none}.guide-entry:hover h3{text-decoration:underline;text-underline-offset:4px}
.guide-entry>span{display:block;font-size:12px;line-height:18px;text-transform:uppercase;color:var(--red);margin-bottom:8px}.guide-entry h3{font-size:18px;line-height:26px;margin:0 0 8px;font-weight:700}.guide-entry p{font-size:15px;line-height:24px;color:var(--muted);margin:0}
.first-trip{padding:24px;background:var(--soft)}.first-trip h2{font-size:18px;line-height:26px;margin:0 0 16px}.first-trip ul{list-style:none;padding:0;margin:0;display:grid;gap:16px}.first-trip a{font-size:15px;line-height:24px}
.site-footer{background:var(--soft);border-top:0;text-align:left;padding:24px max(20px,calc((100vw - 1120px)/2)) 32px;font-size:12px;line-height:18px;margin-top:0}.site-footer p{max-width:1120px;margin:0 0 12px}.site-footer a{color:var(--muted)}
.collection-intro{max-width:750px;margin:28px 0}.collection-intro h1{font-size:32px;line-height:42px;margin:0 0 16px}.collection-intro p{color:var(--muted)}.collection-count{font-size:13px}.eyebrow{font-size:12px;font-weight:500;color:var(--red);letter-spacing:0}
.topic-pills{display:flex;flex-wrap:wrap;gap:8px 20px;margin:24px 0}.topic-pills a{margin:0;font-size:13px;padding:6px 0;border-bottom:1px solid transparent}.topic-pills a[aria-current=page]{border-color:var(--red);color:var(--red)}
.guide-search{margin:24px 0;padding:20px;background:var(--soft)}.guide-search label{display:block;font-size:13px;margin-bottom:8px}.search-row{display:flex;gap:8px}.search-row input{flex:1;min-width:0;border:1px solid var(--line);background:white;color:var(--fg);padding:12px 18px;font:inherit;border-radius:999px}.search-row button{border:1px solid var(--fg);background:var(--fg);color:white;padding:10px 16px;font:inherit;cursor:pointer;border-radius:999px}.guide-search p{font-size:12px;color:var(--muted);margin:8px 0 0}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:32px 24px;margin-top:24px}.card{display:flex;flex-direction:column;min-width:0;background:white;border:0;border-bottom:1px solid var(--line);border-radius:0;box-shadow:none;transition:none}.card:hover{transform:none;box-shadow:none;text-decoration:none}.card:hover h2{text-decoration:underline}
.card-thumb{width:100%;height:220px;object-fit:contain;aspect-ratio:auto}.card-placeholder{display:flex;flex-direction:column;justify-content:space-between;height:220px;padding:24px;background:var(--soft);color:var(--muted)}.card-placeholder span{font-size:12px}.card-placeholder b{font-size:18px;line-height:26px;max-width:90%}
.card-copy{display:flex;flex-direction:column;flex:1;padding:20px 0}.card-topic{font-size:12px;text-transform:uppercase;color:var(--red)}.card .card-copy h2{font-size:18px;line-height:26px;margin:8px 0;padding:0}.card .card-copy p{font-size:14px;line-height:23px;padding:0;margin:0 0 16px;color:var(--muted)}.card .card-copy .date{font-size:12px;padding:0;margin-top:auto}
.post h1{font-size:30px;line-height:1.35}.post h2{font-size:23px;border:0;padding-left:0}.post h3{font-size:18px}.post .meta .cat{background:var(--soft);color:var(--fg);border-radius:0}.post table{display:block;overflow-x:auto}.post :is(h1,h2,h3,p,a){overflow-wrap:break-word}
.breadcrumbs{font-size:12px}.breadcrumbs a{font-size:inherit;margin:0}.related-guides{border:1px solid var(--line);border-radius:0;background:var(--soft);padding:24px}
@media(max-width:900px){.topic-links{grid-template-columns:repeat(2,minmax(0,1fr))}.home-intro{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.site-header nav{gap:20px}.home-search{max-width:100%}}
@media(max-width:620px){.site-header{padding:8px 20px}.brand-logo{height:76px;width:76px}.site-header>.desktop-nav{display:none}.mobile-nav{display:block}.mobile-nav summary{font-size:13px;cursor:pointer;list-style:none;padding:12px 0}.mobile-nav summary::-webkit-details-marker{display:none}.mobile-nav nav{position:absolute;right:20px;top:calc(100% - 8px);width:240px;background:white;border:1px solid var(--line);padding:12px 20px;z-index:10;display:flex;flex-direction:column;align-items:flex-start;gap:0}.mobile-nav nav a{padding:12px 0}main.page{padding-top:24px}.home-intro{gap:0;margin-bottom:40px}.home-intro h1{font-size:26px;line-height:34px}.home-intro p{margin-bottom:0}.home-search{width:100%;margin-top:16px}.home-section>h2{font-size:24px;line-height:32px}.topic-links{gap:16px 24px}.destination-stories{grid-template-columns:1fr}.destination-story img{height:220px}.destination-story h3{margin:12px 0}.guide-entries{grid-template-columns:1fr}.grid{grid-template-columns:1fr}.collection-intro h1{font-size:26px;line-height:34px}.site-footer{padding-left:20px;padding-right:20px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}

/* Reviewed editorial photographs */
.reviewed-photo{margin:24px 0}.reviewed-photo .hero-img{width:100%;height:auto;max-height:720px;object-fit:contain;margin:0}.reviewed-photo figcaption{font-size:12px;line-height:1.7;color:#59635c;margin-top:8px}.photo-credit{border-top:1px solid #dedbd3;padding:20px 0}.photo-credit h2{font-size:20px}
/* Travel guide layout: destination-led editorial hierarchy. */
.site-header{padding-top:6px;padding-bottom:6px}.brand-logo{width:78px;height:78px}
main.page:has(.travel-home){max-width:1240px;padding:28px 28px 48px}
.guide-heading{display:flex;justify-content:space-between;align-items:center;gap:28px;margin-bottom:28px}
.guide-heading h1{font-size:28px;line-height:1.3;margin:0 0 6px;font-weight:700;letter-spacing:-.6px}
.guide-heading p{margin:0;color:var(--muted);font-size:14px}
.guide-heading .home-search{width:390px;display:flex;position:relative;margin:0}
.guide-heading .home-search input{padding-right:60px;border-radius:4px;font-size:14px;height:46px}
.home-search button{position:absolute;right:1px;top:1px;bottom:1px;width:46px;border:0;border-radius:0 3px 3px 0;background:var(--fg);color:#fff;font-size:22px;cursor:pointer}
.travel-lead{display:grid;grid-template-columns:minmax(0,2.05fr) minmax(0,1fr);gap:28px;align-items:stretch}
.lead-story{position:relative;display:block;overflow:hidden;border-radius:6px;background:#f3f3ef;align-self:start;color:#fff}
.lead-story>img{display:block;width:100%;height:auto;aspect-ratio:3/2;object-fit:cover}
.lead-caption{position:absolute;left:24px;bottom:24px;max-width:76%;padding:16px 20px;background:rgba(24,32,31,.91)}
.story-kicker,.section-kicker{font-size:11px;font-weight:700;letter-spacing:1.5px;line-height:18px}
.story-kicker{color:#f2dfb8}.lead-caption h2{font-size:30px;line-height:1.22;margin:8px 0 14px;letter-spacing:-.6px;color:#fff}
.story-action{font-size:13px;color:#fff}.story-action>span{padding-left:12px}
.lead-story:hover{color:white;text-decoration:none}.lead-story:hover .story-action{text-decoration:underline;text-underline-offset:4px}
.section-kicker{color:var(--red);margin:0 0 8px}
.season-panel{background:#f5f4ef;padding:28px 28px 20px;border-top:3px solid var(--red);display:flex;flex-direction:column}.season-start{display:block;margin-top:auto;padding-top:20px;font-size:12px;line-height:20px;color:var(--red)}.season-start span{padding-left:8px}
.season-panel h2{font-size:25px;line-height:32px;margin:0 0 12px;letter-spacing:-.5px}
.season-pick{display:block;position:relative;padding:20px 24px 20px 0;border-bottom:1px solid #dedfd9}
.season-pick:last-child{border-bottom:0}.season-pick h3{font-size:15px;line-height:22px;margin:0 0 6px}
.season-pick p{font-size:13px;line-height:21px;color:var(--muted);margin:0}.season-pick>span{position:absolute;right:0;top:20px}
.season-pick:hover{text-decoration:none}.season-pick:hover h3{text-decoration:underline;text-underline-offset:3px}
.travel-section{margin-top:52px}.section-heading{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:24px}
.section-heading h2,.route-section h2{font-size:27px;line-height:35px;letter-spacing:-.6px;margin:0;font-weight:700}
.section-heading>div>p:not(.section-kicker){font-size:14px;color:var(--muted);margin:8px 0 0}.section-heading>a{font-size:13px;white-space:nowrap;padding-bottom:4px}.section-heading>a>span{padding-left:8px}
.place-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px}.place-card{display:block;min-width:0}
.place-card>img{width:100%;height:auto;aspect-ratio:3/2;object-fit:contain;display:block;border-radius:4px;background:#f5f5f0}
.place-card>div{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:14px}.place-card h3{font-size:21px;line-height:28px;margin:0;letter-spacing:-.4px}
.place-card>div>span{font-size:11px;color:var(--muted)}.place-card>div>span>span{padding-left:8px}
.place-card p{font-size:13px;color:var(--muted);line-height:22px;margin:6px 0 0}.place-card:hover{text-decoration:none}.place-card:hover h3{text-decoration:underline;text-underline-offset:4px}
.preparation{padding:32px;background:#f5f4ef;border-radius:4px}.prep-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0 28px}
.prep-link{display:flex;align-items:baseline;gap:12px;padding:20px 0;border-top:1px solid #dedfd9;min-width:0}.prep-number{font-size:11px;font-weight:700;color:var(--red)}
.prep-link>div{flex:1;min-width:0}.prep-link h3{font-size:15px;line-height:22px;margin:0 0 5px}.prep-link p{font-size:12px;line-height:20px;margin:0;color:var(--muted)}.prep-arrow{font-size:17px;font-weight:400}
.prep-link:hover{text-decoration:none}.prep-link:hover h3{text-decoration:underline;text-underline-offset:3px}
.interest-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:24px}.interest-story{display:block;min-width:0}.interest-story img{display:block;width:100%;height:auto;aspect-ratio:3/2;object-fit:contain;background:#f5f5f0;border-radius:4px}
.interest-story h3{font-size:17px;line-height:25px;margin:12px 0 5px}.interest-story p{font-size:12px;color:var(--muted);line-height:20px;margin:0}.interest-story:hover{text-decoration:none}.interest-story:hover h3{text-decoration:underline}
.route-section{display:grid;grid-template-columns:1fr 2fr;gap:48px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:30px 0}.route-section>div>p:not(.section-kicker){font-size:14px;line-height:23px;color:var(--muted);margin:12px 0 0;max-width:240px}
.route-list{display:grid;gap:0}.route-link{display:flex;align-items:center;gap:20px;padding:18px 0;border-bottom:1px solid var(--line)}.route-link:first-child{padding-top:0}.route-link:last-child{border-bottom:0;padding-bottom:0}.route-link>span{font-size:12px;color:var(--red)}.route-link>div{flex:1}.route-link h3{font-size:16px;line-height:24px;margin:0 0 4px}.route-link p{font-size:12px;color:var(--muted);margin:0}.route-link>b{font-weight:400}.route-link:hover{text-decoration:none}.route-link:hover h3{text-decoration:underline}
.recent-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px}.recent-story{border-top:none;display:flex;flex-direction:column;align-items:stretch}.recent-story img{border-radius:10px;object-fit:cover;width:100%;height:230px;display:block}.recent-story-copy{padding-top:16px;border-top:2px solid var(--line);display:flex;flex-direction:column;align-items:start;flex:1}.recent-story>span,.recent-story-copy>span{font-size:11px;color:var(--red)}.recent-story h3{font-size:17px;line-height:26px;margin:10px 0 16px}.recent-story time{font-size:11px;color:var(--muted);margin-top:auto}.recent-story:hover{text-decoration:none}.recent-story:hover h3{text-decoration:underline}
.home-hero{position:relative;margin:0 0 32px;border-radius:14px;overflow:hidden;min-height:420px;display:flex;align-items:flex-end}.home-hero img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}.home-hero-scrim{position:absolute;inset:0;background:linear-gradient(180deg,rgba(20,30,22,.1) 0%,rgba(18,26,20,.74) 100%)}.home-hero-copy{position:relative;color:#fff;padding:48px 40px;max-width:640px}.home-hero-copy h1{font-size:46px;line-height:1.08;margin:0 0 14px;color:#fff;font-family:var(--font-display)}.home-hero-copy p{font-size:16px;color:#f1ede2;margin:0 0 6px}.home-hero-copy .editorial-location{font-size:13px;color:#d8d2c0;margin-bottom:22px}.home-hero-copy .home-search{margin-top:10px;width:100%;max-width:420px}
.preparation-layout{display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.6fr);gap:40px;align-items:stretch}.essentials-intro{position:relative;border-radius:12px;overflow:hidden;min-height:320px;display:flex;align-items:center;justify-content:center}.essentials-intro img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}.essentials-intro-copy{position:relative;color:#fff;text-align:center;padding:28px;display:flex;flex-direction:column;align-items:center}.essentials-intro-copy::before{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(20,30,22,.35) 0%,rgba(18,26,20,.72) 100%);z-index:-1;margin:-28px}.essentials-intro-copy .eyebrow{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#d8d2c0;margin:0 0 8px}.essentials-intro-copy h3{font-family:var(--font-display);font-size:26px;margin:0 0 10px;color:#fff}.essentials-intro-copy p{font-size:13px;color:#f1ede2;margin:0 0 18px;max-width:28ch}.essentials-intro-copy a{color:#fff;font-size:13px;font-weight:600;border:1px solid rgba(255,255,255,.6);border-radius:999px;padding:9px 18px}
@media(max-width:760px){.preparation-layout{grid-template-columns:1fr}.home-hero-copy{padding:28px 22px;max-width:none}.home-hero-copy h1{font-size:32px}.home-hero-copy .home-search{width:100%}.home-hero-copy .search-row{flex-wrap:wrap}.home-hero{min-height:360px}}
.travel-services{padding:32px;background:#eef2ee;border-top:3px solid #314a40;border-radius:4px}.service-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px}.service-link{display:flex;flex-direction:column;align-items:start;min-width:0;border-top:1px solid #cbd4cb;padding-top:18px}.service-link h3{font-size:19px;line-height:26px;margin:0 0 10px}.service-link p{font-size:13px;line-height:22px;color:var(--muted);margin:0 0 20px}.service-link>span{margin-top:auto;font-size:12px;font-weight:700;border-bottom:1px solid #a0b1a4;padding-bottom:4px}.service-link>span>span{padding-left:12px}.service-link:hover{text-decoration:none}.service-link:hover>span{text-decoration:underline;text-underline-offset:4px}
.topic-directory{margin-top:48px;padding-top:24px;border-top:1px solid var(--line);display:flex;gap:32px;align-items:baseline}.topic-directory h2{font-size:14px;margin:0;white-space:nowrap}.topic-directory>div{display:flex;gap:8px 22px;flex-wrap:wrap}.topic-directory a{font-size:12px}
@media(max-width:1000px){.travel-lead{gap:20px;grid-template-columns:minmax(0,1.75fr) minmax(0,1fr)}.season-panel{padding:20px 20px 8px}.season-pick{padding-top:14px;padding-bottom:14px}.lead-caption{left:16px;bottom:16px;padding:14px 16px}.lead-caption h2{font-size:25px}.interest-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:28px}.guide-heading .home-search{width:340px}}
@media(max-width:760px){.guide-heading{align-items:start;flex-direction:column;gap:18px}.guide-heading .home-search{width:100%}.travel-lead{grid-template-columns:1fr}.season-panel{padding:24px}.season-pick{padding-top:16px;padding-bottom:16px}.prep-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.route-section{grid-template-columns:1fr;gap:24px}.route-section>div>p:not(.section-kicker){max-width:none}.topic-directory{flex-direction:column;gap:16px}}
@media(max-width:620px){.brand-logo{width:64px;height:64px}.site-header{padding:6px 20px}main.page:has(.travel-home){padding:24px 20px 36px}.guide-heading h1{font-size:25px;letter-spacing:-.4px}.guide-heading p{font-size:13px}.guide-heading{margin-bottom:24px}.travel-lead{gap:24px}.lead-caption{position:static;max-width:none;background:#253b37;padding:20px}.lead-caption h2{font-size:25px;margin:6px 0 12px}.lead-caption h2 br{display:none}.lead-caption h2 br:after{content:' '}.lead-caption .story-action{font-size:12px}.season-panel h2{font-size:23px}.season-panel{padding:22px 22px 10px}.travel-section{margin-top:36px}.section-heading{align-items:start;margin-bottom:20px;gap:12px}.section-heading h2,.route-section h2{font-size:24px;line-height:32px}.section-heading>a{font-size:11px;white-space:normal;text-align:right;max-width:92px;padding-top:24px}.place-grid{grid-template-columns:1fr;gap:28px}.place-card>div{margin-top:12px}.place-card h3{font-size:20px}.preparation{padding:24px 20px}.prep-grid{grid-template-columns:1fr}.prep-link{padding:16px 0}.interest-grid{gap:24px 16px}.interest-story h3{font-size:15px;line-height:22px}.interest-story p{font-size:11px;line-height:18px}.route-section{padding:24px 0}.route-link{gap:14px}.route-link h3{font-size:15px}.recent-grid{grid-template-columns:1fr;gap:24px}.recent-story h3{font-size:16px;line-height:24px;margin-bottom:12px}.topic-directory{margin-top:36px}.topic-directory>div{gap:12px 20px}}
@media(max-width:760px){.service-grid{grid-template-columns:1fr}.travel-services{padding:24px}.service-link p{margin-bottom:12px}}

/* Consistent reading edges and natural wrapping; no text is clipped. */
.guide-heading h1,.home-intro h1,.collection-intro h1,.section-heading h2,.route-section h2,.post h1{font-family:var(--font-display);font-weight:400;letter-spacing:-.7px}
.guide-heading h1,.collection-intro h1{font-size:38px;line-height:1.15}
.section-heading h2,.route-section h2{font-size:32px;line-height:1.2}
.lead-caption h2{font-family:var(--font-display);font-weight:400;font-size:34px;line-height:1.12;letter-spacing:-.5px}
.guide-heading p{font-size:14px}.card .card-copy h2,.interest-story h3,.recent-story h3,.service-link h3{font-weight:600}
.site-header nav a{font-size:13px;letter-spacing:.1px}
.home-search button{background:#253b37;transition:background .18s ease}
.home-search button:hover{background:#3c584d}.home-search button:active{background:#1b2d27}
.search-row button{background:var(--blue);border-color:var(--blue);border-radius:999px;font-size:13px;font-weight:600;transition:background .18s ease}
.search-row button:hover{background:#2e9476}
.section-heading>a{border-bottom:1px solid #c8d2c9;padding-bottom:6px;font-weight:500}
.section-heading>a:hover{border-color:#253b37;color:#253b37;text-decoration:none}
.section-heading>a>span{display:inline-block;transition:transform .2s ease}
.section-heading>a:hover>span{transform:translateX(3px)}
.service-link>span{display:inline-flex;align-items:center;gap:22px;border-bottom:1px solid #a0b1a4;padding:4px 0 7px;color:#253b37;font-weight:600}
.service-link>span>span{padding-left:0;transition:transform .2s ease}.service-link:hover>span>span{transform:translateX(3px)}
.destination-explorer{display:grid;grid-template-columns:minmax(0,1.08fr) minmax(0,1fr);gap:48px;align-items:center}
.destination-preview{min-width:0}.destination-preview .place-grid{grid-template-columns:1fr;margin-top:0}
.arrival-guides{background:#f1f3ee;border:1px solid #e0e5dc;padding:24px;margin:24px 0 32px}
.arrival-guides h2{font:400 25px/1.3 var(--font-display);margin:0 0 8px}.arrival-guides p{margin:0 0 16px;color:#526651}
.arrival-guides>div{display:flex;flex-wrap:wrap;gap:8px 24px}.arrival-guides a{display:inline-flex;align-items:center;gap:16px;min-height:44px;font-size:15px;text-decoration:underline;text-underline-offset:4px}
.destination-choices{display:flex;gap:28px;margin-bottom:20px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.destination-choices button{font:600 14px/1.5 var(--font-body);cursor:pointer;min-height:44px;padding:10px 2px 12px;background:transparent;border:0;border-bottom:2px solid transparent;border-radius:0;color:#727a72;margin-bottom:-1px;transition:color .18s ease,border-color .18s ease}
.destination-choices button:hover{color:#253b37}
.destination-choices button[aria-pressed=true]{background:transparent;border-bottom-color:#253b37;color:#253b37}
.destination-choices button:focus-visible{outline:2px solid var(--red);outline-offset:3px}
.destination-cta{display:inline-flex;align-items:center;gap:32px;min-height:44px;background:var(--blue);border:1px solid var(--blue);border-radius:999px;font-size:13px;font-weight:600;margin-top:20px;padding:10px 20px;color:#fff;transition:background .18s ease}
.destination-cta>span{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:50%;background:rgba(255,255,255,.22);transition:transform .2s ease}.place-card:hover .destination-cta{background:#2e9476;border-color:#2e9476}.place-card:hover .destination-cta>span{transform:translateX(3px)}
.place-card:active .destination-cta{background:#155c44}
.korea-map{background:#f1f3ee;border:1px solid #e0e5dc;border-radius:8px;padding:26px 28px 16px;text-align:start;box-shadow:0 8px 24px #26392d05}
.atlas-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;border-bottom:1px solid #d9e0d5;padding-bottom:18px}
.map-eyebrow{font-size:9px;letter-spacing:2.1px;font-weight:700;color:#637565;margin:0 0 8px}
.map-title{font-family:Georgia,'Times New Roman',serif;font-size:30px;letter-spacing:-.8px;line-height:1.15;margin:0;color:#263c32}
.atlas-count{font-family:Georgia,'Times New Roman',serif;font-size:25px;color:#607263;line-height:1;text-align:center}
.atlas-count>span{font:700 8px/1.5 var(--font-body);display:block;letter-spacing:1.6px;margin-top:5px}
.korea-map svg{width:100%;height:390px;display:block;margin:10px auto 6px;max-width:440px;overflow:visible}
.korea-coast-shadow{fill:#b5c6b7;opacity:.65}
.korea-land{fill:#dce5d4;stroke:#8caa90;stroke-width:1;stroke-linejoin:round}
.atlas-country-name{font:italic 25px Georgia,'Times New Roman',serif;fill:#657f69;text-anchor:middle;opacity:.6;pointer-events:none}
.atlas-compass text{font:700 9px var(--font-body);fill:#748573;text-anchor:middle}.atlas-compass path{fill:none;stroke:#748573;stroke-width:1}
.destination-pin{cursor:pointer;text-decoration:none}
.destination-pin[data-map-region=seoul]{--pin-color:#a64d3c}
.destination-pin[data-map-region=busan]{--pin-color:#306e80}
.destination-pin[data-map-region=jeju]{--pin-color:#71813b}
.pin-hit{fill:transparent}.pin-halo{fill:#fff;stroke:var(--pin-color);stroke-width:1.5}
.pin-wave{fill:none;stroke:var(--pin-color);stroke-width:1.5;opacity:0;pointer-events:none;transform-box:fill-box;transform-origin:center}
.pin-dot{fill:var(--pin-color);transition:fill .2s ease}
.pin-leader{fill:none;stroke:var(--pin-color);stroke-width:1;opacity:.65}
.pin-label{fill:#ffffffed;stroke:#dce2d7;stroke-width:.75}
.destination-pin .pin-name{fill:#263c32;font:700 21px var(--font-body)}
.destination-pin .pin-subtitle{fill:#526651;font:600 9px var(--font-body);letter-spacing:.15px}
.destination-pin.is-selected .pin-halo{fill:var(--pin-color);stroke:var(--pin-color)}
.destination-pin.is-selected .pin-dot{fill:#fff}
.destination-pin.is-selected .pin-label{fill:var(--pin-color);stroke:var(--pin-color)}
.destination-pin.is-selected .pin-name{fill:#fff}
.destination-pin.is-selected .pin-subtitle{fill:#fff;opacity:.88}
.destination-pin:focus-visible{outline:none}.destination-pin:focus-visible .pin-hit{stroke:var(--pin-color);stroke-width:2}
.atlas-footer{border-top:1px solid #d9e0d5;padding-top:12px;display:flex;align-items:center;justify-content:space-between;color:#637565}
.map-note{font-size:13px;line-height:1.6;color:#526651;margin:0}
.map-credit{display:block;font-size:11px;color:#526651;margin-top:10px}.map-credit a{text-decoration:underline;text-underline-offset:2px}
.region-map{background:#f1f3ee;border:1px solid #e0e5dc;border-radius:8px;padding:22px 24px 16px;margin-top:20px}
.region-map svg{width:100%;height:auto;max-height:420px;display:block;margin:12px auto}
.map-intro{font-size:13px;line-height:1.6;color:#526651;margin:4px 0 0}
.region-district{fill:#dce5d4;stroke:#fff;stroke-width:1.2;transition:fill .2s ease}
.region-district.is-active{fill:#306e80;cursor:pointer}
.region-district.is-active:hover,.region-district.is-active:focus-visible{fill:#24536a}
.region-district.is-active.is-selected{fill:#1b4256}
.region-district.is-active:focus-visible{outline:none;stroke:#263c32;stroke-width:2}
.region-preview-choices{display:flex;gap:20px;flex-wrap:wrap;border-bottom:1px solid var(--line);margin:14px 0 0}
.region-preview-choices button{font:600 13px/1.5 var(--font-body);cursor:pointer;min-height:40px;padding:8px 2px 10px;background:transparent;border:0;border-bottom:2px solid transparent;color:#727a72;margin-bottom:-1px;transition:color .18s ease,border-color .18s ease}
.region-preview-choices button:hover{color:#253b37}
.region-preview-choices button[aria-pressed=true]{border-bottom-color:#253b37;color:#253b37}
.region-preview-card{margin-top:16px}
.region-preview-card+.region-preview-card{border-top:1px solid var(--line);padding-top:16px}
.region-preview-card h3{font-size:18px;margin:0 0 6px;color:#526651;font-weight:600}
.region-place+.region-place{margin-top:14px;padding-top:14px;border-top:1px dashed var(--line)}
.region-place h4{font-size:17px;margin:0 0 4px}
.region-transit{font-size:13px;color:#526651;margin:0}
.region-cta{display:inline-flex;align-items:center;gap:8px;min-height:40px;background:#253b37;border-radius:3px;font-size:13px;font-weight:600;margin-top:10px;padding:9px 15px;color:#fff;transition:background .18s ease}
.region-cta:hover{background:#3c584d;text-decoration:none}
@media(prefers-reduced-motion:no-preference){
 .destination-explorer.is-enhanced .place-card:not([hidden]){animation:destination-reveal .32s ease-out both}
 .destination-pin.is-activating .pin-wave{animation:atlas-pin-wave .6s ease-out both}
 .destination-pin.is-activating .pin-label{animation:atlas-label-press .3s ease-out both;transform-box:fill-box;transform-origin:left center}
}
@keyframes atlas-pin-wave{from{opacity:.65;transform:scale(.7)}to{opacity:0;transform:scale(1.9)}}
@keyframes atlas-label-press{0%{transform:scale(.96)}60%{transform:scale(1.025)}100%{transform:scale(1)}}
@keyframes destination-reveal{from{opacity:.2;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:760px){
 .destination-explorer{grid-template-columns:1fr;gap:24px}
 .korea-map{grid-row:1;padding:22px 18px 14px}
 .map-title{font-size:28px}.korea-map svg{height:350px}
 .destination-choices{margin-bottom:16px}
 .destination-choices button{flex:1;padding:10px 12px}
}
.place-card>img,.interest-story img,.destination-story img,.card-thumb{
 width:100%;height:auto;aspect-ratio:3/2;object-fit:cover;object-position:center;display:block;
}
.interest-story img.photo-daiso,.card-thumb.photo-daiso{object-position:50% 65%}
.card-placeholder{height:auto;aspect-ratio:3/2}
.place-card,.interest-story,.service-link,.recent-story{transition:transform .24s ease}
@media(hover:hover) and (pointer:fine){
 .place-card:hover,.interest-story:hover,.service-link:hover,.recent-story:hover{transform:translateY(-4px)}
}
@media(prefers-reduced-motion:reduce){
 .place-card,.interest-story,.service-link,.recent-story{transition:none}
 .place-card:hover,.interest-story:hover,.service-link:hover,.recent-story:hover{transform:none}
}
main.page,main.post,.site-footer{text-align:start}
main :is(h1,h2,h3){text-wrap:balance;word-break:normal;overflow-wrap:break-word;hyphens:none}
main :is(p,li){text-wrap:pretty;word-break:normal;overflow-wrap:break-word}
.guide-heading>div,.section-heading>div,.route-link>div{min-width:0}
.guide-heading p,.collection-intro>p{max-width:65ch;line-height:1.7}
.quick-answers{margin:0 0 28px;padding:16px 0 20px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.quick-answers>p{margin:0 0 10px;font-size:12px;color:var(--muted)}
.quick-answers>div{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px 24px}
.quick-answers a{display:flex;justify-content:space-between;align-items:baseline;gap:12px;font-size:13px;font-weight:500;line-height:1.6}
.quick-answers a>span{color:var(--red);flex-shrink:0}
@media(max-width:900px){.quick-answers>div{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 20px}}
@media(max-width:620px){.quick-answers a{font-size:12px}.quick-answers>div{gap:14px 18px}}
.place-card>div{align-items:baseline}.place-card>div>span{white-space:nowrap;flex-shrink:0}
.prep-number{flex:0 0 2ch;font-variant-numeric:tabular-nums}.prep-arrow{flex-shrink:0}
.route-link>span{flex:0 0 2ch;font-variant-numeric:tabular-nums}.route-link>b{flex-shrink:0}
.interest-story,.place-card,.card-copy,.service-link{ text-align:start }
.post{font-size:16px;line-height:1.8}
.post h1{max-width:25ch;margin:0 0 20px;line-height:1.3}
.post h2{line-height:1.4;margin-bottom:.65em}.post h3{line-height:1.5}
.post :is(p,ul,ol){margin-top:0;margin-bottom:1.25em}
.post :is(ul,ol){padding-inline-start:1.5em}.post li+li{margin-top:.45em}
.reviewed-photo figcaption{max-width:72ch;text-align:start;overflow-wrap:anywhere}
/* Plain editorial hierarchy: real photographs and useful labels carry the page. */
:root{--font-display:'Manrope',Arial,'Helvetica Neue',sans-serif;--fg:#211f1a;--muted:#6d6a60;--line:#e3dac2;--red:#d9603a;--soft:#f1e9d8}
.guide-heading{align-items:flex-end;margin:10px 0 26px}.guide-heading h1{font:600 44px/1.08 var(--font-display);letter-spacing:-1.6px;max-width:16ch;margin-bottom:12px}
.guide-heading p{font-size:16px;line-height:1.6}.guide-heading .editorial-location{font-size:12px;color:#677269;margin-top:8px}
.lead-story,.lead-story:hover{border-radius:0;background:white;color:var(--fg)}
.lead-caption{position:static;max-width:none;padding:18px 0 0;background:white;animation:none}
.lead-caption .story-kicker{font-size:12px;line-height:1.5;font-weight:500;letter-spacing:0;color:#58625b}
.lead-caption h2{font:600 29px/1.2 var(--font-display);color:var(--fg);letter-spacing:-.7px;margin:7px 0 12px}
.story-action{color:#32624e;font-size:14px}.lead-story>img{animation:none;border-radius:0}
.season-panel{background:white;border-top:1px solid var(--line);padding:22px 0 0 24px}.season-panel h2{font-size:22px;letter-spacing:-.4px}
.section-heading h2{font:600 28px/1.2 var(--font-display);letter-spacing:-.7px}
.section-kicker{letter-spacing:0;text-transform:none}.section-heading.motion-in .section-kicker:before{display:none}
.preparation{background:white;padding:34px 0 0;border-top:1px solid var(--line);border-radius:0}.prep-number{display:none}
.prep-link h3{font-size:17px}.prep-link p{font-size:14px}.prep-arrow{font-size:18px}
.interest-story h3{font-size:18px}.interest-story p{font-size:14px;line-height:1.6}
.travel-section{margin-top:58px}.place-card h3{font-size:25px}.place-card p{font-size:15px;line-height:1.6}
.korea-map{border:0;border-radius:0;box-shadow:none;background:#f6f7f4;padding:24px}
.map-title{font:600 20px/1.3 var(--font-display);letter-spacing:-.4px}.atlas-heading{border:0;padding-bottom:4px}
.korea-land{fill:#e2e8df;stroke:#9bad9d}.korea-coast-shadow{display:none}
.destination-pin .pin-name{font:600 24px var(--font-body)}.pin-label{stroke:none}.atlas-footer{border-top:1px solid var(--line)}
.destination-cta{font-size:14px;min-height:46px;padding:11px 20px;margin-top:18px}
.place-card>img,.interest-story img{border-radius:0}.place-card:hover,.interest-story:hover,.recent-story:hover{transform:none}
.place-card:hover h3{text-decoration:none}.destination-cta:hover{background:#3c584d}
@media(max-width:760px){.guide-heading h1{font-size:36px;max-width:none}.guide-heading p{font-size:15px}.season-panel{padding:20px 0 0}.travel-section{margin-top:38px}.section-heading h2{font-size:26px}.interest-story p{font-size:13px}.lead-caption{padding:16px 0 0;background:white}.lead-caption h2{font-size:26px;color:var(--fg)}.korea-map{padding:20px 16px}.prep-grid{gap:0 24px}}
@media(max-width:620px){.interest-grid{grid-template-columns:1fr 1fr;gap:26px 18px}.interest-story h3{font-size:16px}.guide-heading h1{font-size:33px;letter-spacing:-1px}.lead-caption .story-action{font-size:14px}.guide-heading .editorial-location{font-size:12px}}

@media(min-width:621px){
 .interest-story h3{min-height:1.5em}
 .card .card-copy h2{min-height:78px}
}
@media(min-width:761px){.service-link h3{min-height:52px}}
@media(max-width:620px){
 .guide-heading h1,.collection-intro h1{font-size:32px}
 .section-heading h2,.route-section h2{font-size:29px}
 .lead-caption h2{font-size:30px}
 .section-heading{align-items:flex-end}
 .section-heading>a{padding-top:0;padding-bottom:3px;line-height:1.5;flex-shrink:0}
 main.post{padding:24px 20px}
 .post h1{font-size:30px;line-height:1.25;max-width:none}
 .post h2{font-size:22px}
 .interest-story h3{min-height:44px}
 .service-link h3,.card .card-copy h2{min-height:0}
}
@media(prefers-reduced-motion:reduce){
 .home-search button,.search-row button,.section-heading>a>span,.service-link>span>span,.destination-choices button,.destination-cta,.destination-cta>span{transition:none}
 .section-heading>a:hover>span,.service-link:hover>span>span,.place-card:hover .destination-cta>span{transform:none}
}

/* Purposeful imagery and one-time motion, October 22 visual refinement. */
.prep-link{align-items:center;gap:16px}.prep-visual{flex:0 0 58px;width:58px;height:58px;display:grid;place-items:center;background:#f0f3eb;color:#365c49;border-radius:12px}.prep-visual svg{width:36px;height:36px}
.prep-link:nth-child(2n) .prep-visual{background:#eef2f5;color:#385466}.prep-link:nth-child(3n) .prep-visual{background:#f7f0e6;color:#7b5a39}
.korea-map svg{width:100%;height:auto;max-height:540px;overflow:visible}.map-island{fill:#45654f}.map-island text{font:600 17px var(--font-body);fill:#324f3c}.map-island .island-korean{font:500 14px var(--font-body)}.map-island .island-touch{fill:transparent;stroke:none}.map-island .island-wave{fill:none;stroke:#52775c;stroke-width:1.5;opacity:0;transform-box:fill-box;transform-origin:center}
.dokdo-marker{cursor:pointer}.dokdo-marker:focus-visible{outline:none}.dokdo-marker:focus-visible .island-touch{stroke:#254c37;stroke-width:2;fill:#e3eadb}.island-note{margin:16px 0 8px;padding:12px 0;border-top:1px solid #d6dfd0;font-size:13px}.island-note summary{cursor:pointer;color:#304e3b;font-weight:600;min-height:32px;align-content:center}.island-note p{font-size:13px;margin:8px 0}.island-note>a{display:inline-flex;padding:8px 0;text-decoration:underline;text-underline-offset:3px}
@media(prefers-reduced-motion:no-preference){
 .prep-link,.interest-story,.section-heading{transition:transform .35s ease,opacity .55s ease;transition-delay:var(--reveal-delay,0ms)}
 .motion-pending{opacity:0;transform:translateY(18px)}
 .interest-story.is-visible{animation:photo-card-in .65s cubic-bezier(.2,.7,.3,1) both;animation-delay:var(--reveal-delay,0ms)}
 .prep-link.is-visible .prep-visual{animation:icon-arrive .5s ease-out both;animation-delay:var(--reveal-delay,0ms)}
 .destination-explorer.is-enhanced .place-card:not([hidden]){animation:photo-card-in .6s cubic-bezier(.2,.7,.3,1) both}
 .destination-pin.is-activating .pin-wave,.dokdo-marker.is-activating .island-wave{animation:atlas-pin-wave .8s ease-out both}
 .destination-cta>span,.prep-arrow{display:inline-block;transition:transform .25s ease}
 .destination-cta:hover>span,.prep-link:hover .prep-arrow{transform:translateX(5px)}
}
@keyframes photo-card-in{from{opacity:.25;transform:translateY(16px);clip-path:inset(0 0 5% 0)}to{opacity:1;transform:translateY(0);clip-path:inset(0)}}
@keyframes icon-arrive{from{opacity:.25;transform:translateY(7px) scale(.9)}to{opacity:1;transform:none}}
@media(hover:hover) and (prefers-reduced-motion:no-preference){.interest-story:hover{transform:translateY(-5px)}.prep-link:hover .prep-visual{transform:translateY(-3px);transition:transform .25s ease}}
@media(max-width:620px){.prep-visual{width:50px;height:50px;flex-basis:50px}.prep-visual svg{width:31px;height:31px}.korea-map svg{height:auto;min-height:300px}.map-island text{font-size:18px}}
@media(prefers-reduced-motion:reduce){.motion-pending{opacity:1;transform:none}.interest-story,.prep-visual,.place-card,.island-wave,.prep-arrow{animation:none!important;transition:none!important;transform:none!important}}
@media print{.motion-pending{opacity:1;transform:none}}

"""


# ---------- shared layout ----------
def page(cfg, base, title, description, body, canonical, is_post=False,
         og_type="website", image=None, published=None, jsonld=None):
    canonical = canonical_url(base, canonical)
    if jsonld is None:
        jsonld = jsonld_data(site_entities(cfg, base))
    robots_tag = '<meta name="robots" content="noindex, follow">' if canonical == base + "/404" else ""
    brand_html = brand_markup(cfg, base)
    nav = discovery_nav(base)
    year = datetime.now().year
    affiliate_enabled = cfg.get("affiliate_links_enabled", False)
    footer_note = (
        'Some links on this site are affiliate links (Klook, Trip.com, GetYourGuide) — '
        'we may earn a commission at no extra cost to you. See our '
        f'<a href="{base}/affiliate-disclosure.html">disclosure</a>. '
        if affiliate_enabled else
        'This site is currently pure editorial content with no affiliate links. '
    )
    img_tag = f'<meta property="og:image" content="{html.escape(image)}">\n<meta name="twitter:image" content="{html.escape(image)}">\n' if image else ""
    published_tag = f'<meta property="article:published_time" content="{published}">\n' if published else ""
    jsonld_tag = f'<script type="application/ld+json">{jsonld}</script>\n' if jsonld else ""
    output = f"""<!doctype html>
<html lang="{cfg['language']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} | {html.escape(cfg['site_name'])}</title>
<meta name="description" content="{html.escape(description)}">
<link rel="canonical" href="{html.escape(canonical)}">
<link rel="alternate" type="application/rss+xml" title="Land in Korea guides" href="{base}/feed.xml">
{robots_tag}
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="{html.escape(cfg['site_name'])}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(description)}">
<meta property="og:url" content="{canonical}">
<meta name="twitter:card" content="{'summary_large_image' if image else 'summary'}">
<meta name="twitter:title" content="{html.escape(title)}">
<meta name="twitter:description" content="{html.escape(description)}">
{img_tag}{published_tag}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{base}/style.css">
{goatcounter_script(cfg)}{adsense_script(cfg)}{jsonld_tag}</head>
<body>
<a class="skip-link" href="#main-content">Skip to content</a>
<header class="site-header">
  <a class="brand" href="{base}/index.html">
    {brand_html}
  </a>
  <nav class="desktop-nav" aria-label="Primary">{nav}</nav>
  <details class="mobile-nav"><summary>Browse guides</summary><nav aria-label="Mobile">{nav}</nav></details>
</header>
<main id="main-content" class="{'post' if is_post else 'page'}">
{body}
</main>
<footer class="site-footer">
  <p>&copy; {year} {html.escape(cfg['site_name'])}. {footer_note}
  Prices and rules change — always confirm on the official site before booking.</p>
  <p><a href="{base}/about.html">About</a> ·
  <a href="{base}/affiliate-disclosure.html">Disclosure</a> ·
  <a href="{base}/privacy.html">Privacy</a> ·
  <a href="{base}/terms-of-service.html">Terms</a> ·
  <a href="{base}/photo-credits.html">Photo credits</a> ·
  <a href="{base}/contact.html">Contact</a></p>
</footer>
<script src="{base}/guide-search.js" defer></script>
</body>
</html>
"""
    # Normalize generated and existing same-site navigation to the served URL.
    return re.sub(r'href="([^"<>]+)"',
                  lambda m: 'href="' + canonical_url(base, m.group(1)) + '"', output)


FAQ_SECTION = re.compile(r"^##\s+Frequently Asked Questions\s*$", re.MULTILINE)
FAQ_ITEM = re.compile(r"^###\s+(.+?)\s*\n+((?:(?!^###\s|^##\s).+\n?)+)", re.MULTILINE)


def extract_faq(body):
    """마크다운 본문에서 '## Frequently Asked Questions' 아래 '### 질문 / 답변'
    쌍을 뽑아 FAQPage 구조화 데이터에 쓴다 — 애드센스 심사와는 무관하지만
    보이는 문답을 구조화한다. Google FAQ 리치 결과는 2026-05-07 종료됐다."""
    m = FAQ_SECTION.search(body)
    if not m:
        return []
    section = body[m.end():]
    items = []
    for qm in FAQ_ITEM.finditer(section):
        question = qm.group(1).strip()
        answer = " ".join(line.strip() for line in qm.group(2).strip().splitlines())
        if question and answer:
            items.append({"question": question, "answer": answer})
    return items



def photo_review():
    path = os.path.join(AUTO, "photo_review.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def apply_photo_review(meta, cfg):
    # Two tiers: "approved" is the hand-curated tier (real capture date, full
    # provenance, credited on photo-credits.html) and wins outright. Anything else
    # just keeps whatever image: fetch_images.py already wrote — that pipeline already
    # enforces a Korea+subject match via vision check before it writes the field, so an
    # unreviewed-but-confirmed Pexels photo is the second, lower tier, not nothing.
    # Only "withheld" (a specific editorial call, not merely "not yet hand-reviewed")
    # blanks it. 2026-10-23 user decision after hand-curation alone couldn't cover
    # everyday-life topics (no recent/Korea-specific/CC source exists for most of them).
    review = photo_review()
    slug = meta["slug"]
    approved = review["approved"].get(slug)
    if approved:
        meta["image"] = cfg["base_url"].rstrip("/") + approved["image"]
        meta["_photo"] = approved
    elif slug in review["withheld"]:
        meta["image"] = ""


def reviewed_photo_markup(p, base, css_class="", eager=False, caption=False, sizes=None):
    item = p.get("_photo")
    image = p.get("image", "").replace("https://raw.githubusercontent.com/solusupport-bot/land-in-korea-blog/media-assets/images/", base + "/media/images/")
    if not image:
        return ""
    alt = item["alt"] if item else p["title"]
    attrs = ' fetchpriority="high" loading="eager"' if eager else ' loading="lazy"'
    responsive = ""
    if item:
        css_class = (css_class + ' photo-' + item['key']).strip()
        candidates = ", ".join(base + v["path"] + " " + str(v["width"]) + "w" for v in item["variants"])
        sizes = sizes or '(max-width: 800px) calc(100vw - 40px), 752px'
        responsive = f' srcset="{html.escape(candidates)}" sizes="{html.escape(sizes, quote=True)}"'
    intrinsic_width, intrinsic_height = (item['variants'][-1]['width'], item['variants'][-1]['height']) if item else (1200, 800)
    image_tag = f'<img class="{html.escape(css_class)}" src="{html.escape(image)}" alt="{html.escape(alt)}"{responsive}{attrs} width="{intrinsic_width}" height="{intrinsic_height}">'
    if not caption or not item:
        return image_tag
    credit = (f'<figcaption>Photo: {html.escape(item["author"])} · '
              f'<a href="{html.escape(item["source"])}">Source</a> · '
              f'<a href="{html.escape(item["license_url"])}">{html.escape(item["license"])}</a>'
              '</figcaption>')
    return '<figure class="reviewed-photo">' + image_tag + credit + '</figure>'


def write_photo_credits(cfg, base):
    rows = []
    for slug, item in photo_review()["approved"].items():
        rows.append(f'<section class="photo-credit"><h2>{html.escape(item["alt"])}</h2>'
                    f'<p>Photo: {html.escape(item["author"])} · '
                    f'<a href="{html.escape(item["source"])}">Source</a> · '
                    f'<a href="{html.escape(item["license_url"])}">{html.escape(item["license"])}</a></p>'
                    f'<p><a href="{base}/posts/{html.escape(slug)}">Read the guide</a></p></section>')
    content = ('<h1>Photo credits</h1>'
               '<p>Photographers and sources for the photographs used on Land in Korea. '
               'Display copies are resized, and card thumbnails may be cropped. '
               'Share-alike photographs remain under the linked licence.</p>' + ''.join(rows))
    with open(os.path.join(SITE, "photo-credits.html"), "w", encoding="utf-8") as f:
        f.write(page(cfg, base, "Photo credits", "Photographers, sources and licences for Land in Korea photographs.", content, base + "/photo-credits.html"))


def copy_reviewed_photo_assets():
    import hashlib
    import shutil
    for item in photo_review()["approved"].values():
        for variant in item["variants"]:
            filename = os.path.basename(variant["path"])
            candidates = [os.path.join(AUTO, "assets", "reviewed", filename), os.path.join(AUTO, "assets", filename)]
            source = next((path for path in candidates if os.path.isfile(path)), None)
            if source is None:
                raise RuntimeError("Reviewed photograph asset missing: " + filename)
            with open(source, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != variant["sha256"]:
                    raise RuntimeError("Reviewed photograph asset differs: " + filename)
            destination = os.path.join(SITE, *variant["path"].lstrip("/").split("/"))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(source, destination)


def read_posts(cfg):
    posts = []
    if not os.path.isdir(POSTS_SRC):
        return posts
    for fn in os.listdir(POSTS_SRC):
        if not fn.endswith(".md"):
            continue
        with open(os.path.join(POSTS_SRC, fn), encoding="utf-8") as f:
            raw = f.read()
        meta, body = parse_front_matter(raw)
        body = apply_affiliate_tokens(body, cfg)
        meta.setdefault("slug", fn[:-3])
        meta.setdefault("title", meta["slug"])
        meta.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
        meta.setdefault("category", "Comparisons")
        meta.setdefault("description", meta["title"])
        meta["_body_html"] = md_to_html(body, cfg["base_url"].rstrip("/"))
        meta["_reading"] = max(1, len(body) // 1000)
        meta["_faq"] = extract_faq(body)
        apply_photo_review(meta, cfg)
        discovery_meta(meta)
        posts.append(meta)
    posts.sort(key=lambda p: (p["date"], p["slug"]), reverse=True)
    return posts


def copy_logo_asset():
    """Copies automation/assets/logo.jpg -> site/assets/logo.jpg when the real file has been added."""
    src = os.path.join(AUTO, "assets", "logo.jpg")
    if not os.path.exists(src):
        return
    dst_dir = os.path.join(SITE, "assets")
    os.makedirs(dst_dir, exist_ok=True)
    with open(src, "rb") as fsrc, open(os.path.join(dst_dir, "logo.jpg"), "wb") as fdst:
        fdst.write(fsrc.read())


def remove_stale_posts(posts):
    """content/posts/에서 삭제되거나 슬러그가 바뀐 글의 예전 HTML이 site/posts/에
    그대로 남아 있으면(빌드는 새 파일만 쓰고 지우지 않으므로) 중복/죽은 URL이
    계속 라이브로 남는다 — 2026-09-04, 중복 콘텐츠 정리 중 실측."""
    if not os.path.isdir(POSTS_OUT):
        return
    valid = {f'{p["slug"]}.html' for p in posts}
    for fn in os.listdir(POSTS_OUT):
        if fn.endswith(".html") and fn not in valid:
            os.remove(os.path.join(POSTS_OUT, fn))
            print(f"Removed stale post output: {fn}")


def build():
    cfg = load_config()
    os.makedirs(POSTS_OUT, exist_ok=True)
    copy_logo_asset()
    copy_reviewed_photo_assets()
    posts = read_posts(cfg)
    remove_stale_posts(posts)
    base = cfg["base_url"].rstrip("/")

    for p in posts:
        hero = ""
        if p.get("image") and p['image'] not in p['_body_html']:
            hero = reviewed_photo_markup(p, base, "hero-img", eager=True, caption=True)
        post_url = canonical_url(base, f'{base}/posts/{p["slug"]}.html')
        article = (
            f'<nav class="breadcrumbs" aria-label="Breadcrumb"><a href="{base}/">Home</a>'
            f' <span aria-hidden="true">/</span> <a href="{base}/topics/{p["_topic"]}">{html.escape(p["_topic_label"])}</a>'
            f' <span aria-hidden="true">/</span> <span aria-current="page">{html.escape(p["title"])}</span></nav>'
            f'<article>' 
            f'<p class="meta"><a class="cat" href="{base}/topics/{p["_topic"]}">{html.escape(p["_topic_label"])}</a>'
            f' · {p["date"]}'
            + (f' · Updated {p["modified"]}' if p.get("modified") else "")
            + f' · {p["_reading"]} min read</p>'
            + f'<h1>{html.escape(p["title"])}</h1>'
            f'<p class="byline">By {html.escape(cfg["author"])} · '
            f'<a href="{base}/about.html">Editorial policy</a></p>'
            f'{hero}'
            f'{p["_body_html"]}'
            f'</article>'
            f'{related_guides(p, posts, base)}'
            f'<p class="back"><a href="{base}/guides">← Back to all guides</a></p>'
        )
        graph = site_entities(cfg, base) + [{
            "@type": "Article",
            "@id": post_url + "#article",
            "url": post_url,
            "inLanguage": cfg["language"],
            "isPartOf": {"@id": base + "/#website"},
            "headline": p["title"],
            "description": p["description"],
            "articleSection": p["_topic_label"],
            "datePublished": p["date"],
            "dateModified": p.get("modified", p["date"]),
            "author": {"@id": base + "/#editorial-team", "@type": "Organization", "name": cfg["author"], "url": base + "/about"},
            "publisher": {"@id": base + "/#organization", "@type": "Organization", "name": cfg["site_name"]},
            **({"image": p["image"]} if p.get("image") else {}),
            "mainEntityOfPage": {"@type": "WebPage", "@id": post_url}
        }]
        graph.append({"@type": "BreadcrumbList", "@id": post_url + "#breadcrumb",
                      "itemListElement": [
                          {"@type": "ListItem", "position": 1, "name": "Home", "item": base + "/"},
                          {"@type": "ListItem", "position": 2, "name": p["_topic_label"], "item": base + "/topics/" + p["_topic"]},
                          {"@type": "ListItem", "position": 3, "name": p["title"], "item": post_url},
                      ]})
        if p.get("_faq"):
            graph.append({
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": item["question"],
                        "acceptedAnswer": {"@type": "Answer", "text": item["answer"]}
                    }
                    for item in p["_faq"]
                ]
            })
        article_jsonld = jsonld_data(graph)
        # raw.githubusercontent.com has been unreliable for this repo (confirmed
        # 2026-10-23: the GitHub Contents API reports a file that raw.githubusercontent.com
        # 404s even on a fresh, cache-busted request) — og:image needs to resolve for link
        # previews, so point it at the same mirrored /media/images/ path the on-page
        # thumbnail already uses instead of hotlinking GitHub directly.
        og_image = p.get("image", "").replace(
            "https://raw.githubusercontent.com/solusupport-bot/land-in-korea-blog/media-assets/images/",
            base + "/media/images/")
        out = page(cfg, base, p["title"], p["description"], article,
                   f'{base}/posts/{p["slug"]}.html', is_post=True, og_type="article",
                   image=og_image, published=p["date"], jsonld=article_jsonld)
        with open(os.path.join(POSTS_OUT, f'{p["slug"]}.html'), "w", encoding="utf-8") as f:
            f.write(out)

    # The approved homepage has isolated assets; articles keep their existing templates.
    home_image = base + "/home-ui/assets/busan.jpg"
    home_metadata = page(cfg, base, "Korea Travel Guide: Destinations, Planning & Tips",
                         "Explore Korea by region, find neighbourhood guides and prepare your trip with practical local advice.",
                         "", f"{base}/", image=home_image)
    write_homepage(cfg, base, posts, home_metadata, SITE)

    write_static_pages(cfg, base)
    write_photo_credits(cfg, base)
    write_discovery(cfg, base, posts)
    write_sitemap(cfg, base, posts)
    write_feed(cfg, base, posts)
    write_robots(base)
    write_cname(cfg)
    write_ads_txt(cfg)
    write_css()

    print(f"Build complete: {len(posts)} posts + static pages")
    print(f"Output: {SITE}")


def write_static_pages(cfg, base):
    affiliate_enabled = cfg.get("affiliate_links_enabled", False)
    about_affiliate_bullet = (
        '<li>Some links are affiliate links (Klook, Trip.com, GetYourGuide) — see our '
        f'<a href="{base}/affiliate-disclosure.html">disclosure</a>.</li>'
        if affiliate_enabled else
        '<li>This site currently carries no affiliate links — every recommendation here is '
        'purely editorial. See our <a href="{base}/affiliate-disclosure.html">disclosure</a> page for '
        'our policy going forward.</li>'.format(base=base)
    )
    about = f"""
<h1>About Land in Korea</h1>
<p>{html.escape(cfg['site_name'])} is an independent travel guide based in Busan, South Korea. We publish practical information for visitors and foreign residents: transport, places to visit, payments and everyday life.</p>
<h2>Our guides</h2>
<p>Guides explain the choices travellers need to make and link to official sources for schedules, prices and procedures. Check the operator or government website before booking: details can change after publication.</p>
<p>AI tools assist with research and drafting. Land in Korea is responsible for the published material. These guides are reference articles; a photograph or description does not mean we personally tested every service.</p>
<h2>Photographs</h2>
<p>Photograph sources and licences are listed in the <a href="{base}/photo-credits.html">photo credits</a>.</p>
<h2>Commercial links</h2>
<ul>{about_affiliate_bullet}</ul>
<h2>Corrections</h2>
<p>Found a wrong detail or an outdated link? Please <a href="{base}/contact.html">contact us</a> with the guide URL and the correction.</p>
"""
    disclosure_status = ('Some links on this site may earn us a commission when you make a booking or purchase. Affiliate links are identified in the relevant guide.'
                         if affiliate_enabled else 'Affiliate links are currently disabled on this site.')
    disclosure = f"""
<h1>Affiliate Disclosure</h1>
<p>{disclosure_status}</p>
<p>Guides may mention booking platforms and service providers to help you compare options. A mention is not a guarantee of price, availability or suitability for your trip.</p>
<p>If we introduce affiliate links or paid placements, they will be clearly labelled. Check the provider’s current price, cancellation terms and service details before purchasing.</p>
<p>Questions about a link or recommendation? Please <a href="{base}/contact.html">contact us</a>.</p>
"""
    goatcounter_bullet = (
        f'<p>We use <a href="https://www.goatcounter.com/" rel="noopener" target="_blank">GoatCounter</a>, '
        'a privacy-respecting, cookieless analytics service, to see how many people visit each page. '
        "GoatCounter does not use cookies and does not track you across other websites. See "
        '<a href="https://www.goatcounter.com/privacy" rel="noopener" target="_blank">GoatCounter\'s privacy '
        'policy</a> for details.</p>'
    )
    privacy = f"""
<h1>Privacy Policy</h1>
<p>Last updated: {POLICY_PAGE_LAST_UPDATED}</p>
<p>{html.escape(cfg['site_name'])} does not require account registration and does not directly collect
personal information such as your name or email address, except when you voluntarily send us a message
through our <a href="{base}/contact.html">contact page</a> or by email.</p>
<h2>Analytics</h2>
{goatcounter_bullet}
<h2>Cookies &amp; third-party tracking</h2>
<p>This site does not currently use affiliate tracking cookies. This site may show ads served through the
Google AdSense program; if ads are displayed, Google and its advertising partners may use cookies to serve
ads based on your visits to this site and other sites. You can opt out of personalized advertising via
<a href="https://adssettings.google.com/" rel="noopener" target="_blank">Google's Ad Settings</a>, and read
more about how Google uses this data at
<a href="https://policies.google.com/technologies/partner-sites" rel="noopener" target="_blank">Google's
partner sites policy</a>. See our <a href="{base}/affiliate-disclosure.html">disclosure</a> page for the
current status of any other tracking.</p>
<h2>Data retention</h2>
<p>Messages sent through our contact page or by email are kept only as long as needed to respond to your
inquiry and are not shared with third parties except where required by law.</p>
<h2>Your rights</h2>
<p>You may ask us what information we hold about you, request a correction, or request deletion at any
time — contact us using the details below.</p>
<h2>Changes to this policy</h2>
<p>We may update this policy as the site evolves. Material changes will be reflected here with an updated
date at the top of this page.</p>
<h2>Contact</h2>
<p>Privacy questions: <a href="mailto:{html.escape(cfg['email'])}">{html.escape(cfg['email'])}</a></p>
""" if not affiliate_enabled else f"""
<h1>Privacy Policy</h1>
<p>Last updated: {POLICY_PAGE_LAST_UPDATED}</p>
<p>{html.escape(cfg['site_name'])} does not require account registration and does not directly collect
personal information such as your name or email address, except when you voluntarily send us a message
through our <a href="{base}/contact.html">contact page</a> or by email.</p>
<h2>Analytics</h2>
{goatcounter_bullet}
<h2>Cookies &amp; affiliate tracking</h2>
<p>Links to Klook, Trip.com, and GetYourGuide may set tracking cookies on their own sites once you click
through, used to attribute bookings to this site. This site may also show ads served through the Google
AdSense program; if ads are displayed, Google and its advertising partners may use cookies to serve ads
based on your visits to this site and other sites. You can opt out of personalized advertising via
<a href="https://adssettings.google.com/" rel="noopener" target="_blank">Google's Ad Settings</a>. We do not
control these third-party cookies — see each platform's own privacy policy for details.</p>
<h2>Data retention</h2>
<p>Messages sent through our contact page or by email are kept only as long as needed to respond to your
inquiry and are not shared with third parties except where required by law.</p>
<h2>Your rights</h2>
<p>You may ask us what information we hold about you, request a correction, or request deletion at any
time — contact us using the details below.</p>
<h2>Changes to this policy</h2>
<p>We may update this policy as the site evolves. Material changes will be reflected here with an updated
date at the top of this page.</p>
<h2>Contact</h2>
<p>Privacy questions: <a href="mailto:{html.escape(cfg['email'])}">{html.escape(cfg['email'])}</a></p>
"""
    terms = f"""
<h1>Terms of Service</h1>
<p>Last updated: {POLICY_PAGE_LAST_UPDATED}</p>
<p>By using {html.escape(cfg['site_name'])} ("the Site"), you agree to the following terms.</p>
<h2>Content accuracy</h2>
<p>We do our best to keep prices, procedures, and rules current, but travel information changes quickly.
Always confirm important details (prices, visa rules, transit schedules) on the relevant official source
before you rely on them for a booking or a trip.</p>
<h2>Not professional advice</h2>
<p>Nothing on this Site is legal, medical, financial, or immigration advice. For anything with real
consequences — visas, taxes, medical concerns — consult a licensed professional or the relevant government
authority.</p>
<h2>Use of content</h2>
<p>You're welcome to link to our articles or share short quoted excerpts with attribution and a link back.
Reproducing full articles elsewhere without permission is not allowed. Contact us if you'd like to reprint
or syndicate something.</p>
<h2>Affiliate links</h2>
{about_affiliate_bullet}
<h2>Limitation of liability</h2>
<p>{html.escape(cfg['site_name'])} is provided "as is" without warranties of any kind. We are not liable
for losses, missed connections, financial cost, or any other outcome resulting from decisions made using
information on this Site.</p>
<h2>External links</h2>
<p>This Site links to third-party services and websites. We are not responsible for the content, accuracy,
or privacy practices of sites we don't control.</p>
<h2>Changes to these terms</h2>
<p>We may revise these terms as the site evolves. Continued use of the Site after a change means you accept
the updated terms.</p>
<h2>Contact</h2>
<p>Questions about these terms: <a href="mailto:{html.escape(cfg['email'])}">{html.escape(cfg['email'])}</a></p>
"""
    contact = f"""
<h1>Contact</h1>
<p>Corrections, partnership questions, or anything else — we'd like to hear from you.</p>
<ul>
<li>Email: <a href="mailto:{html.escape(cfg['email'])}">{html.escape(cfg['email'])}</a></li>
</ul>
<p>We reply within a few business days. We can't provide individual visa, legal, or tax advice.</p>
"""
    # GitHub Pages는 404.html이 없으면 자사 기본 오류 페이지를 보여준다 — 방문자가
    # 사이트를 벗어난 것처럼 보이고, 심사 크롤러에게도 미완성 사이트로 읽힌다.
    not_found = f"""
<h1>Page not found</h1>
<p>That link doesn't point to a guide on this site — it may have been moved, or the address may have a typo.</p>
<p>Try the <a href="{base}/index.html">list of guides</a>, or
<a href="{base}/contact.html">tell us</a> which page you were looking for and we'll fix the link.</p>
"""
    for name, title, body in [
        ("about", "About", about),
        ("affiliate-disclosure", "Affiliate Disclosure", disclosure),
        ("privacy", "Privacy Policy", privacy),
        ("terms-of-service", "Terms of Service", terms),
        ("contact", "Contact", contact),
        ("404", "Page not found", not_found),
    ]:
        with open(os.path.join(SITE, f"{name}.html"), "w", encoding="utf-8") as f:
            f.write(page(cfg, base, title, f"{title} - {cfg['site_name']}", body, f"{base}/{name}.html"))


def write_sitemap(cfg, base, posts):
    # lastmod must be the last time that URL's content actually changed
    # (Google Search Central guidance) — not the build time. Stamping every
    # URL with today's date on every rebuild, regardless of whether that
    # page changed, was inaccurate and made the sitemap look like the whole
    # site rewrites itself daily (2026-09-29, AdSense re-review check).
    latest_post_date = max([LAYOUT_CONTENT_UPDATED, DISCOVERY_UPDATED] + [p["date"] for p in posts])
    static_urls = [
        ("", latest_post_date),  # home lists posts, so it follows the latest post
        ("about.html", LAYOUT_CONTENT_UPDATED),
        ("photo-credits.html", "2026-10-21"),
        ("affiliate-disclosure.html", "2026-10-21"),
        ("privacy.html", POLICY_PAGE_LAST_UPDATED),
        ("terms-of-service.html", POLICY_PAGE_LAST_UPDATED),
        ("contact.html", POLICY_PAGE_LAST_UPDATED),
    ]
    collection_urls = [("guides", latest_post_date)] + [(path, max([DISCOVERY_UPDATED] + [p["date"] for p in group])) for path, _, _, group in discovery_groups(posts)]
    urls = static_urls + collection_urls + [(f"posts/{p['slug']}.html", p.get("modified", p["date"])) for p in posts]
    body = '<?xml version="1.0" encoding="UTF-8"?>\n'
    body += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for u, lastmod in urls:
        loc = canonical_url(base, f"{base}/{u}" if u else f"{base}/")
        body += f"  <url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>\n"
    body += "</urlset>\n"
    with open(os.path.join(SITE, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(body)


def write_feed(cfg, base, posts):
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    for key, value in [("title", cfg["site_name"]), ("link", base + "/"),
                       ("description", cfg["site_tagline"]), ("language", cfg["language"])]:
        ET.SubElement(channel, key).text = value
    for p in posts:
        item = ET.SubElement(channel, "item")
        url = canonical_url(base, f'{base}/posts/{p["slug"]}.html')
        for key, value in [("title", p["title"]), ("link", url),
                           ("description", p["description"]), ("guid", url),
                           ("pubDate", format_datetime(datetime.strptime(p["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)))]:
            ET.SubElement(item, key).text = value
    ET.ElementTree(rss).write(os.path.join(SITE, "feed.xml"), encoding="utf-8", xml_declaration=True)


def write_robots(base):
    with open(os.path.join(SITE, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n")


def write_cname(cfg):
    domain = cfg.get("custom_domain", "").strip()
    cname_path = os.path.join(SITE, "CNAME")
    if domain:
        with open(cname_path, "w", encoding="utf-8") as f:
            f.write(domain + "\n")
    elif os.path.exists(cname_path):
        os.remove(cname_path)


def write_ads_txt(cfg):
    pub_id = cfg.get("adsense_publisher_id", "").strip()
    ads_path = os.path.join(SITE, "ads.txt")
    if pub_id:
        with open(ads_path, "w", encoding="utf-8") as f:
            f.write(f"google.com, {pub_id.removeprefix('ca-')}, DIRECT, f08c47fec0942fa0\n")
    elif os.path.exists(ads_path):
        os.remove(ads_path)


def write_css():
    # Palette pulled from the Land in Korea logo: cream/ivory paper background,
    # near-black ink for text and the hanok-gate mark, gold/red/blue dancheong
    # accents used sparingly for links, category chips, and headings.
    css = """:root{
  --fg:#211f1a;--muted:#6d6a60;--bg:#faf6ec;--soft:#f1e9d8;--card:#ffffff;
  --gold:#1f7a5c;--red:#d9603a;--blue:#1f7a5c;--green:#1f7a5c;--line:#e3dac2;
  --font-display:'Manrope',Arial,'Helvetica Neue',sans-serif;
  --font-body:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif
}
*{box-sizing:border-box}
body{margin:0;font-family:var(--font-body);color:var(--fg);background:var(--bg);line-height:1.75}
h1,h2,h3{font-family:var(--font-display)}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
a:focus-visible,button:focus-visible,.card:focus-visible{outline:2px solid var(--blue);outline-offset:3px;border-radius:2px}
.site-header{border-bottom:2px solid var(--gold);padding:30px 20px;text-align:center;background:var(--card)}
.brand{font-family:var(--font-display);font-size:1.6rem;font-weight:800;color:var(--fg);display:inline-flex;align-items:center;gap:10px}
.brand-mark{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:8px;
  background:linear-gradient(180deg,var(--red),var(--blue));color:#f4efe3;font-size:1.1rem}
.brand-logo{height:96px;width:auto;display:block;border-radius:12px;box-shadow:0 6px 18px rgba(34,31,26,.16)}
.tagline{color:var(--muted);margin:8px 0 14px;max-width:480px;margin-left:auto;margin-right:auto}
nav a{margin:0 10px;font-weight:600;font-size:.92rem;color:var(--fg)}
nav a:hover{color:var(--red)}
main{max-width:760px;margin:0 auto;padding:28px 20px}
.hero{text-align:center;padding:24px 0 8px}
.hero h1{font-size:1.9rem;margin:.2em 0;color:var(--fg)}
.hero-sub{text-align:left;max-width:640px;margin:12px auto 0;font-size:.95rem;line-height:1.7;color:var(--muted)}
.grid{display:grid;grid-template-columns:1fr;gap:16px;margin-top:20px}
@media(min-width:640px){.grid{grid-template-columns:1fr 1fr}}
.card{display:block;border:1px solid var(--line);border-radius:14px;overflow:hidden;background:var(--card);transition:.15s}
.card:hover{transform:translateY(-2px);text-decoration:none;box-shadow:0 8px 22px rgba(34,31,26,.12)}
.card-thumb{width:100%;aspect-ratio:16/10;object-fit:cover;display:block}
.card h2,.card p,.card .cat,.card .date{padding-left:18px;padding-right:18px}
.card h2{font-size:1.12rem;margin:12px 0 8px}
.card p{color:var(--muted);font-size:.94rem;margin:6px 0}
.card .date{display:block;padding-bottom:16px}
.cat{display:inline-block;font-size:.75rem;font-weight:700;color:#fff;background:var(--blue);padding:3px 10px;border-radius:999px;margin-top:16px}
.cat[data-cat="comparisons"]{background:var(--blue)}
.cat[data-cat="money-saving"]{background:var(--green)}
.cat[data-cat="etiquette-mistakes"]{background:var(--red)}
.cat[data-cat="airport-transit"]{background:var(--gold);color:var(--fg)}
.cat[data-cat="practical-info"]{background:var(--muted)}
.post .meta .cat{margin-top:0}
.date{font-size:.8rem;color:var(--muted)}
.hero-img{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:12px;margin:16px 0 8px}
.post-img{width:100%;aspect-ratio:16/10;object-fit:cover;border-radius:12px;margin:1.4em 0}
.post h1{font-size:1.7rem;line-height:1.35;color:var(--fg)}
.post h2{margin-top:1.6em;border-left:4px solid var(--gold);padding-left:10px;color:var(--fg)}
.post .meta{color:var(--muted);font-size:.88rem}
.post .byline{color:var(--muted);font-size:.82rem;margin:.3em 0 1.2em}
.post .byline a{color:var(--muted);text-decoration:underline}
.post table{width:100%;border-collapse:collapse;margin:1.2em 0}
.post th,.post td{border:1px solid var(--line);padding:9px 11px;text-align:left}
.post th{background:var(--soft);color:var(--fg)}
.post tbody tr:nth-child(even){background:var(--soft)}
blockquote{border-left:4px solid var(--gold);margin:1.2em 0;padding:4px 16px;color:var(--muted);background:var(--soft)}
code{background:var(--soft);border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-size:.9em;word-break:break-word}
.back{margin-top:32px}
.breadcrumbs{font-size:.82rem;color:var(--muted);margin-bottom:20px;overflow-wrap:anywhere}
.breadcrumbs a{margin:0;font-size:inherit}
.related-guides,.start-here{margin:28px 0;padding:18px 22px;background:var(--card);border:1px solid var(--line);border-radius:14px}
.related-guides h2,.start-here h2{font-size:1.15rem;margin:0 0 12px}
.related-guides li,.start-here li{margin:8px 0}
.start-here ul{display:grid;gap:8px 20px;padding-left:20px}
@media(min-width:640px){.start-here ul{grid-template-columns:1fr 1fr}}

/* 제휴/외부 링크(CTA)는 본문 텍스트 링크와 다르게, 실제 클릭해야 할 버튼처럼 보이게 처리 */
.post p a[rel~="sponsored"]{
  display:inline-block;background:var(--red);color:#fff;font-weight:600;font-size:.92rem;
  padding:9px 16px;border-radius:6px;margin:4px 6px 4px 0
}
.post p a[rel~="sponsored"]:hover{background:var(--gold);text-decoration:none}
.post p a[rel~="sponsored"]:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
.site-footer{border-top:2px solid var(--gold);margin-top:40px;padding:24px 20px;text-align:center;color:var(--muted);font-size:.82rem;background:var(--card)}
.site-footer a{color:var(--muted)}
"""
    with open(os.path.join(SITE, "style.css"), "w", encoding="utf-8") as f:
        f.write(css + DISCOVERY_CSS)


if __name__ == "__main__":
    build()
