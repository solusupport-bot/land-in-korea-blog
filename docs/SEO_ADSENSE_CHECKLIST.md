# SEO & Google AdSense Checklist

Working rule set for `land-in-korea-blog`. Created 2026-09-18 after the site's
titles/descriptions were found badly out of spec right before an AdSense
application. Check every new/edited post against this before publishing.

## Per-post frontmatter rules

- **title**: 50–60 characters. Put the main keyword near the start. Avoid the
  templated `"<Topic> - What First-Timers Actually Need to Know"` suffix the
  automation falls back to — it produces duplicate-feeling, oversized titles.
- **description**: 120–160 characters. This is the meta description AND the
  og/twitter description (see `automation/build.py`'s `page()` function) —
  one field drives all of them, so it has to work as real ad copy, not just
  a summary.
- **slug**: already keyword-based (`korea-<topic>`) — keep this pattern.
- Exactly one `<h1>` per page (the build script already enforces this via the
  post title — don't add a manual `# Heading` in the body).

## Content depth

- Target 800+ words per post. Under ~350 words reads as thin content to both
  human reviewers and Google's helpful-content systems.
- As of 2026-09-18, thin posts (<350 words) needing expansion before/soon
  after an AdSense application: `korea-convenience-store-hacks`,
  `korea-dmz-and-jsa-tours`, `korea-emergency-numbers-24hr-pharmacies`,
  `korea-everland-vs-lotte-world-theme-parks`,
  `korea-insadong-traditional-culture-street`,
  `korea-luggage-storage-forwarding-services`,
  `korea-seasonal-packing-weather-tips`,
  `korea-tax-refund-tax-free-shopping`,
  `korea-trash-and-recycling-rules-for-residents`.
- Every factual claim must trace to an official source (Korea Tourism
  Organization, Korail, KTO, operator sites, etc.) — see the site's own
  "How we work" pledge on the About page. Don't invent specifics to pad
  word count.

## Google AdSense — what actually matters

- **No penalty for editing content right before applying.** AdSense reviews
  the site's state at review time, not its edit history. Fixing quality
  issues the day of application is normal and recommended.
- **Real risk factors**: thin content, missing policy pages, broken links,
  hard-to-find main content (should be reachable in ≤3 clicks), dumping a
  batch of new AI-flavored posts right before applying (reads as "scaled
  content abuse"). Editing *existing* posts for quality is not this.
- Required pages (already present: `site/about.html`, `contact.html`,
  `privacy.html`, `terms-of-service.html`) — privacy policy must disclose
  AdSense + cookie usage.
- `robots.txt` and `sitemap.xml` present — good, keep them updated as posts
  are added/removed.
- `ads.txt` isn't needed until an AdSense publisher ID exists — add it right
  after approval, not before.
- Recommended minimums before applying: 15–20 quality posts (this site has
  25), domain >1 month old, HTTPS, own domain (not a subdomain of a free host).

## How to check compliance quickly

```bash
cd content/posts
for f in *.md; do
  t=$(grep -m1 '^title:' "$f" | sed 's/^title: *//')
  d=$(grep -m1 '^description:' "$f" | sed 's/^description: *//')
  echo "$f | title=${#t} | desc=${#d}"
done
```

Flag anything with title >60 or description outside 120–160, then rebuild
with `python automation/build.py` and spot-check the generated `<title>`/
`<meta name="description">` in `site/posts/<slug>.html`.
