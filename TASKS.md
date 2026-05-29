# zotero-mcp — TASKS

Durable backlog for this repo. Severity: `[high]` / `[med]` / `[low]`.

## Active

(none)

## Backlog

Deferred from the 2026-05-28 MCP best-practices audit (full evidence in
`~/claude-memory/system/audit-reports/2026-05-28-mcp-server-audit.md`). These
are larger refactors/feature additions, not defects; the verified defects were
fixed on branch `audit-fixes-2026-05-28`.

- `[med]` **ZOT-05 — pagination metadata.** `search_items` and
  `get_collection_items` have no offset/total/has_more, so an agent cannot page
  past the first `limit` results of a large library/collection. Adding it
  cleanly means threading an `offset`/`start` param and a result envelope
  (`{total, count, offset, items, has_more, next_offset}`) through
  `_read_local_or_web` and BOTH the web client (read `Total-Results` header) and
  the local client. Deferred from the audit-fix batch to avoid shipping a
  half-correct `total` across the two read paths — do as a focused change with
  tests for each path.
- `[low]` **ZOT-07 — `response_format` option.** Add a `ResponseFormat` enum
  (markdown default / json) on high-traffic read tools (`search_items`,
  `get_item`, `get_collections`, `query_knowledge_graph`) for token efficiency.
- `[low]` **ZOT-08 — Pydantic input models.** Replace manual validation +
  free-string enums with Pydantic `BaseModel` inputs: `Field()` constraints and
  `Enum`s for `direction` / `query_type` / tag `action` / `limit`, so the
  inputSchema advertises valid choices up front. Large; do incrementally.

## Done

- `[med]` ZOT-03 — added annotations (readOnly/destructive/idempotent) to all
  write/destructive tools via shared presets. completed 2026-05-28.
- `[low]` ZOT-04 — set openWorldHint on external-service tools (34/36; the two
  long-decorator tools query_authors/query_knowledge_graph keep their existing
  readOnly annotation). completed 2026-05-28.
- `[med]` ZOT-06 — capped inline extracted PDF text at 50k chars with a
  `truncated` flag and `total_chars`. completed 2026-05-28.
- `[med]` ZOT-09 — `OpenAlexClient.get_work` now distinguishes 401/403 auth
  failures from genuine 404s and raises an actionable error instead of
  swallowing them. completed 2026-05-28.
- `[low]` ZOT-10 — `__version__` single-sourced from package metadata via
  `importlib.metadata`. completed 2026-05-28.
- `[low]` ZOT-11 — `ZOTERO_MCP_EMAIL` surfaced in manifest user_config;
  `server_status` warns when the placeholder polite email is in effect. completed 2026-05-28.
- `[low]` ZOT-12 — `store_entities` validates all entity types up front before
  any writes, so a bad item can't leave a partially-committed batch. completed 2026-05-28.
- `[low]` ZOT-01 / ZOT-02 — naming/server-name tradeoff recorded in server.py.
  completed 2026-05-28.
