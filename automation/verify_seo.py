#!/usr/bin/env python3
"""Check generated HTML, sitemap and RSS without network access (stdlib only)."""
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
        self.links, self.canonical, self.graphs, self.robots = [], [], [], []
        self.h1 = self.related_links = 0
        self.in_json = self.in_related = False
        self.buffer = ''
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
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
            self.graphs.extend(json.loads(self.buffer)['@graph'])
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
        if path == '/':
            return SITE / 'index.html'
        p = SITE / path.lstrip('/')
        return p if p.suffix else p.with_suffix('.html')

    docs = {}
    for path in SITE.rglob('*.html'):
        relative = path.relative_to(SITE).as_posix()
        doc = Document(path.read_text(encoding='utf-8'))
        docs[relative] = doc
        check(doc.h1 == 1, f'{relative}: expected one H1, got {doc.h1}')
        expected = base + ('/' if relative == 'index.html' else '/' + relative[:-5])
        check(doc.canonical == [expected], f'{relative}: canonical mismatch')
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
                check(article[0]['dateModified'] == article[0]['datePublished'], f'{relative}: publication date changed')
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
        source_dates[base + '/posts/' + slug] = meta['date'].strip()
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
