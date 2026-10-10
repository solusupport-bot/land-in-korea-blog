# Draft queue

Finished posts go here, not straight into `content/posts/`.

`automation/publish_queue.py` runs on a schedule (see
`.github/workflows/scheduled-publish.yml`) and moves **at most one** draft per
run into `content/posts/`, stamping it with that day's date. Two scheduled runs
a day means a maximum of two posts a day, and the script refuses to go past two
even if it is triggered manually in between.

This separation is deliberate. The site was rejected by AdSense in October 2026
for low-value content, and one of the signals was twelve posts all dated
2026-09-08. Generating in batches is fine; publishing in batches is not.

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
