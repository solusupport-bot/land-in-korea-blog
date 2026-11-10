"""Add approved comparison pages after the existing blog build, without editing posts."""
from pathlib import Path
from html import escape
import json
import re
import shutil
import xml.etree.ElementTree as ET

BUNDLE = Path(__file__).with_name('compare_bundle')
PAGES = {'esim': ('SIM & eSIM', 'Compare Korea SIM & eSIM Prices'),
         'travel': ('Flights', 'Plan Your Journey to Korea'),
         'stays': ('Hotels', 'Choose Where to Stay in Korea')}
MARKER = 'data-lik-compare'
CSS = '''.lik-compare-links{box-sizing:border-box;margin:2rem auto;padding:1.3rem;max-width:1160px;background:#f4f0e6;border:1px solid #ddd7ca;border-radius:12px;color:#182d3a;font-family:Manrope,system-ui,sans-serif}.lik-compare-links h2{margin:0 0 .6rem;font-size:clamp(1.2rem,2.5vw,1.7rem);line-height:1.3}.lik-compare-links p{line-height:1.6;margin:.5rem 0 1rem}.lik-compare-links nav{display:flex;flex-wrap:wrap;gap:.65rem}.lik-compare-links a{display:inline-flex;align-items:center;min-height:44px;padding:.35rem .85rem;border:1px solid #182d3a;border-radius:6px;color:#182d3a;background:#fff;text-decoration:none;font-weight:700}.lik-compare-links a:hover{background:#182d3a;color:#fff}.lik-compare-links a:focus-visible{outline:3px solid #c35f2d;outline-offset:3px}@media(max-width:600px){.lik-compare-links{margin:1.5rem 1rem;padding:1rem}.lik-compare-links nav{display:grid;grid-template-columns:1fr}.lik-compare-links a{justify-content:space-between}}.lik-compare-home{margin:2.2rem auto 1.2rem;padding:1.6rem;width:calc(100% - 40px)}.lik-compare-home nav{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.8rem}.lik-compare-home a{justify-content:space-between;gap:.8rem;min-height:68px;padding:.8rem 1rem;border-radius:10px;background:#153e55;border-color:#153e55;color:#fff}.lik-compare-home a strong{display:block;font-size:1.05rem}.lik-compare-home a small{display:block;margin-top:.15rem;font-weight:500;font-size:.8rem;opacity:.85}.lik-compare-home a:hover{background:#c35f2d;border-color:#c35f2d}@media(max-width:760px){.lik-compare-home{margin:1.6rem auto 1rem;padding:1.1rem;width:calc(100% - 32px)}.lik-compare-home nav{grid-template-columns:1fr}}'''


HOME_HEADING = 'Three things to book before you land.'
HOME_INTRO = ('Compare Korea eSIM prices, check flight options into each airport and pick a neighbourhood to stay in. '
              'Offers open on each provider’s website.')
HOME_LABELS = {'esim': ('SIM & eSIM', 'Compare prices by days and data'),
               'travel': ('Flights', 'Arrival airports and routes'),
               'stays': ('Hotels', 'Seoul, Busan and Jeju neighbourhoods')}


def link_block(base, categories=None, home=False):
    categories = categories or list(PAGES)
    if home:
        # Homepage: three large choices directly under the search card.
        links = ''.join(
            f'<a href="{escape(base)}/compare/{key}/"><span><strong>{HOME_LABELS[key][0]}</strong>'
            f'<small>{HOME_LABELS[key][1]}</small></span><span aria-hidden="true">→</span></a>' for key in categories)
        return (f'<section class="lik-compare-links lik-compare-home" {MARKER}="section" aria-label="Trip planning tools">'
                f'<h2>{HOME_HEADING}</h2><p>{HOME_INTRO}</p>'
                f'<nav aria-label="Choose a planning tool">{links}</nav></section>')
    links = ''.join(f'<a href="{escape(base)}/compare/{key}/">{PAGES[key][0]} <span aria-hidden="true">↗</span></a>' for key in categories)
    return (f'<section class="lik-compare-links" {MARKER}="section" aria-label="Trip planning tools">'
            '<h2>A few details. A better trip.</h2><p>Compare eSIM reference prices, explore arrival airports and choose a neighbourhood. Check current offers on each provider’s website.</p>'
            f'<nav aria-label="Choose a planning tool">{links}</nav></section>')


def article_categories(path, text):
    # Match the article title/slug, never every mention of a generic word in the body.
    title = re.search(r'<h1\b[^>]*>(.*?)</h1>', text, re.S | re.I)
    value = path.stem + ' ' + (re.sub('<[^>]*>', ' ', title[1]) if title else '')
    keys = []
    if re.search(r'esim|\bsim\b|mobile.data', value, re.I):
        keys.append('esim')
    if re.search(r'airport|flight|k.eta|visa', value, re.I):
        keys.append('travel')
    if re.search(r'hotel|housing|jeju|busan|seoul|hongdae|myeongdong', value, re.I):
        keys.append('stays')
    # Every guide links to the planning tools; the ones matching its topic lead.
    return keys + [key for key in PAGES if key not in keys]


def add_links(text, path, base, is_home=False):
    # Rebuild normally replaces HTML; this cleanup also makes direct reruns idempotent.
    text = re.sub(r'<section\b[^>]*data-lik-compare="section".*?</section>', '', text, flags=re.S)
    text = re.sub(r'<a\b[^>]*data-lik-compare="nav"[^>]*>.*?</a>', '', text, flags=re.S)
    text = re.sub(r'<link\b[^>]*data-lik-compare="style"[^>]*>', '', text)
    navlink = f'<a href="{escape(base)}/compare/esim/" {MARKER}="nav">Compare</a>'
    # Restrict to header navigation; leave breadcrumbs, article content and TOCs intact.
    def header(match):
        return re.sub(r'(<nav\b[^>]*>)(.*?)(</nav>)',
                      lambda n: n[1] + n[2] + navlink + n[3], match[0], flags=re.S | re.I)
    text = re.sub(r'<header\b[^>]*>.*?</header>', header, text, flags=re.S | re.I)
    if is_home:
        # Directly under the hero search card, above the map, so the tools are
        # reachable without scrolling the whole page. Falls back to the old
        # end-of-page position if the template anchor is ever renamed.
        anchor = '<section id="explore-map"'
        block = link_block(base, home=True)
        if anchor in text:
            text = text.replace(anchor, block + anchor, 1)
        else:
            text = text.replace('</main>', block + '</main>', 1)
    elif path.parent.name == 'posts':
        categories = article_categories(path, text)
        if '</article>' in text:
            text = text.replace('</article>', '</article>' + link_block(base, categories), 1)
    elif path.parent.name == 'destinations' and '</main>' in text:
        # A destination page is where a reader chooses where to stay.
        text = text.replace('</main>', link_block(base, ['stays', 'travel', 'esim']) + '</main>', 1)
    elif path.parent.name == 'topics' and '</main>' in text:
        text = text.replace('</main>', link_block(base) + '</main>', 1)
    if MARKER in text:
        text = text.replace('</head>', f'<link rel="stylesheet" href="{escape(base)}/compare-assets/blog-links.css" {MARKER}="style"></head>', 1)
    return text


def write_compare_pages(site, base):
    site, base = Path(site), base.rstrip('/')
    if not BUNDLE.is_dir():
        raise RuntimeError('Missing approved comparison bundle')
    # Namespace assets so the original homepage and blog assets cannot be overwritten.
    target = site / 'compare-assets'
    shutil.copytree(BUNDLE / 'assets', target, dirs_exist_ok=True)
    for path in target.rglob('*'):
        if path.suffix in ('.js', '.css'):
            value = path.read_text(encoding='utf-8').replace('../../assets/', '../../compare-assets/')
            path.write_text(value, encoding='utf-8')
    (target / 'blog-links.css').write_text(CSS, encoding='utf-8')
    for key, (_, title) in PAGES.items():
        dest = site / 'compare' / key / 'index.html'
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = (BUNDLE / 'compare' / key / 'index.html').read_text(encoding='utf-8')
        text = text.replace('../../assets/', '../../compare-assets/').replace('https://landinkorea.com', base)
        text = text.replace('<meta name="robots" content="noindex,follow">', '')
        canonical = base + f'/compare/{key}/'
        meta = (f'<link rel="canonical" href="{escape(canonical)}">'
                f'<meta property="og:type" content="website"><meta property="og:url" content="{escape(canonical)}">'
                f'<meta property="og:title" content="{escape(title)} | Land in Korea">')
        schema = {'@context': 'https://schema.org', '@type': 'WebPage', 'url': canonical,
                  'name': title, 'inLanguage': 'en'}
        text = text.replace('</head>', meta + '<script type="application/ld+json">' + json.dumps(schema) + '</script></head>')
        text = re.sub(r'<title>.*?</title>', f'<title>{escape(title)} | Land in Korea</title>', text, count=1)
        if key == 'travel':
            text = text.replace('Explore flight providers', 'Explore arrival options')
        if key == 'stays':
            text = text.replace('Start with Seoul hotels and check room prices for your dates. Compare the full stay total and cancellation terms.',
                                'Explore neighbourhoods in Seoul, Busan and Jeju. Choose a base for your trip, then check current hotel prices on provider websites.')
        dest.write_text(text, encoding='utf-8')
    # Include homepage, articles and collections, but never mutate article Markdown.
    for path in site.rglob('*.html'):
        if 'compare' not in path.relative_to(site).parts:
            value = path.read_text(encoding='utf-8')
            updated = add_links(value, path, base, path == site / 'index.html')
            if updated != value:
                path.write_text(updated, encoding='utf-8')
    sitemap = site / 'sitemap.xml'
    if not sitemap.exists():
        raise RuntimeError('Run comparison integration after the existing sitemap builder')
    # Append without reserializing or changing existing URL dates or namespaces.
    text = sitemap.read_text(encoding='utf-8')
    ET.fromstring(text)
    for key in PAGES:
        url = base + f'/compare/{key}/'
        if f'<loc>{escape(url)}</loc>' not in text:
            text = text.replace('</urlset>', f'  <url><loc>{escape(url)}</loc></url>\n</urlset>')
    sitemap.write_text(text, encoding='utf-8')
    return list(PAGES)
