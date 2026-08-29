# zotero-mcp — TASKS

Durable backlog for this repo — **pending work only**. Severity: `[high]` / `[med]` / `[low]`.
Completed items live in [CHANGELOG.md](CHANGELOG.md), not here.

## Active

- `[high]` Run the attachment migration against the live library. The code and
  tests are in (`src/zotero_mcp/attachment_migration.py`), but the run itself
  needs a normal terminal: an agent sandbox with a TLS-inspecting proxy cannot
  complete it, because `zotero_mcp/__init__.py` injects `truststore` and the
  proxy's MITM certificate is not in the macOS keychain. Sequence: dry run →
  `--apply --modes imported_file --limit 5` → spot-check in Zotero → full
  `--apply` → review trash → `--apply --empty-trash` (pass the same custom
  `--dest`, if any, so the ownership journal is found). As of 2026-08-21 the library
  holds 83 `imported_file` (82 PDFs, all cloud-only) and 182 `imported_url`
  snapshots, 51 of which hold cloud storage. `imported_url` is deliberately
  excluded from the default run and requires a separate explicit opt-in after
  local single-file validation. Acceptance: `linkMode` census shows no
  `imported_file` attachment with a server-side `md5`, and every migrated PDF
  still opens from its parent item.

- `[low]` Reinstall the venv so the new `zotero-migrate-attachments` console
  script is on PATH (`pip install -e ".[dev,graph,fulltext]"`). Until then use
  `python -m zotero_mcp.attachment_migration`.

## Backlog

- `[low]` Consider an opt-out for the `truststore` injection in
  `zotero_mcp/__init__.py` (e.g. `ZOTERO_MCP_TRUSTSTORE=0`), falling back to
  Python's default certifi verification rather than disabling it. Corporate
  TLS-inspecting proxies (Zscaler/Netskope, and agent sandboxes) present a MITM
  certificate that is trusted by the Python bundle but absent from the macOS
  keychain, so OS-trust verification fails where the default path succeeds —
  the exact inverse of the stale-bundle problem truststore was added for.
  Deferred 2026-08-21: not needed on Ali's normal network.

## Won't Do

Kept so resolved decisions aren't re-litigated as low-priority items.

- `[med]` ZOT-29 — consolidate `check_retractions`+`check_published_versions`
  into `check_items`, and fold `add_to_collection` into `batch_organize`.
  Declined 2026-06-02: removing/renaming tools breaks existing skills and saved
  workflows that call `mcp__zotero__<name>`, contradicting the project's
  no-rename policy (ZOT-01/02). Disambiguation was improved in the tool
  descriptions instead. Revisit only with a major-version bump.
