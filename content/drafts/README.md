# Draft queue

Finished posts go here, not straight into `content/posts/`.

`automation/publish_queue.py` runs on a schedule (see
`.github/workflows/scheduled-publish.yml`) and moves **at most one** draft per
run into `content/posts/`, stamping it with that day's date. Four scheduled
runs a day (raised 2026-10-29 from two) means a maximum of four posts a day,
and the script refuses to go past that even if it is triggered manually in
between.

This separation is deliberate. The site was rejected by AdSense in October 2026
for low-value content, and one of the signals was twelve posts all dated
2026-09-08. Generating in batches is fine; publishing in batches is not.

## Drafts awaiting human review before publishing

The release script only scans this folder's top level, so a subfolder is invisible
to it. If a draft needs human sign-off before it's safe to auto-publish (e.g. it
was explicitly requested as "review only, don't publish"), put it in
`content/drafts/_pending_review/` instead of here. Move it back up once approved.

## Writing a draft

Same format as a live post, minus the `date:` field — the queue stamps that on
release. Required front matter:

```yaml
---
title: ...
category: Comparisons | Money-saving | Etiquette & mistakes | Airport & transit | Practical info
description: ...        # under 160 characters
slug: ...               # must match the filename without .md
---
```

Optional:

```yaml
publish_after: 2026-12-20   # held back until this date (use for seasonal posts)
```

Every post needs an `## Official sources` section and at least 600 words, or
the quality gate rejects it.

## Before you queue something

Run the gate yourself rather than finding out in CI:

```bash
cd automation && python3 validate.py
```
