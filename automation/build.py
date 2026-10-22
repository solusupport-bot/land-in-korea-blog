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
    return (f'<a href="{base}/#destinations">Destinations</a>'
            f'<a href="{base}/#plan-your-trip">Plan a trip</a>'
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
    dokdo = ('<g class="map-island" role="img" aria-label="Ulleungdo, east of the Korean peninsula">'
             '<circle cx="397" cy="134" r="5"/><text x="391" y="113" text-anchor="middle">Ulleungdo</text></g>'
             '<a class="map-island dokdo-marker" href="#dokdo-note" data-island-note aria-label="Dokdo: view island information">'
             '<circle class="island-touch" cx="467" cy="156" r="38"/>'
             '<circle class="island-wave" cx="467" cy="156" r="13"/>'
             '<circle cx="464" cy="155" r="3.5"/><circle cx="470" cy="158" r="3"/>'
             '<text x="467" y="185" text-anchor="middle">Dokdo</text>'
             '<text class="island-korean" x="467" y="204" text-anchor="middle">독도</text></a>')
    return ('<div class="korea-map"><div class="atlas-heading"><p class="map-title">Choose a destination</p></div>'
            '<svg viewBox="0 0 540 510" role="group" aria-labelledby="korea-map-title korea-map-desc">'
            '<title id="korea-map-title">Explore South Korea</title>'
            '<desc id="korea-map-desc">Select Seoul, Busan or Jeju to preview a destination. Follow the View guide link to read its travel information. Ulleungdo and Dokdo are marked in the East Sea; small island markers are enlarged for visibility.</desc>'
            '<path class="korea-coast-shadow" d="'+KOREA_MAP_PATH+'" transform="translate(0 4)" aria-hidden="true"/>'
            '<path class="korea-land" d="'+KOREA_MAP_PATH+'" aria-hidden="true"/>'
            +''.join(pins)+dokdo+'</svg><div class="atlas-footer"><p class="map-note">Select a place, then open its guide.</p></div>'
            '<details id="dokdo-note" class="island-note"><summary>Dokdo · 독도</summary><p>Islands east of Ulleungdo, in the East Sea.</p><a href="https://www.dokdo.re.kr/home/cms/cmsCont.do?cntnts_sn=63" rel="noopener" target="_blank">Official island information ↗</a></details>'
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
    newest = ''.join(f'<a class="recent-story" href="{url(p["slug"])}"><span>{html.escape(p["_topic_label"])}</span>'
                     f'<h3>{html.escape(p["title"])}</h3><time datetime="{html.escape(p["date"], quote=True)}">{html.escape(p["date"])}</time></a>'
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
    return (
        '<div class="travel-home">'
        f'<section class="guide-heading"><div><h1>Plan a trip to Korea</h1><p>Transport, places to visit and the practical details.</p><p class="editorial-location">Based in Busan · Guides to Seoul, Busan and Jeju</p></div>{search}</section>'
        f'<nav class="quick-answers" aria-label="Common Korea travel questions"><p>Need a quick answer?</p><div>{quick_answers}</div></nav>'
        f'<div class="travel-lead">{hero}<aside class="season-panel" aria-labelledby="season-title"><h2 id="season-title">Seasonal travel</h2>{season}<a class="season-start" href="#plan-your-trip">First visit? Start with the essentials <span aria-hidden="true">→</span></a></aside></div>'
        f'<section id="plan-your-trip" class="travel-section preparation"><div class="section-heading"><div><h2>Plan your first trip</h2><p>Six useful starting points, before you land.</p></div></div><div class="prep-grid">{preparation}</div></section>'
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
:root{--fg:#202321;--muted:#666b66;--bg:#fff;--card:#fff;--soft:#f5f2ea;--blue:#202321;--red:#a84132;--gold:#b8923f;--line:#dedfd9;--font-body:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;--font-display:Georgia,'Times New Roman',serif}
[hidden]{display:none!important}
html{scroll-padding-top:20px}
body{font-family:var(--font-body);line-height:1.6;background:#fff;font-size:15px}
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
.home-search{width:350px;flex-shrink:0}.home-search input{width:100%;height:48px;padding:12px 16px;font:inherit;font-size:15px;border:1px solid var(--line);background:white;color:var(--fg);border-radius:0;min-width:0}
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
.guide-search{margin:24px 0;padding:20px;background:var(--soft)}.guide-search label{display:block;font-size:13px;margin-bottom:8px}.search-row{display:flex;gap:8px}.search-row input{flex:1;min-width:0;border:1px solid var(--line);background:white;color:var(--fg);padding:12px;font:inherit;border-radius:0}.search-row button{border:1px solid var(--fg);background:var(--fg);color:white;padding:10px 16px;font:inherit;cursor:pointer}.guide-search p{font-size:12px;color:var(--muted);margin:8px 0 0}
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
.recent-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px}.recent-story{padding-top:16px;border-top:2px solid var(--line);display:flex;flex-direction:column;align-items:start}.recent-story>span{font-size:11px;color:var(--red)}.recent-story h3{font-size:17px;line-height:26px;margin:10px 0 16px}.recent-story time{font-size:11px;color:var(--muted);margin-top:auto}.recent-story:hover{text-decoration:none}.recent-story:hover h3{text-decoration:underline}
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
.search-row button{background:#253b37;border-color:#253b37;border-radius:3px;font-size:13px;font-weight:600;transition:background .18s ease}
.search-row button:hover{background:#3c584d}
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
.destination-cta{display:inline-flex;align-items:center;gap:32px;min-height:44px;background:#253b37;border:1px solid #253b37;border-radius:3px;font-size:13px;font-weight:600;margin-top:20px;padding:10px 17px;color:#fff;transition:background .18s ease}
.destination-cta>span{transition:transform .2s ease}.place-card:hover .destination-cta{background:#3c584d;border-color:#3c584d}.place-card:hover .destination-cta>span{transform:translateX(3px)}
.place-card:active .destination-cta{background:#1b2d27}
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
:root{--font-display:Arial,'Helvetica Neue',sans-serif;--fg:#202824;--muted:#58625b;--line:#dfe3df;--red:#32624e;--soft:#f6f7f4}
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
.destination-cta{border-radius:0;font-size:14px;min-height:46px;padding:11px 18px;margin-top:18px}
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
    review = photo_review()
    slug = meta["slug"]
    approved = review["approved"].get(slug)
    if approved:
        meta["image"] = cfg["base_url"].rstrip("/") + approved["image"]
        meta["_photo"] = approved
    elif slug in review["withheld"] or meta.get("image", "").replace("https://raw.githubusercontent.com/solusupport-bot/land-in-korea-blog/media-assets/images/", cfg["base_url"].rstrip("/") + "/media/images/") != review["legacy_visual_review"].get(slug):
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
            f'<p class="back"><a href="{base}/index.html">← Back to all guides</a></p>'
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
        out = page(cfg, base, p["title"], p["description"], article,
                   f'{base}/posts/{p["slug"]}.html', is_post=True, og_type="article",
                   image=p.get("image"), published=p["date"], jsonld=article_jsonld)
        with open(os.path.join(POSTS_OUT, f'{p["slug"]}.html'), "w", encoding="utf-8") as f:
            f.write(out)

    intro = discovery_home(cfg, base, posts)
    home_image = next((p.get('image', '') for p in posts if p['slug'] == 'korea-nami-island-and-petite-france-day-trip'), '')
    home_image = home_image.replace('https://raw.githubusercontent.com/solusupport-bot/land-in-korea-blog/media-assets/images/', base + '/media/images/')
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(page(cfg, base, "Korea Travel Guide: Destinations, Planning & Tips",
                     "Explore Seoul, Busan and Jeju. Plan your first trip to Korea with guides to transport, eSIMs, payments, food, culture and day trips.",
                     intro, f"{base}/", image=home_image or None))

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
  --fg:#221f1a;--muted:#7a7264;--bg:#f4efe3;--soft:#ece4d3;--card:#faf7ef;
  --gold:#b8923f;--red:#a63a35;--blue:#274b6d;--green:#3f6b45;--line:#ddd2ba;
  --font-display:'Nanum Myeongjo',Georgia,'Times New Roman',serif;
  --font-body:'Inter',-apple-system,'Segoe UI',sans-serif
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
