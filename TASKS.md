# zotero-mcp — TASKS

Durable backlog for this repo. Severity: `[high]` / `[med]` / `[low]`.

## Active

(none)

## Backlog

(none — all 2026-05-28 audit findings resolved)

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
- `[med]` ZOT-05 — pagination: `offset` param + `{items, count, offset, limit,
  has_more, next_offset}` envelope on `search_items` and `get_collection_items`;
  `start` threaded through both the web and local clients. `total` is omitted
  (Zotero needs a separate count request); `has_more` is inferred from a full
  page. NOTE: this changes those tools' output shape from a bare list to the
  envelope. completed 2026-05-28.
- `[low]` ZOT-07 — `response_format="markdown"` on search_items, get_item,
  get_collections, get_collection_items, query_knowledge_graph via shared
  `_render`/`_to_markdown`. completed 2026-05-28.
- `[med→low]` ZOT-08 — typed public params with `Literal` enums (direction,
  manage_tags action, build_index type, query_knowledge_graph query_type,
  get_item format); runtime checks kept as defense-in-depth. completed 2026-05-28.
