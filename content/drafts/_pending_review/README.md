# Pending human review — do not move back to content/drafts/ without checking first

These 4 posts were written 2026-10-31 for human review per an explicit "don't
publish directly, draft only" instruction. They are kept in this subfolder,
not `content/drafts/`, specifically so the automated release pipeline
(`automation/publish_queue.py`, run by the `DesktopTutorial-PublishQueue`
scheduled task) cannot pick them up — it only scans the top level of
`content/drafts/` and ignores subdirectories.

Once reviewed and approved for publishing, move the approved file(s) back to
`content/drafts/` (plain `mv`/`git mv`, no code changes needed) and the normal
pipeline will release them on its usual schedule.
