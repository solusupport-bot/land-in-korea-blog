#!/usr/bin/env python3
"""
Pre-build quality gate for Land in Korea.

Runs against content/posts/*.md BEFORE build.py. Exits non-zero on any ERROR,
which fails the GitHub Actions job and stops the broken content from being
deployed. WARN findings are printed but do not block.

Why this exists
---------------
AdSense rejected landinkorea.com on 2026-10 for "low value content", citing
Google's thin-content spam policy. The audit traced the signals to four
concrete, mechanically detectable things:

  1. Stale hard numbers presented as current (transit fares two revisions old
     in a post titled "Complete 2026 Guide").
  2. Unedited generation artifacts (a stray duplicated word inside a list).
  3. First-person experience claims that contradict the About page, which
     states the site is compiled from sources rather than personally tested.
  4. Bulk same-day publishing (12 posts dated 2026-09-08), which reads as
     scaled content abuse.

Each check below maps to one of those. Add to STALE_FIGURES whenever an
official fare or fee changes — that table is the single place this repo
records "what the current number is".

Usage:
    python3 validate.py            # check every post
    python3 validate.py --strict   # also fail on WARN
"""

import os
import re
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS = os.path.join(BASE, "content", "posts")

# --------------------------------------------------------------------------
# 1. Known-stale figures.
#
# key   = the obsolete string that must not appear as a current claim
# value = (what it used to be, what it is now, when it changed)
#
# Keep this table updated. It is the repo's memory of every number that has
# already gone wrong once.
# --------------------------------------------------------------------------
STALE_FIGURES = {
    "1,250 won": (
        "Seoul subway base fare",
        "1,550 won on a transit card / 1,650 won cash",
        "raised to 1,400 on 2023-10-07, then to 1,550 on 2025-06-28",
    ),
    "1,400 won": (
        "Seoul subway base fare (superseded)",
        "1,550 won on a transit card",
        "raised to 1,550 on 2025-06-28 — note 1,400 is still CORRECT for a "
        "yellow circular bus, so this fires as a WARN, not an ERROR",
    ),
    "3,800 won": (
        "Seoul mid-size taxi base fare",
        "4,800 won covering the first 1.6 km",
        "raised to 4,800 in Feb 2023",
    ),
    "1,200 won": (
        "bus fare (context-dependent)",
        "1,200 won is correct ONLY for a village (마을) bus or an "
        "early-morning discounted trunk bus — check the context",
        "n/a",
    ),
}

# Figures that are legitimately 1,400 / 1,200 won. If one of these words is on
# the same line, the stale-figure hit is suppressed.
FARE_CONTEXT_EXEMPT = re.compile(
    r"yellow|circular|village|마을|early-morning|6:30|discount|EUR|USD|exchange",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# 2. First-person experience claims.
#
# The About page promises the site does NOT claim first-hand testing. Any
# sentence implying the author personally did or witnessed something breaks
# that promise and reads as fabricated experience.
# --------------------------------------------------------------------------
FIRST_PERSON = re.compile(
    r"\bI'?ve\s+(watched|seen|been|found|met|stood|waited|used|tried|tested|"
    r"had|lost|spent|walked|eaten|stayed|ridden)\b"
    r"|\bI\s+(watched|saw|went|stood|waited|tried|tested|visited|walked|asked|"
    r"ordered|paid|lived|stayed|rode|ate)\b"
    r"|\bin\s+my\s+experience\b"
    r"|\bmy\s+(first|last|own)\s+(trip|visit|time|stay)\b"
    r"|\bwhen\s+I\s+was\s+(there|in|at)\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# 3. Unedited generation artifacts.
# --------------------------------------------------------------------------
# a) the same word twice in a row ("the the")
DUP_ADJACENT = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE)
# b) a word repeated inside a comma list ("language barriers, language, confusion")
DUP_IN_LIST = re.compile(r"\b(\w{4,})\b[^,.]{0,40},\s*\1\s*,", re.IGNORECASE)
# words whose adjacent repetition is grammatical English
DUP_ALLOW = {
    "had", "that", "is", "very", "long", "no", "what", "so", "out", "more",
    "did", "can", "will", "has", "was", "s",
}

# c) template scaffolding and placeholders that should never ship
PLACEHOLDERS = re.compile(
    r"\bPro tip \d\b|\bTODO\b|\bTBD\b|\bLorem ipsum\b|\[INSERT|\[PLACEHOLDER"
    r"|\bXX+\b|\bexample\.com\b",
)

# --------------------------------------------------------------------------
# 4. Publishing cadence.
#
# Google's scaled-content-abuse policy is about volume published at once.
# Twelve posts sharing one date is the pattern that got this site flagged.
# Raised 2026-10-29 from 2 to match publish_queue.py's MAX_PER_DAY.
# --------------------------------------------------------------------------
MAX_POSTS_PER_DATE = 4

REQUIRED_FIELDS = ("title", "date", "category", "description", "slug")
VALID_CATEGORIES = {
    "Comparisons",
    "Money-saving",
    "Etiquette & mistakes",
    "Airport & transit",
    "Practical info",
}

errors = []
warnings = []


def err(post, line, msg):
    errors.append(f"ERROR  {post}:{line}  {msg}")


def warn(post, line, msg):
    warnings.append(f"WARN   {post}:{line}  {msg}")


def split_front_matter(text):
    """Returns (meta dict, body, number of lines the front matter occupies)."""
    if not text.startswith("---"):
        return {}, text, 0
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text, 0
    meta = {}
    fm_lines = parts[1].splitlines()
    for raw in fm_lines:
        if ":" in raw:
            k, _, v = raw.partition(":")
            meta[k.strip()] = v.strip()
    # +2 for the two '---' delimiter lines
    return meta, parts[2], len(fm_lines) + 2


def check_post(path):
    name = os.path.basename(path)
    with open(path, encoding="utf-8") as f:
        text = f.read()

    meta, body, offset = split_front_matter(text)

    # -- front matter integrity ---------------------------------------------
    for field in REQUIRED_FIELDS:
        if not meta.get(field):
            err(name, 1, f"front matter is missing required field '{field}'")

    cat = meta.get("category", "")
    if cat and cat not in VALID_CATEGORIES:
        err(name, 1, f"category '{cat}' is not one of {sorted(VALID_CATEGORIES)}")

    desc = meta.get("description", "")
    if desc and len(desc) > 160:
        warn(name, 1, f"meta description is {len(desc)} chars (>160 gets truncated in search results)")

    # -- body checks --------------------------------------------------------
    for i, line in enumerate(body.splitlines(), start=offset + 1):
        stripped = line.strip()
        if not stripped:
            continue

        # stale figures
        for bad, (label, current, changed) in STALE_FIGURES.items():
            if bad in line:
                if FARE_CONTEXT_EXEMPT.search(line):
                    continue
                if bad in ("1,400 won", "1,200 won"):
                    warn(name, i, f"'{bad}' — verify context. {label}: now {current} ({changed})")
                else:
                    err(name, i, f"stale figure '{bad}' — {label} is now {current} ({changed})")

        # first-person experience claims
        m = FIRST_PERSON.search(line)
        if m and not stripped.startswith("###"):
            err(name, i, f"first-person experience claim '{m.group(0).strip()}' contradicts the About page")

        # generation artifacts
        for m in DUP_ADJACENT.finditer(line):
            if m.group(1).lower() not in DUP_ALLOW:
                err(name, i, f"duplicated word '{m.group(0)}' — unedited text")
        for m in DUP_IN_LIST.finditer(line):
            err(name, i, f"word '{m.group(1)}' repeats inside a list — unedited text")

        # placeholders / template scaffolding
        m = PLACEHOLDERS.search(line)
        if m:
            err(name, i, f"template scaffolding or placeholder left in body: '{m.group(0)}'")

    # -- sourcing -----------------------------------------------------------
    if "## Official sources" not in body and "## Sources" not in body:
        err(name, 1, "no sources section — every guide must cite what it was checked against")

    words = len(body.split())
    if words < 600:
        err(name, 1, f"only {words} words — too thin to stand on its own")
    elif words < 800:
        warn(name, 1, f"{words} words — thin; consider whether it earns its own page")

    return meta


def main():
    strict = "--strict" in sys.argv

    if not os.path.isdir(POSTS):
        print(f"No posts directory at {POSTS}")
        return 1

    by_date = defaultdict(list)
    files = sorted(f for f in os.listdir(POSTS) if f.endswith(".md"))
    for fn in files:
        meta = check_post(os.path.join(POSTS, fn))
        if meta.get("date"):
            by_date[meta["date"]].append(fn)

    # cadence check
    for date, posts in sorted(by_date.items()):
        if len(posts) > MAX_POSTS_PER_DATE:
            warn(
                "cadence", 0,
                f"{date} has {len(posts)} posts (limit {MAX_POSTS_PER_DATE}) — "
                f"bulk same-day publishing reads as scaled content abuse: {', '.join(posts)}",
            )

    print(f"Checked {len(files)} posts.\n")
    for line in warnings:
        print(line)
    if warnings:
        print()
    for line in errors:
        print(line)

    print()
    print(f"{len(errors)} error(s), {len(warnings)} warning(s).")

    if errors or (strict and warnings):
        print("\nBuild blocked. Fix the errors above, or correct STALE_FIGURES "
              "in validate.py if an official figure has genuinely changed.")
        return 1
    print("Passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
