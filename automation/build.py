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
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO = os.path.join(BASE, "automation")
POSTS_SRC = os.path.join(BASE, "content", "posts")
SITE = os.path.join(BASE, "site")
POSTS_OUT = os.path.join(SITE, "posts")


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
def link_attrs(href, base_url):
    """
    2026-09-02: 처음엔 본문에 나오는 링크가 전부 제휴 링크였어서 모든 링크에
    rel="sponsored"를 붙여도 문제가 없었다. 이제 글 사이 내부 링크(SEO에 좋은
    관행)를 추가하면서, 내부 링크에까지 "sponsored"를 붙이면 검색엔진에 잘못된
    신호를 주게 된다 — 링크 목적지에 따라 속성을 구분한다.
    """
    if href.startswith(base_url) or href.startswith("/"):
        return ""
    return ' rel="sponsored noopener" target="_blank"'


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


LOGO_ASSET_PATH = os.path.join("assets", "logo.jpg")


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


# ---------- shared layout ----------
def page(cfg, base, title, description, body, canonical, is_post=False,
         og_type="website", image=None, published=None, jsonld=None):
    brand_html = brand_markup(cfg, base)
    nav = (
        f'<a href="{base}/index.html">Home</a>'
        f'<a href="{base}/about.html">About</a>'
        f'<a href="{base}/affiliate-disclosure.html">Disclosure</a>'
        f'<a href="{base}/privacy.html">Privacy</a>'
        f'<a href="{base}/contact.html">Contact</a>'
        f'<a href="{base}/terms-of-service.html">Terms</a>'
    )
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
    return f"""<!doctype html>
<html lang="{cfg['language']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} | {html.escape(cfg['site_name'])}</title>
<meta name="description" content="{html.escape(description)}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="{html.escape(cfg['site_name'])}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(description)}">
<meta property="og:url" content="{canonical}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{html.escape(title)}">
<meta name="twitter:description" content="{html.escape(description)}">
{img_tag}{published_tag}<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Nanum+Myeongjo:wght@400;700;800&family=Inter:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="{base}/style.css">
{goatcounter_script(cfg)}{jsonld_tag}</head>
<body>
<header class="site-header">
  <a class="brand" href="{base}/index.html">
    {brand_html}
  </a>
  <p class="tagline">{html.escape(cfg['site_tagline'])}</p>
  <nav>{nav}</nav>
</header>
<main class="{'post' if is_post else 'page'}">
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
</body>
</html>
"""


FAQ_SECTION = re.compile(r"^##\s+Frequently Asked Questions\s*$", re.MULTILINE)
FAQ_ITEM = re.compile(r"^###\s+(.+?)\s*\n+((?:(?!^###\s|^##\s).+\n?)+)", re.MULTILINE)


def extract_faq(body):
    """마크다운 본문에서 '## Frequently Asked Questions' 아래 '### 질문 / 답변'
    쌍을 뽑아 FAQPage 구조화 데이터에 쓴다 — 애드센스 심사와는 무관하지만
    검색결과 FAQ 리치 스니펫 기회를 늘린다(2026-09-04, 콘텐츠 점검 중 추가)."""
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
        posts.append(meta)
    posts.sort(key=lambda p: p["date"], reverse=True)
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
        article = (
            f'<article>'
            f'<p class="meta"><span class="cat" data-cat="{re.sub(r"[^a-z0-9]+", "-", p["category"].lower()).strip("-")}">{html.escape(p["category"])}</span>'
            f' · {p["date"]} · {p["_reading"]} min read</p>'
            f'<h1>{html.escape(p["title"])}</h1>'
            f'{hero}'
            f'{p["_body_html"]}'
            f'</article>'
            f'<p class="back"><a href="{base}/index.html">← Back to all guides</a></p>'
        )
        graph = [{
            "@type": "Article",
            "headline": p["title"],
            "description": p["description"],
            "datePublished": p["date"],
            "dateModified": p["date"],
            "author": {"@type": "Organization", "name": cfg["author"]},
            "publisher": {"@type": "Organization", "name": cfg["site_name"]},
            "image": p["image"] if p.get("image") else None,
            "mainEntityOfPage": {"@type": "WebPage", "@id": f'{base}/posts/{p["slug"]}.html'}
        }]
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
        article_jsonld = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)
        out = page(cfg, base, p["title"], p["description"], article,
                   f'{base}/posts/{p["slug"]}.html', is_post=True, og_type="article",
                   image=p.get("image"), published=p["date"], jsonld=article_jsonld)
        with open(os.path.join(POSTS_OUT, f'{p["slug"]}.html'), "w", encoding="utf-8") as f:
            f.write(out)

    cat_slug = lambda c: re.sub(r"[^a-z0-9]+", "-", c.lower()).strip("-")

    cards = ""
    for p in posts:
        thumb = f'<img class="card-thumb" src="{html.escape(p["image"])}" alt="{html.escape(p["title"])}">' if p.get("image") else ""
        cards += (
            f'<a class="card" href="{base}/posts/{p["slug"]}.html">'
            f'{thumb}'
            f'<span class="cat" data-cat="{cat_slug(p["category"])}">{html.escape(p["category"])}</span>'
            f'<h2>{html.escape(p["title"])}</h2>'
            f'<p>{html.escape(p["description"])}</p>'
            f'<span class="date">{p["date"]}</span>'
            f'</a>'
        )
    intro = (f'<section class="hero"><h1>{html.escape(cfg["site_name"])}</h1>'
             f'<p>Pick the situation you\'re actually facing — every guide below runs the real comparison, not another list of tips.</p>'
             f'<p class="hero-sub">Most Korea travel advice tells you what exists. It rarely tells you which option is '
             f'right for <em>your</em> trip length, group size, or budget — so you end up guessing at the airport counter '
             f'anyway. These guides are written the other way round: each one takes a single decision a first-time visitor '
             f'or new foreign resident actually has to make — airport transfer, data plan, transit card, tax refund, '
             f'attraction pass — lays the options side by side, and says plainly which one wins in which situation, '
             f'including when the answer is "none of them, skip it."</p>'
             f'<p class="hero-sub">Prices, opening hours, and procedures in Korea change often. Every guide notes where a '
             f'detail is time-sensitive and points you to the official source to confirm before you book. We correct '
             f'errors when readers report them — see the <a href="{base}/about.html">about page</a> for how we work, '
             f'or <a href="{base}/contact.html">tell us</a> if something here is out of date.</p></section>'
             f'<section class="grid">{cards or "<p>No guides yet.</p>"}</section>')
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(page(cfg, base, "Home", cfg["site_tagline"], intro, f"{base}/index.html"))

    write_static_pages(cfg, base)
    write_sitemap(cfg, base, posts)
    write_robots(base)
    write_cname(cfg)
    write_css()

    print(f"Build complete: {len(posts)} posts + static pages")
    print(f"Output: {SITE}")


def write_static_pages(cfg, base):
    affiliate_enabled = cfg.get("affiliate_links_enabled", False)
    about_affiliate_bullet = (
        '<li>Some links are affiliate links (Klook, Trip.com, GetYourGuide) — see our '
        f'<a href="{base}/affiliate-disclosure.html">disclosure</a>.</li>'
        if affiliate_enabled else
        '<li>This site currently carries no affiliate links or ads — every recommendation here is '
        'purely editorial. See our <a href="{base}/affiliate-disclosure.html">disclosure</a> page for '
        'our policy going forward.</li>'.format(base=base)
    )
    about = f"""
<h1>About Land in Korea</h1>
<p><strong>{html.escape(cfg['site_name'])}</strong> writes for people landing in Korea for the first time —
tourists and new foreign residents alike. We don't repeat the same generic "how to use T-money" listicle
you've already seen a hundred times. Instead, we run the actual comparisons: which airport transfer is
worth the money, which eSIM option is genuinely cheaper, whether a tour pass pays for itself.</p>
<h2>Who writes this</h2>
<p>{html.escape(cfg['site_name'])} is written and maintained by a long-term Seoul resident with over a
decade of direct, on-the-ground experience navigating Korean transit, banking, healthcare, and everyday
etiquette as a foreign resident. Every guide on this site reflects something we've personally used, tested,
or verified — not information copied from other travel sites. When prices, procedures, or app names change,
we go back and confirm before republishing rather than leaving outdated advice live.</p>
<h2>How we work</h2>
<ul>
<li>We compare real options side by side instead of just describing one.</li>
<li>Every claim is grounded in direct experience or verifiable public information — we don't invent details
to fill space.</li>
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
content. As of {datetime.now().strftime('%Y-%m-%d')}, <code>affiliate_links_enabled</code> is set to
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
<p>Last updated: {datetime.now().strftime('%Y-%m-%d')}</p>
<p>{html.escape(cfg['site_name'])} does not require account registration and does not directly collect
personal information such as your name or email address, except when you voluntarily send us a message
through our <a href="{base}/contact.html">contact page</a> or by email.</p>
<h2>Analytics</h2>
{goatcounter_bullet}
<h2>Cookies &amp; third-party tracking</h2>
<p>This site does not currently use affiliate tracking or advertising cookies. If that changes, this page
will be updated to reflect exactly what is added — see our
<a href="{base}/affiliate-disclosure.html">disclosure</a> page for the current status.</p>
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
<p>Last updated: {datetime.now().strftime('%Y-%m-%d')}</p>
<p>{html.escape(cfg['site_name'])} does not require account registration and does not directly collect
personal information such as your name or email address, except when you voluntarily send us a message
through our <a href="{base}/contact.html">contact page</a> or by email.</p>
<h2>Analytics</h2>
{goatcounter_bullet}
<h2>Cookies &amp; affiliate tracking</h2>
<p>Links to Klook, Trip.com, and GetYourGuide may set tracking cookies on their own sites once you click
through, used to attribute bookings to this site. We do not control these third-party cookies — see each
platform's own privacy policy for details.</p>
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
<p>Last updated: {datetime.now().strftime('%Y-%m-%d')}</p>
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
    for name, title, body in [
        ("about", "About", about),
        ("affiliate-disclosure", "Affiliate Disclosure", disclosure),
        ("privacy", "Privacy Policy", privacy),
        ("terms-of-service", "Terms of Service", terms),
        ("contact", "Contact", contact),
    ]:
        with open(os.path.join(SITE, f"{name}.html"), "w", encoding="utf-8") as f:
            f.write(page(cfg, base, title, f"{title} - {cfg['site_name']}", body, f"{base}/{name}.html"))


def write_sitemap(cfg, base, posts):
    urls = ["", "about.html", "affiliate-disclosure.html", "privacy.html", "terms-of-service.html", "contact.html"]
    urls += [f"posts/{p['slug']}.html" for p in posts]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = '<?xml version="1.0" encoding="UTF-8"?>\n'
    body += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for u in urls:
        loc = f"{base}/{u}" if u else f"{base}/"
        body += f"  <url><loc>{loc}</loc><lastmod>{today}</lastmod></url>\n"
    body += "</urlset>\n"
    with open(os.path.join(SITE, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(body)


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
.post table{width:100%;border-collapse:collapse;margin:1.2em 0}
.post th,.post td{border:1px solid var(--line);padding:9px 11px;text-align:left}
.post th{background:var(--soft);color:var(--fg)}
.post tbody tr:nth-child(even){background:var(--soft)}
blockquote{border-left:4px solid var(--gold);margin:1.2em 0;padding:4px 16px;color:var(--muted);background:var(--soft)}
code{background:var(--soft);border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-size:.9em;word-break:break-word}
.back{margin-top:32px}

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
        f.write(css)


if __name__ == "__main__":
    build()
