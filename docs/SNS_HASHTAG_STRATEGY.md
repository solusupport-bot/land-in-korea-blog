# SNS Hashtag Strategy — 2026-10-30

Status: first pass done (Instagram/Bluesky/Mastodon prompt rules updated). Not
yet measured against real engagement data — this is a starting framework, not
a finished optimization.

## Problem found

The SNS posting pipeline (`desktop-tutorial/lib/curation/curate.js` — a repo
GPT does not have access to, see note at the end) generates each platform's
caption via an LLM prompt. The hashtag instructions gave the model 1-2 example
tags per platform (e.g. "#KoreaTravel #SeoulTrip") without telling it to vary
them — in practice the model tends to anchor on the example and repeat the
same 2-5 tags on every post regardless of what the post is actually about,
which wastes the niche-discovery value hashtags are supposed to provide.

## Research (free web search, no paid tools used)

No tool gives real per-hashtag engagement rates for Korea-travel content as of
this check; hashtag-analytics sites measure post volume/competition, not
engagement. Takeaways used below:

- `#koreatravel`, `#korea`, `#seoul`, `#visitkorea`, `#koreatrip`,
  `#seoultravel` are the established broad/volume tags for this niche.
- Broad tags like `#seoulkorea` are rated very high-competition by hashtag
  tools — low odds of surfacing a given post on their own.
- Common advice (consistent across sources, not from one authoritative
  engagement study): pair 1-2 broad tags with niche/location-specific tags
  (e.g. a specific palace, neighborhood, or activity name) — the broad tag
  gives ceiling, the niche tag is what actually gets a specific post found by
  someone searching that place.

Sources: displaypurposes.com/hashtags, iqhashtags.com (seoultravel,
seoulkorea), general Korea-travel-influencer roundups. Treat as directional,
not measured.

## What changed (2026-10-30)

In `desktop-tutorial/lib/curation/curate.js`, the per-platform hashtag
instructions for **Instagram**, **Bluesky**, and **Mastodon** now specify an
explicit tier structure instead of a bare example pair:

- **Instagram** (5 tags): 2 broad (`#KoreaTravel #VisitKorea #TravelKorea
  #Seoul`, pick 2) + 1 mid (`#SeoulTrip #KoreaTrip #KoreaTourism`) + 2 built
  from the specific post's own place/topic name.
- **Bluesky** (1-2 tags): 1 broad + 1 built from the post's own place/topic.
- **Mastodon** (3-5 tags): 2 broad + 1-2 built from the post's own
  place/topic.

Threads carries no hashtags by design (the publisher relies on profile-link
discovery instead — unchanged). Facebook and Reddit don't use hashtags in
this pipeline — unchanged. Tumblr uses the publisher's own `tags` field, not
the post body — unchanged, not touched here.

The instruction explicitly says "never the same N tags on every post" so the
model is pushed to actually pull a real place/topic name out of each post
instead of defaulting to the broad examples every time.

## Not done yet / open for follow-up

- No real engagement data yet on whether the niche tags actually move reach —
  this needs a few weeks of posts under the new rule, then a look at
  `data/` analytics (if tracked) or manual spot-checks.
- Didn't touch Pinterest/Reddit tagging (out of scope for this pass — those
  platforms don't use the same hashtag mechanism).
- A paid tool (vidIQ) could give real per-tag competition/volume data instead
  of the free-search approximations above — not used without approval first
  (standing rule in this project).

## Note for GPT

This doc lives in `land-in-korea-blog` (shared repo) because the actual code
change is in `desktop-tutorial`, a GitHub-only repo GPT doesn't currently have
access to. If you want to review or extend the hashtag logic itself, ask the
user for access to that repo, or treat this doc as the spec and suggest
changes here — Claude will apply them on the `desktop-tutorial` side.
