#!/usr/bin/env python3
"""Check generated HTML, sitemap and RSS without network access (stdlib only)."""
import hashlib
from datetime import date
import importlib.util
import json
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / 'site'


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.links, self.canonical, self.graphs, self.robots, self.images = [], [], [], [], []
        self.h1 = self.related_links = 0
        self.in_json = self.in_related = False
        self.buffer = ''
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'img':
            self.images.append(a.get('src', ''))
        if tag == 'h1':
            self.h1 += 1
        if tag == 'link' and a.get('rel') == 'canonical':
            self.canonical.append(a.get('href'))
        if tag == 'meta' and a.get('name') == 'robots':
            self.robots.append(a.get('content', ''))
        if tag == 'a' and 'href' in a:
            self.links.append(a['href'])
            if self.in_related:
                self.related_links += 1
        if tag == 'section' and a.get('class') == 'related-guides':
            self.in_related = True
        if tag == 'script' and a.get('type') == 'application/ld+json':
            self.in_json, self.buffer = True, ''

    def handle_data(self, data):
        if self.in_json:
            self.buffer += data

    def handle_endtag(self, tag):
        if tag == 'section':
            self.in_related = False
        if tag == 'script' and self.in_json:
            data = json.loads(self.buffer)
            # Most pages wrap their schema.org objects in {"@graph": [...]}; the compare/*
            # pages carry a single bare WebPage object instead.
            self.graphs.extend(data['@graph'] if '@graph' in data else [data])
            self.in_json = False


def main():
    cfg = json.loads((ROOT / 'automation/config.json').read_text(encoding='utf-8'))
    base = cfg['base_url'].rstrip('/')
    errors = []
    def check(condition, message):
        if not condition:
            errors.append(message)

    def target(url):
        path = unquote(urlsplit(url).path)
        if path.endswith('/'):
            # Directory-style URL (e.g. the compare/* pages) resolves to its index.html.
            return SITE / path.strip('/') / 'index.html'
        p = SITE / path.lstrip('/')
        return p if p.suffix else p.with_suffix('.html')

    docs = {}
    for path in SITE.rglob('*.html'):
        relative = path.relative_to(SITE).as_posix()
        doc = Document(path.read_text(encoding='utf-8'))
        docs[relative] = doc
        check(doc.h1 == 1, f'{relative}: expected one H1, got {doc.h1}')
        if relative == 'index.html':
            expected = base + '/'
        elif relative.endswith('/index.html'):
            # Directory-style page (e.g. compare/esim/index.html) -> base/compare/esim/
            expected = base + '/' + relative[:-len('index.html')]
        else:
            expected = base + '/' + relative[:-5]
        check(doc.canonical == [expected], f'{relative}: canonical mismatch')
        if not relative.startswith('compare/'):
            # The compare/* pages intentionally carry only a WebPage schema (see
            # automation/compare_pages.py), not the site-wide WebSite graph entry.
            check(any(g.get('@type') == 'WebSite' for g in doc.graphs), f'{relative}: missing WebSite')
        if relative == '404.html':
            check(any('noindex' in r for r in doc.robots), '404: missing noindex')
        else:
            check(not any('noindex' in r for r in doc.robots), f'{relative}: unexpected noindex')
        for url in doc.links:
            u = urlsplit(url)
            if u.scheme in ('mailto', 'tel') or (u.netloc and u.netloc != urlsplit(base).netloc):
                continue
            if not u.path.startswith('/'):
                continue
            check(target(url).exists(), f'{relative}: broken local link {url}')
        if relative.startswith('posts/'):
            check(doc.related_links == 3, f'{relative}: expected 3 related links')
            article = [g for g in doc.graphs if g.get('@type') == 'Article']
            check(len(article) == 1, f'{relative}: expected one Article')
            if article:
                check(article[0]['mainEntityOfPage']['@id'] == expected, f'{relative}: Article URL mismatch')
                check(article[0]['datePublished'] <= article[0]['dateModified'] <= date.today().isoformat(), f'{relative}: invalid content modification date')
            crumbs = [g for g in doc.graphs if g.get('@type') == 'BreadcrumbList']
            check(len(crumbs) == 1 and crumbs[0]['itemListElement'][-1]['item'] == expected,
                  f'{relative}: breadcrumb mismatch')

    source_slugs = set()
    source_dates = {}
    for path in (ROOT / 'content/posts').glob('*.md'):
        front = path.read_text(encoding='utf-8').split('---', 2)[1]
        meta = dict(line.split(':', 1) for line in front.splitlines() if ':' in line)
        slug = meta.get('slug', path.stem).strip()
        source_slugs.add(slug)
        source_dates[base + '/posts/' + slug] = meta.get('modified', meta['date']).strip()
        source_doc=docs.get('posts/' + slug + '.html')
        if source_doc:
            graph=next((g for g in source_doc.graphs if g.get('@type')=='Article'), {})
            check(graph.get('datePublished') == meta['date'].strip(), slug + ': first publication date differs from source')
            check(graph.get('dateModified') == meta.get('modified', meta['date']).strip(), slug + ': modified date differs from source')
    built_slugs = {Path(p).stem for p in docs if p.startswith('posts/')}
    check(source_slugs == built_slugs, 'Source/output post inventory differs')
    ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    urls = ET.parse(SITE / 'sitemap.xml').getroot().findall('s:url', ns)
    locations = [u.findtext('s:loc', namespaces=ns) for u in urls]
    expected_urls = {d.canonical[0] for p, d in docs.items() if p != '404.html'}
    check(set(locations) == expected_urls and len(locations) == len(expected_urls), 'Sitemap coverage/duplicates')
    for u in urls:
        loc = u.findtext('s:loc', namespaces=ns)
        if loc in source_dates:
            check(u.findtext('s:lastmod', namespaces=ns) == source_dates[loc], f'{loc}: date changed')
    items = ET.parse(SITE / 'feed.xml').findall('./channel/item')
    feed_links = [i.findtext('link') for i in items]
    check(set(feed_links) == set(source_dates) and len(feed_links) == len(source_dates), 'RSS inventory/duplicates')
    check(all(i.findtext('guid') == i.findtext('link') for i in items), 'RSS GUID mismatch')
    # Regression: source text containing a closing script tag must remain inert JSON.
    spec = importlib.util.spec_from_file_location('blog_build', ROOT / 'automation/build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    posts = module.read_posts(cfg)
    for post in posts:
        check(module.re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', post['slug']) is not None,
              post['slug'] + ': invalid published slug; check export sanitization')
        image = post.get('image', '')
        check(not image or (urlsplit(image).scheme in ('http', 'https') and not any(c in image for c in '[]<>')),
              post['slug'] + ': invalid image URL; check export sanitization')
        text = (SITE / 'posts' / (post['slug'] + '.html')).read_text(encoding='utf-8')
        # Layout/navigation must never rewrite the existing editorial body.
        # page() normalizes existing same-site .html links; use the same normalization.
        normalized_body = module.re.sub(r'href="([^"<>]+)"', lambda m: 'href="' + module.canonical_url(base, m.group(1)) + '"', post['_body_html'])
        check(normalized_body in text, post['slug'] + ': editorial body changed')
        check(post['_topic'] in module.TOPICS, post['slug'] + ': unknown topic')
        check(base + '/topics/' + post['_topic'] in text, post['slug'] + ': topic link missing')
    review = module.photo_review()
    if review.get('legacy_images_allowed') is False:
        check(not review['legacy_visual_review'], 'Unverified legacy photo allowlist must be empty')
        # 2026-10-23 user decision (see build.py apply_photo_review): a post's image is
        # legitimate if it is either hand-reviewed ("approved", full Commons provenance)
        # or came from fetch_images.py's own Korea+subject vision check (second tier,
        # accepted by policy, not a review backlog item). read_posts()/apply_photo_review()
        # already resolved each post's correct image under that policy, so trust post['image']
        # as ground truth instead of re-deriving a stricter allowlist here. Approved photos'
        # smaller variants (used as thumbnails on index/topic/destination pages) and the
        # approved homepage's own design assets (home-ui/assets/*, not a content photograph
        # subject to this review policy) are allowed too.
        approved_image_urls = {base + item['image'] for item in review['approved'].values()}
        approved_variant_urls = {base + v['path'] for item in review['approved'].values() for v in item['variants']}
        # build.py rewrites raw.githubusercontent.com media-assets URLs to base+/media/images/
        # right before writing HTML (see build.py page()/line ~402) — post['image'] from
        # read_posts() alone is pre-rewrite, so apply the same substitution here too.
        raw_prefix = 'https://raw.githubusercontent.com/solusupport-bot/land-in-korea-blog/media-assets/images/'
        post_image_urls = {post['image'].replace(raw_prefix, base + '/media/images/') for post in posts if post.get('image')}
        allowed_image_urls = approved_image_urls | approved_variant_urls | post_image_urls
        for path, doc in docs.items():
            for src in doc.images:
                # compare-assets images are disclosed AI-generated illustration (see each
                # compare page's footer credit), not real photography subject to this
                # provenance policy — same exemption class as the home-ui design assets.
                check(src in allowed_image_urls or src == base + '/assets/logo.jpg'
                      or '/home-ui/assets/' in src or '/compare-assets/' in src,
                      path + ': unreviewed photograph remains visible: ' + src)
    checked = date.fromisoformat(review['reviewed_on'])
    cutoff = checked.replace(year=checked.year - 3)
    for slug, photo in review['approved'].items():
        check(cutoff <= date.fromisoformat(photo['date_taken']) <= checked, slug + ': capture date outside review window')
        check(photo['location_verified'] and photo['visual_review'], slug + ': photograph review incomplete')
        check(photo['source'].startswith('https://commons.wikimedia.org/wiki/File:'), slug + ': missing photo source')
        check(photo['license'] in ('CC0 1.0', 'CC BY 2.0', 'CC BY 4.0', 'CC BY-SA 2.0', 'CC BY-SA 4.0'), slug + ': photo license not reviewed')
        page_path = SITE / 'posts' / (slug + '.html')
        check(page_path.exists(), slug + ': reviewed photograph has no article')
        if page_path.exists():
            content = page_path.read_text(encoding='utf-8')
            check(base + photo['image'] in content and photo['source'] in content and photo['license_url'] in content,
                  slug + ': photo or visible attribution missing')
        for variant in photo['variants']:
            check(variant['width'] <= photo['original_dimensions'][0] and variant['height'] <= photo['original_dimensions'][1],
                  slug + ': photo upscaling detected')
            if 'downloaded_source_dimensions' in photo:
                check(variant['width'] <= photo['downloaded_source_dimensions'][0] and variant['height'] <= photo['downloaded_source_dimensions'][1],
                      slug + ': display derivative upscaling detected')
            asset = SITE / variant['path'].lstrip('/')
            # Assets may already live in the preserved deployment media directory.
            if asset.exists():
                check(hashlib.sha256(asset.read_bytes()).hexdigest() == variant['sha256'], slug + ': photo asset hash mismatch')
    for slug in review['withheld']:
        content = (SITE / 'posts' / (slug + '.html')).read_text(encoding='utf-8')
        check('class="hero-img"' not in content, slug + ': withheld image remains visible')
    check('Photo credits' in (SITE / 'index.html').read_text(encoding='utf-8'), 'Missing card photograph credits link')
    all_guides = docs.get('guides.html')
    check(all_guides is not None, 'Missing complete guide library')
    if all_guides:
        check(set(source_dates).issubset(set(all_guides.links)), 'Guide library misses article links')
    for path, label, description, group in module.discovery_groups(posts):
        doc = docs.get(path + '.html')
        check(doc is not None, path + ': missing collection')
        if doc:
            check(all(base + '/posts/' + p['slug'] in doc.links for p in group), path + ': missing guide link')
            lists = [g for g in doc.graphs if g.get('@type') == 'CollectionPage']
            check(len(lists) == 1 and len(lists[0]['mainEntity']['itemListElement']) == len(group), path + ': collection schema differs from visible list')
    home = (SITE / 'index.html').read_text(encoding='utf-8')
    # 2026-10-24: 승인된 홈페이지(지역 아틀라스)가 'topics' 섹션을 'essentials'로 이름을
    # 바꿔 그대로 가지고 있다(실제 topics/* 링크가 그 안에 있음 확인) — 구버전
    # discovery_home()의 id만 보던 체크를 새 구조에 맞게 갱신.
    check('id="essentials"' in home and 'id="destinations"' in home, 'Homepage discovery sections missing')
    check((SITE / 'guide-search.js').exists(), 'Missing progressive search script')
    payload = '</script><script>alert(1)</script>'
    encoded = module.jsonld_data([{'name': payload}])
    check('</script>' not in encoded and json.loads(encoded)['@graph'][0]['name'] == payload,
          'JSON-LD script escaping regression')
    report = {'html_pages': len(docs), 'posts': len(built_slugs), 'sitemap_urls': len(locations),
              'rss_items': len(items), 'errors': errors}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
