#!/usr/bin/env python3
"""
Release queued drafts into the live post set, on a cadence.

Editorial publishing policy
---------------------------
Keep generation and release separate so each article can be checked before
publication. Four posts per Korean calendar day is this site's editorial
limit (raised 2026-10-29 from 2); it is not an AdSense approval requirement
or a way to guarantee approval.

So generation and publication are now separate. A writer (human or agent)
drops finished posts into content/drafts/ whenever they are ready. This script
runs on a schedule and moves at most MAX_PER_RUN of them into content/posts/,
stamping each with the release date. Nothing reaches the site until this script
releases it, and it can never release a burst.

Drafts are released oldest-filename-first, so you control the order by naming.
A draft carrying an explicit `publish_after: YYYY-MM-DD` in its front matter is
held back until that date — use it for anything seasonal.

Usage:
    python3 publish_queue.py              # release up to MAX_PER_RUN
    python3 publish_queue.py --count 1    # release exactly 1
    python3 publish_queue.py --dry-run    # show what would be released
"""

import argparse
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAFTS = os.path.join(BASE, "content", "drafts")
POSTS = os.path.join(BASE, "content", "posts")

# Hard ceiling per run. The scheduled workflow fires four times a day
# (raised 2026-10-29 from two), so this being 1 is what makes "4 posts a
# day" the maximum rather than a burst.
MAX_PER_RUN = 1
MAX_PER_DAY = 4
KST = timezone(timedelta(hours=9))


def read_front_matter(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, text


def set_date(text, today):
    """Stamps the front matter date, adding the field if it's absent."""
    if re.search(r"^date:.*$", text, flags=re.MULTILINE):
        return re.sub(r"^date:.*$", f"date: {today}", text, count=1, flags=re.MULTILINE)
    # insert right after the opening delimiter
    return text.replace("---\n", f"---\ndate: {today}\n", 1)


def strip_publish_after(text):
    return re.sub(r"^publish_after:.*\n", "", text, flags=re.MULTILINE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=MAX_PER_RUN)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    count = min(args.count, MAX_PER_RUN)
    today = datetime.now(KST).date().isoformat()

    if not os.path.isdir(DRAFTS):
        os.makedirs(DRAFTS, exist_ok=True)
        print(f"Created empty draft queue at content/drafts/. Nothing to release.")
        return 0

    candidates = sorted(f for f in os.listdir(DRAFTS)
                        if f.endswith(".md") and f.lower() != "readme.md")
    if not candidates:
        print("Draft queue is empty — nothing to publish. "
              "This is a normal no-op, not a failure.")
        return 0

    # don't exceed the daily ceiling even if the workflow double-fires
    already_today = 0
    if os.path.isdir(POSTS):
        for fn in os.listdir(POSTS):
            if not fn.endswith(".md"):
                continue
            meta, _ = read_front_matter(os.path.join(POSTS, fn))
            if meta.get("date") == today:
                already_today += 1

    remaining = max(0, MAX_PER_DAY - already_today)
    if remaining == 0:
        print(f"{already_today} posts already published today — daily ceiling reached. "
              f"Leaving the queue alone.")
        return 0
    count = min(count, remaining)

    released = 0
    for fn in candidates:
        if released >= count:
            break
        src = os.path.join(DRAFTS, fn)
        meta, text = read_front_matter(src)
        required = ('title', 'category', 'description', 'slug')
        if any(not meta.get(key) for key in required) or meta['slug'] != fn[:-3]:
            print(f"SKIP     {fn} — incomplete article metadata or filename/slug mismatch")
            continue

        hold = meta.get("publish_after")
        if hold and hold > today:
            print(f"holding  {fn}  (publish_after: {hold})")
            continue

        dst = os.path.join(POSTS, fn)
        if os.path.exists(dst):
            print(f"SKIP     {fn}  — a post with this filename already exists")
            continue

        if args.dry_run:
            print(f"would release  {fn}  -> content/posts/ dated {today}")
            released += 1
            continue

        text = strip_publish_after(set_date(text, today))
        with open(dst, "w", encoding="utf-8") as f:
            f.write(text)
        os.remove(src)
        print(f"released {fn}  -> content/posts/ dated {today}")
        released += 1

    left = len([f for f in os.listdir(DRAFTS) if f.endswith(".md")]) if os.path.isdir(DRAFTS) else 0
    print(f"\nReleased {released}. {left} draft(s) still queued.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
