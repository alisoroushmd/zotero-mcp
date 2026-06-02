# zotero-mcp — TASKS

Durable backlog for this repo. Severity: `[high]` / `[med]` / `[low]`.

## Active

(none)

## Backlog

Stability/distributability audit 2026-06-02 (ZOT-13…ZOT-31). Verified against code.

### High

- `[high]` ZOT-13 — **graph_renderer XSS.** `render_*` inject `json.dumps(data)`
  into a `<script>` tag (`graph_renderer.py:394-395`); `json.dumps` does not
  escape `<`/`>`/`/`, so a paper title or author name containing `</script>...`
  breaks out and executes arbitrary JS in the exported HTML the user opens.
  Second sink: info panel uses `innerHTML` (`graph_renderer.py:122`). Fix:
  escape `<`→`<`, `>`→`>`, `&`→`&` (and U+2028/2029) on the
  embedded JSON; switch the info-body to `textContent`/element creation.
- `[high]` ZOT-14 — **`_handle_tool_errors` leaks opaque errors.** Catches only
  `ValueError`/`HTTPStatusError`/`TimeoutException`/`RuntimeError`
  (`server.py:362-394`). Missing: `httpx.HTTPError` (DNS/connect-reset/transport
  on every network blip), `(KeyError, IndexError, TypeError, AttributeError)`
  (unexpected API response shapes), and a final `except Exception` catch-all. Add
  these so no tool raises raw to the MCP layer; return structured
  `network_error`/`internal_error`.
- `[high]` ZOT-15 — **Local-API non-connection errors bypass web fallback.**
  `_read_local_or_web` only catches `RuntimeError` (`server.py:418`), and
  `LocalClient._get` only converts `httpx.ConnectError`→`RuntimeError`
  (`local_client.py:35-47`). A local-API 500/timeout/read-error escapes, so a
  momentarily-unhealthy Zotero desktop (mid-sync, DB locked) surfaces an error
  instead of falling back to the cloud. Fix: catch `(RuntimeError, httpx.HTTPError)`
  on the local attempt, or wrap all local failures into `RuntimeError`.
- `[high]` ZOT-16 — **Cap fastmcp major version.** `fastmcp>=2.3.0` is unbounded
  (`pyproject.toml:13`) but `uv.lock` resolves 3.x — declared floor is 2.x,
  dev/tested is 3.x, a fresh `uvx`/`pip` install gets whatever is newest. Pick one
  tested major line and cap, e.g. `fastmcp>=3.2,<4`. Single biggest
  works-on-my-machine risk.
- `[high]` ZOT-17 — **DXT manifest assumes system `uv` on PATH.** `manifest.json`
  uses `command: "uv"`; on a fresh machine with no `uv`, or a GUI-launched
  Claude Desktop that doesn't inherit shell PATH (`~/.local/bin`), the extension
  fails with bare "command not found". Bundle deps (intended DXT model) or
  document/guard the `uv` prerequisite with a fallback to `python -m zotero_mcp`.
- `[high]` ZOT-18 — **OpenAlex has no retry/429 handling.** Every OpenAlex call
  relies only on a 10s timeout + broad `except` returning `[]`/`None`
  (`openalex_client.py`). On a large `bulk_get_works` (KG build over the whole
  library) a per-second rate limit silently yields an incomplete graph with no
  error. Add a shared `_get_with_retry()` mirroring the Semantic Scholar 429
  pattern; also batch `get_references` (currently N individual GETs via a
  threadpool) into one `openalex:` filter query.
- `[high]` ZOT-19 — **`get_citation_graph` returns unbounded `references`.**
  `cited_by` is capped by `limit` but `references` is returned in full
  (`server.py:931`), and each ref triggers a `_check_duplicate_doi` Zotero call
  (`max_workers=5`). A review with 200+ refs → large payload + 200 API calls.
  Slice `references[:limit_int]` before flagging.

### Med

- `[med]` ZOT-20 — **SQLite hardening.** `graph_store.py`: no WAL mode / no
  `busy_timeout` (concurrent KG + fulltext builds → `database is locked`); no
  `sqlite3.DatabaseError` handling on a corrupted/truncated file (cloud-drive
  conflict copy) → raw stack trace; connection leaks if `__init__` raises after
  `connect()`; per-row `commit()` in `upsert_*` is slow on large libraries.
  Fix: `PRAGMA journal_mode=WAL` + `busy_timeout=10000` on connect; wrap connect
  in try/close-on-failure; batch `_index_works` upserts in one transaction;
  surface a clear "DB corrupted; delete to rebuild" message.
- `[med]` ZOT-21 — **`user_version`-gated migrations.** `_create_tables()` +
  `_migrate()` run on every `GraphStore()` (every tool call) — DDL/PRAGMA/commit
  churn and no real migration versioning (`graph_store.py:25-117`). Gate both on
  `PRAGMA user_version` and bump it.
- `[med]` ZOT-22 — **DOI casing breaks graph back-links.** `_format_summary`
  returns DOI verbatim (`local_client.py:217`), so `key_by_doi`
  (`server.py:1785`) is mixed-case while papers are stored under OpenAlex's
  lowercase DOI → uppercase DOIs (e.g. `10.1016/J.GIE…`) get an empty
  `zotero_key`, breaking "in my library" flags. Normalize DOIs to lowercase at a
  single chokepoint (`get_all_items_with_dois` + `key_by_doi`).
- `[med]` ZOT-23 — **Add httpx client cleanup.** `WebClient.__init__` opens 3
  persistent `httpx.Client`s with no `close()`/`__del__`/atexit
  (`web_client.py:229`); same for `LocalClient` and the OpenAlex singleton. Add
  `close()` methods + an `atexit` handler in `server.py` alongside the temp-file
  cleanup. (ResourceWarning / clean-shutdown hygiene for a distributable.)
- `[med]` ZOT-24 — **`Retry-After` crashes on HTTP-date.** `float()`/`int()` of
  the header (`web_client.py:183`, `:1432`) raises `ValueError` if a CDN sends an
  HTTP-date instead of delta-seconds. Wrap the parse with a backoff fallback.
- `[med]` ZOT-25 — **Destructive-op version probe not retried.**
  `trash_items`/`empty_trash`/`remove_tag` fetch `Last-Modified-Version` via a
  plain GET (`web_client.py:1557,1588,1684`); a 429 there isn't retried and the
  version defaults to `"0"`, producing a guaranteed-412 (or stale) destructive
  call. Route the probe through `_retry_request`; raise if the header is absent
  rather than defaulting to `"0"`.
- `[med]` ZOT-26 — **Silent duplicate creation.** `_check_duplicate_doi`/
  `_check_duplicate_title` swallow all exceptions → `None` (no dup)
  (`web_client.py:496,535`); a transient search failure makes `create_item`
  create a duplicate silently. Surface a `dedup_check_failed: true` flag on the
  success response (or fail the create).
- `[med]` ZOT-27 — **CI/release never test the artifact users get.** CI uses
  `pip install -e` (editable), `uv.lock` is committed but referenced nowhere, and
  no job installs the built wheel in a clean venv. Add a clean-wheel install +
  smoke-test job (catches ZOT-16 and missing runtime deps); either enforce the
  lock (`uv sync --locked`) or drop it. Consider a matrix leg pinning fastmcp to
  its floor.
- `[med]` ZOT-28 — **PubMed efetch has no NCBI key / no rate-limit retry.**
  eutils enforces 3 req/s without a key; bulk resolution/build over many items
  hits 429s that aren't retried (`web_client.py:817`). Add an optional
  `NCBI_API_KEY` env var and a 429-aware retry on eutils GETs.
- `[med]` ZOT-29 — **Consolidate `check_retractions` + `check_published_versions`.**
  Near-identical signatures/structure, always run together (the
  `literature_audit` prompt runs them back-to-back). Merge into
  `check_items(item_keys, checks=["retractions","published_versions"])`. Highest
  -value tool-count reduction (−1). Also fold single-item `add_to_collection`
  into `batch_organize` (−1).

### Low

- `[low]` ZOT-30 — **Polish items (batch):** (a) wrong package name in
  user-facing install hints — `knowledge_graph.py:34` and `text_extractor.py:29`
  say `pip install zotero-mcp[...]` but the dist is `zotero-mcp-plus`; (b)
  `_get_or_build_kg` error names the removed `build_knowledge_graph` tool
  (`server.py:1635`) → `build_index(type='graph')`; (c)
  `query_knowledge_graph`/`query_authors` have incomplete annotations (bare
  `readOnlyHint`) — use `_RL` (`server.py:1960,2151`); (d) `text_extractor`
  missing-pypdf returns `None` indistinguishable from "no text" → user sees
  "N failed" with no install hint; raise a clear ImportError; (e) `_handle_tool_errors`
  discards the Zotero 4xx response body — include truncated `exc.response.text`
  for actionable errors; (f) bound `query_knowledge_graph` `clusters`/`timeline`/
  `topic_evolution` outputs.
- `[low]` ZOT-31 — **Ergonomics (batch):** (a) convert `query_authors.query_type`,
  `search_entities.query_type`, `export_knowledge_graph.view` to `Literal` enums;
  (b) add `_parse_dict_param` for `update_item.fields` (dict analog of the
  list-as-JSON-string gotcha); (c) bidirectional disambiguation pointers in tool
  descriptions (`create_item`↔`create_item_manual`, `insert_citations`↔
  `write_cited_document`, `get_pdf_content`↔`get_item_attachments`) and document
  `get_pdf_content`'s `content_source` discriminator; (d) wrap
  `get_collections`/`get_notes`/`get_item_attachments` in `{items, count}`;
  (e) consider `platformdirs` for the graph DB path; (f) mention
  `OPENALEX_API_KEY` in the top-level server instructions.

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
