# zotero-mcp — TASKS

Durable backlog for this repo — **pending work only**. Severity: `[high]` / `[med]` / `[low]`.
Completed items live in [CHANGELOG.md](CHANGELOG.md), not here.

## Active

(none)

## Backlog

(none)

## Won't Do

Kept so resolved decisions aren't re-litigated as low-priority items.

- `[med]` ZOT-29 — consolidate `check_retractions`+`check_published_versions`
  into `check_items`, and fold `add_to_collection` into `batch_organize`.
  Declined 2026-06-02: removing/renaming tools breaks existing skills and saved
  workflows that call `mcp__zotero__<name>`, contradicting the project's
  no-rename policy (ZOT-01/02). Disambiguation was improved in the tool
  descriptions instead. Revisit only with a major-version bump.
