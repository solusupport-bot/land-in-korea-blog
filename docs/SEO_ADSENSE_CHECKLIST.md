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
- The thin-post list flagged on 2026-09-18 was expanded to 1,000+ words each
  and confirmed on 2026-09-29 — no thin posts remain as of that check.
- Every factual claim must trace to an official source (Korea Tourism
  Organization, Korail, KTO, operator sites, etc.) — see the site's own
  "How we work" pledge on the About page. Don't invent specifics to pad
  word count.
- **2026-09-29 finding**: this pledge was written on 2026-09-18 but not
  actually implemented — zero of the 46 live posts linked to any outbound
  official source, despite the About page explicitly promising "every
  specific in these guides traces back to a source we can point to." Fixed
  by adding a real "## Official sources" section (verified, topically
  relevant government/operator links — KTO, Korail, HiKorea, Seoul
  Metropolitan Government, etc.) to every post. **Keep this current**: any
  new post must get a real, verified source section before publishing, not
  a placeholder — an unfulfilled version of this pledge is worse than not
  making it, since a reviewer (or a reader) who checks can tell.
- Outbound citation links use `rel="noopener"` only (not `sponsored`) —
  `sponsored` is reserved for the three actual affiliate domains
  (`AFFILIATE_DOMAINS` in `build.py`). Don't mark a genuine editorial
  citation as sponsored; that misrepresents it as a paid link.
- The post template now renders a visible byline ("Written by ... checked
  against official sources") under the title — previously the author only
  existed in the invisible JSON-LD schema, which a human reviewer never
  sees. Don't remove this when touching `page()`/the article template.

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
  46), domain >1 month old (custom domain connected 2026-08-28, so >1 month
  as of late Sep 2026 — was borderline at the 2026-09-19/09-26 applications),
  HTTPS, own domain (not a subdomain of a free host).
- **2026-09-26 rejection**: AdSense gave only "Program policy compliance" as
  the reason, no specific violation named. Nothing here confirms that was
  the cause, but at review time the live site had: zero outbound source
  citations despite the About page's sourcing pledge, no visible author/
  byline, a broken logo on every page (Windows-path bug in `build.py`), and
  10/46 post titles wildly over the 60-char guideline from posts added
  after the 2026-09-18 pass. All fixed 2026-09-29 — see entries above.

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
