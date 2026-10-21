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
            out.append(f'<img class="post-img" loading="lazy" src="{html.escape(src)}" alt="{html.escape(alt)}">')
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
DISCOVERY_UPDATED = "2026-10-21"
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
            f'<a href="{base}/guides">Travel essentials</a>'
            f'<a href="{base}/topics/food-and-culture">Food &amp; culture</a>'
            f'<a href="{base}/topics/living-in-korea">Living in Korea</a>')


def guide_card(p, base):
    # Collection pages use the same existing localized media as article pages.
    image = p.get("image", "").replace("https://raw.githubusercontent.com/solusupport-bot/land-in-korea-blog/media-assets/images/", base+"/media/images/")
    thumb = (f'<img class="card-thumb" src="{html.escape(image)}" alt="" loading="lazy" width="640" height="400">'
             if image else f'<div class="card-placeholder" aria-hidden="true"><span>LAND IN KOREA</span><b>{html.escape(p["_topic_label"])}</b></div>')
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
    links = ''.join(f'<a href="{base}/topics/{key}"' + (' aria-current="page"' if path == "topics/"+key else "") + f'>{html.escape(value[0])}</a>' for key,value in TOPICS.items() if any(p["_topic"]==key for p in all_posts))
    body = (f'<nav class="breadcrumbs" aria-label="Breadcrumb"><a href="{base}/">Home</a> / '
            f'<span aria-current="page">{html.escape(title)}</span></nav>'
            f'<section class="collection-intro"><p class="eyebrow">THE GUIDE LIBRARY</p><h1>{html.escape(title)}</h1>'
            f'<p>{html.escape(description)}</p><p class="collection-count">{len(posts)} guides</p></section>'
            f'<nav class="topic-pills" aria-label="Browse by topic">{links}</nav>'
            f'{discovery_search()}<section class="grid" aria-label="Guides">'+''.join(guide_card(p,base) for p in posts)+'</section>'
            '<p id="search-empty" hidden>No guides match that search. Try a place or a shorter phrase.</p>'
            f'<p class="back"><a href="{base}/guides">Browse all guides →</a></p>')
    url = base+"/"+path
    graph = site_entities(cfg,base) + [{"@type":"CollectionPage", "@id":url+"#collection", "url":url,
        "name":title,"description":description,"isPartOf":{"@id":base+"/#website"},
        "mainEntity":{"@type":"ItemList","itemListElement":[{"@type":"ListItem","position":i+1,"url":base+"/posts/"+p["slug"],"name":p["title"]} for i,p in enumerate(posts)]}},
        {"@type":"BreadcrumbList","itemListElement":[{"@type":"ListItem","position":1,"name":"Home","item":base+"/"},{"@type":"ListItem","position":2,"name":title,"item":url}]}]
    return page(cfg,base,title,description,body,url,jsonld=jsonld_data(graph))


def discovery_home(cfg, base, posts):
    topic_summaries = {
        'before-you-go': 'Entry, packing and planning',
        'getting-around': 'Airport, trains and transport',
        'sim-and-apps': 'Mobile data, maps and useful apps',
        'money-and-shopping': 'Cards, cash and tax refunds',
        'food-and-culture': 'Ordering, etiquette and local life',
        'things-to-do': 'Places, experiences and day trips',
        'living-in-korea': 'Banking, housing and daily life',
    }
    available = {p['_topic'] for p in posts}
    topics = ''.join(f'<a class="topic-link" href="{base}/topics/{key}"><h3>{html.escape(label)}</h3><p>{topic_summaries[key]}</p></a>'
                     for key,(label,_) in TOPICS.items() if key in available)
    lookup = {p['slug']:p for p in posts}
    destinations = ''
    for key,label,description,slug in [
        ('seoul','Seoul','Palaces, neighbourhoods and nearby escapes','korea-gyeongbokgung-palace'),
        ('busan','Busan','Coastal walks and colourful hillside streets','korea-busan-haeundae-beach-gamcheon-culture-village'),
        ('jeju','Jeju','Island landscapes and getting there','korea-jeju-island'),
    ]:
        if not any(key in p['_regions'] for p in posts):
            continue
        p = lookup.get(slug)
        image = p.get('image','') if p else ''
        image = image.replace('https://raw.githubusercontent.com/solusupport-bot/land-in-korea-blog/media-assets/images/',base+'/media/images/')
        pic = f'<img src="{html.escape(image)}" alt="" width="357" height="220" loading="lazy">' if image else ''
        destinations += f'<a class="destination-story" href="{base}/destinations/{key}">{pic}<h3>{label}</h3><p>{description}</p></a>'
    featured = [
        ('korea-airport-transfer-options','Compare cost, time and convenience before choosing your airport transfer.'),
        ('korea-esim-mobile-data','Find the right connection for your phone and trip.'),
        ('korea-currency-card-payments','Prepare for payments and currency exchange.'),
        ('korea-eating-alone-in-korea-which-restaurants-refuse-solo-diners-and-where-honbap-works','Understand which meals and restaurants work for a table of one.'),
    ]
    guides = ''
    for slug,description in featured:
        if slug not in lookup:
            continue
        p=lookup[slug]
        guides += (f'<a class="guide-entry" href="{base}/posts/{slug}"><span>{html.escape(p["_topic_label"])}</span>'
                   f'<h3>{html.escape(p["title"])}</h3><p>{description}</p></a>')
    checklist = ''.join(f'<li><a href="{base}/posts/{slug}">{label}</a></li>' for label,slug in [
        ('Check entry requirements','korea-k-eta-visa-waiver-requirements'),
        ('Plan your airport transfer','korea-airport-transfer-options'),
        ('Get connected with a SIM or eSIM','korea-esim-mobile-data'),
        ('Prepare your transit card','korea-t-money-transit-card'),
    ] if slug in lookup)
    return (f'<section class="home-intro"><div><h1>Your Korea travel guide</h1><p>Practical help for the trip you want to take.</p></div>'
            f'<form id="home-guide-search-form" class="home-search" role="search" action="{base}/guides">'
            '<label class="sr-only" for="home-guide-search">Search Korea travel guides</label>'
            '<input id="home-guide-search" type="search" placeholder="Search places, transport or eSIM" aria-describedby="home-search-help">'
            '<span class="sr-only" id="home-search-help">Press Enter to search the guide library.</span></form></section>'
            f'<section id="topics" class="home-section"><h2>What do you need?</h2><div class="topic-links">{topics}</div></section>'
            f'<section id="destinations" class="home-section"><h2>Explore Korea</h2><div class="destination-stories">{destinations}</div></section>'
            f'<section class="home-section practical-stories"><h2>Make the everyday parts easier</h2><div class="guide-entries">{guides}</div></section>'
            f'<section class="first-trip"><h2>First time in Korea? Start here.</h2><ul>{checklist}</ul></section>')


def write_discovery(cfg,base,posts):
    groups = [("guides", "Korea Travel Guide Library", "Browse all our Korea guides, or search by place, topic and the problem you need to solve.",posts)] + discovery_groups(posts)
    for path,title,desc,group in groups:
        dest = os.path.join(SITE,*path.split('/'))+".html"
        os.makedirs(os.path.dirname(dest),exist_ok=True)
        with open(dest,'w',encoding='utf-8') as f:
            f.write(collection_page(cfg,base,group,title,desc,path,posts))
    with open(os.path.join(SITE,'guide-search.js'),'w',encoding='utf-8') as f:
        f.write('''(() => {
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
/* Figma: rCBpKhCuykMAK9TYE2ZIvV · desktop 4:33 / mobile 4:34. */
:root{--fg:#202321;--muted:#666b66;--bg:#fff;--card:#fff;--soft:#f5f2ea;--blue:#202321;--red:#a84132;--gold:#b8923f;--line:#dedfd9;--font-body:'Noto Sans',Arial,sans-serif;--font-display:var(--font-body)}
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
{img_tag}{published_tag}<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans:wght@400;500;700&display=swap">
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
    posts = read_posts(cfg)
    remove_stale_posts(posts)
    base = cfg["base_url"].rstrip("/")

    for p in posts:
        hero = ""
        if p.get("image"):
            hero = f'<img class="hero-img" src="{html.escape(p["image"])}" alt="{html.escape(p["title"])}">'
        post_url = canonical_url(base, f'{base}/posts/{p["slug"]}.html')
        article = (
            f'<nav class="breadcrumbs" aria-label="Breadcrumb"><a href="{base}/">Home</a>'
            f' <span aria-hidden="true">/</span> <a href="{base}/topics/{p["_topic"]}">{html.escape(p["_topic_label"])}</a>'
            f' <span aria-hidden="true">/</span> <span aria-current="page">{html.escape(p["title"])}</span></nav>'
            f'<article>' 
            f'<p class="meta"><a class="cat" href="{base}/topics/{p["_topic"]}">{html.escape(p["_topic_label"])}</a>'
            f' · {p["date"]} · {p["_reading"]} min read</p>'
            f'<h1>{html.escape(p["title"])}</h1>'
            f'<p class="byline">Written by {html.escape(cfg["author"])} · checked against official sources — '
            f'see <a href="{base}/about.html">how we work</a></p>'
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
            "dateModified": p["date"],
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
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(page(cfg, base, "Korea Travel Guides: Transport, eSIMs and Money",
                     "Plan your first trip to Korea with practical comparisons of airport transfers, eSIMs, transit cards, payments and luggage storage. Check official sources.",
                     intro, f"{base}/"))

    write_static_pages(cfg, base)
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
<p><strong>{html.escape(cfg['site_name'])}</strong> writes for people landing in Korea for the first time —
tourists and new foreign residents alike. We don't pad guides with generic filler just to hit a word
count. Instead, we run the actual comparisons: which airport transfer is worth the money, which eSIM
option is genuinely cheaper, whether a tour pass pays for itself.</p>
<h2>Who writes this</h2>
<p>{html.escape(cfg['site_name'])} is an independent editorial project run from Busan. Guides are researched
and compiled from official Korean sources — the Korea Tourism Organization, operators such as Korail and the
airport railroad, the National Tax Service's tax-refund rules, and the transit and map apps themselves —
combined with local familiarity with how those systems work in practice.</p>
<p>Each guide is compiled from those sources and checked against them before it goes live. We say this plainly
because we would rather you know how the site is made than imply a first-hand travel diary it isn't: what we
promise is that every specific in these guides traces back to a source we can point to, not that a person
personally re-tested each fare and opening time the week you read it. Where a detail is time-sensitive —
prices, thresholds, seasonal schedules — the guide says so and sends you to the official source to confirm.</p>
<p>In the interest of being upfront about how this is made: drafting uses AI assistance over the source
material above, and publishing is handled by an automated pipeline on a schedule. What doesn't change is
the standard — a draft only goes live after it's checked against the sources it cites, not on AI output
alone.</p>
<h2>How we work</h2>
<ul>
<li>We compare real options side by side instead of just describing one.</li>
<li>Every claim is grounded in verifiable public information from an official or operator source — we don't
invent details to fill space.</li>
{about_affiliate_bullet}
<li>Prices, thresholds, and rules change — always confirm on the official site before you book or travel.</li>
</ul>
<h2>Editorial standards</h2>
<p>We correct errors when readers point them out, and we don't accept payment in exchange for a positive
review or a favorable comparison result. If you spot something outdated or wrong, tell us — see our
<a href="{base}/contact.html">contact page</a>.</p>
<p>Contact: <a href="{base}/contact.html">contact page</a></p>
"""
    disclosure = f"""
<h1>Affiliate Disclosure</h1>
<p><strong>Current status: affiliate links are disabled.</strong></p>
<p>{html.escape(cfg['site_name'])} uses a token-based affiliate link system managed in configuration, not in
content. As of {POLICY_PAGE_LAST_UPDATED}, <code>affiliate_links_enabled</code> is set to
<code>false</code> in our build configuration, so all affiliate references are displayed as plain text
without links. When enabled, this will be clearly indicated on each post.</p>
<h2>What "token-based" means here</h2>
<p>Some of our guides mention booking platforms such as Klook, Trip.com, and GetYourGuide as places to
compare current prices. In the source of those articles, each mention is written as a placeholder token
(for example <code>{{{{klook}}}}</code>) rather than a hard-coded URL. At build time, our generator reads
<code>automation/config.json</code>:</p>
<ul>
<li>While <code>affiliate_links_enabled</code> is <code>false</code>, every token is stripped and the brand
name is rendered as ordinary text — no outbound affiliate link, no tracking parameter, and no ad code is
served anywhere on this site.</li>
<li>If it is ever switched to <code>true</code>, the same tokens resolve to the real partner URLs in one
place, so the disclosure status of the whole site can never drift out of sync with the articles.</li>
</ul>
<h2>Our plan if we activate affiliate links</h2>
<p>We may enable this system in the future. If and when we do: this page will be updated first with the
programs we have joined and the date the change took effect; every post containing such a link will carry a
visible disclosure in the article itself; and the links will be marked <code>rel="sponsored"</code> so
search engines and readers can identify them. Commissions, if any, would come at no extra cost to you, and
our comparisons would still reflect our own research — we do not accept payment for a positive review or a
favorable comparison result.</p>
<p>Questions about a specific recommendation? Reach out via our
<a href="{base}/contact.html">contact page</a>.</p>
""" if not affiliate_enabled else f"""
<h1>Affiliate Disclosure</h1>
<p>{html.escape(cfg['site_name'])} participates in affiliate programs including Klook, Trip.com, and
GetYourGuide. When you click certain links on this site and make a booking or purchase, we may earn a
commission — at no extra cost to you.</p>
<p>We only link to services relevant to the guide you're reading. Our comparisons reflect our own research
and opinion; they are not paid placements unless explicitly marked as such.</p>
<p>Questions about a specific link or recommendation? Reach out via our
<a href="{base}/contact.html">contact page</a>.</p>
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
        ("affiliate-disclosure.html", POLICY_PAGE_LAST_UPDATED),
        ("privacy.html", POLICY_PAGE_LAST_UPDATED),
        ("terms-of-service.html", POLICY_PAGE_LAST_UPDATED),
        ("contact.html", POLICY_PAGE_LAST_UPDATED),
    ]
    collection_urls = [("guides", latest_post_date)] + [(path, max([DISCOVERY_UPDATED] + [p["date"] for p in group])) for path, _, _, group in discovery_groups(posts)]
    urls = static_urls + collection_urls + [(f"posts/{p['slug']}.html", p["date"]) for p in posts]
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
