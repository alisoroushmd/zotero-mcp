# zotero-mcp — TASKS

Durable backlog for this repo. Severity: `[high]` / `[med]` / `[low]`.

## Active

(none)

## Backlog

(none — 2026-06-02 audit findings ZOT-13…ZOT-31 resolved or recorded below)

## Won't Do

- `[med]` ZOT-29 — consolidate `check_retractions`+`check_published_versions`
  into `check_items`, and fold `add_to_collection` into `batch_organize`.
  Declined 2026-06-02: removing/renaming tools breaks existing skills and saved
  workflows that call `mcp__zotero__<name>`, contradicting the project's
  no-rename policy (ZOT-01/02). The disambiguation was instead improved in the
  tool descriptions (ZOT-31). Revisit only with a major-version bump.

## Done

### 2026-06-02 stability/distributability audit (ZOT-13…ZOT-31)

- `[high]` ZOT-13 — graph_renderer XSS: `_json_for_script` script-context-escapes
  embedded JSON (`<`/`>`/`&`/U+2028/9); info panel + legend use textContent.
  completed 2026-06-02.
- `[high]` ZOT-14 — `_handle_tool_errors` now catches `httpx.HTTPError`,
  data-shape errors, and a final catch-all; API errors include a truncated body.
  completed 2026-06-02.
- `[high]` ZOT-15 — `_read_local_or_web` falls back to the Web API on any local
  `httpx.HTTPError`, not just `RuntimeError`. completed 2026-06-02.
- `[high]` ZOT-16 — `fastmcp>=3.2,<4` (capped to the tested major). completed 2026-06-02.
- `[high]` ZOT-17 — DXT manifest documents the `uv` prerequisite + macOS
  GUI-PATH caveat via `long_description`. completed 2026-06-02.
- `[high]` ZOT-18 — `OpenAlexClient._get_with_retry` (429/5xx/transport backoff)
  on all GETs; `get_references` batched into one query. completed 2026-06-02.
- `[high]` ZOT-19 — `get_citation_graph` caps `references` to `limit`. completed 2026-06-02.
- `[med]` ZOT-20 — GraphStore: WAL + busy_timeout, corruption→actionable error,
  close-on-init-failure, `batch()` single-commit context. completed 2026-06-02.
- `[med]` ZOT-21 — `user_version`-gated schema create/migrate. completed 2026-06-02.
- `[med]` ZOT-22 — `_norm_doi` normalizes DOI casing so graph back-links
  populate `zotero_key` (citation-graph + KG + fulltext paths). completed 2026-06-02.
- `[med]` ZOT-23 — `close()` on WebClient/LocalClient/OpenAlex + `atexit`
  cleanup. completed 2026-06-02.
- `[med]` ZOT-24 — `_parse_retry_after` tolerates HTTP-date Retry-After values
  (web_client + openalex). completed 2026-06-02.
- `[med]` ZOT-25 — destructive-op version probe via `_library_version()`
  (`_retry_request`, raises if header absent — no `"0"` default). completed 2026-06-02.
- `[med]` ZOT-26 — create_* surface `dedup_check_failed` when the duplicate
  check errors transiently. completed 2026-06-02.
- `[med]` ZOT-27 — CI: clean-wheel smoke test (base + extras), version-sync
  check (pyproject==manifest, ==tag on release), `uv lock --check`; release
  gated on these. completed 2026-06-02.
- `[med]` ZOT-28 — `NCBI_API_KEY` + `_pubmed_get` retry wrapper on all eutils
  calls. completed 2026-06-02.
- `[low]` ZOT-30 — polish: correct `zotero-mcp-plus[...]` install hints; fixed
  stale `build_knowledge_graph` message → `build_index(type='graph')`; full
  annotations on query_knowledge_graph/query_authors; pypdf missing →
  actionable error (`pypdf_available()` guard + raise); abstract length cap;
  bounded KG analytics outputs via `_cap_list`. completed 2026-06-02.
- `[low]` ZOT-31 — ergonomics: `Literal` enums on query_authors/search_entities/
  export_knowledge_graph; `_parse_dict_param` for `update_item.fields`;
  bidirectional disambiguation pointers + `content_source` discriminator doc;
  `{items, count}` on get_notes/get_item_attachments; OS-native graph DB path;
  `OPENALEX_API_KEY` noted in server instructions. completed 2026-06-02.

### 2026-06-02 adversarial-review follow-ups (regressions caught + fixed)

- `[high]` ZOT-32 — `_norm_doi` extended to KG query inputs (path/neighborhood/
  citation_velocity) and the entity subsystem (store_entities write +
  search_entities paper_entities/shared_entities reads). The build-time DOI
  normalization had relocated the back-link breakage to query-time; now fixed at
  both ends. completed 2026-06-02.
- `[med]` ZOT-33 — `_parse_retry_after` (web_client + openalex) rejects
  negative/`nan`/`inf` values so `time.sleep` can't crash a retry loop on a
  hostile Retry-After header. completed 2026-06-02.
- `[med]` ZOT-34 — `get_pdf_content(extract_text=True)` degrades gracefully when
  pypdf is missing (returns the path result with a note) instead of raising
  `internal_error`. completed 2026-06-02.
- `[low]` ZOT-35 — `_dedup_check_failed` reset at each create entrypoint so a
  prior call's transient failure can't leak a false warning onto a later
  DOI-less URL create (pooled client is process-wide). completed 2026-06-02.
- `[low]` ZOT-36 — `_index_works` wraps its upserts in two `store.batch()`
  transactions (network resolution outside both), realizing the per-row-commit
  perf win the `batch()` API was built for. completed 2026-06-02.

### 2026-05-28 audit

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
