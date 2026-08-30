# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.10.1] - 2026-08-30

### Fixed

- **MCP Bundle packaging no longer follows a broken development-only
  symlink.** `GEMINI.md` now resolves to the tracked `AGENTS.md`, and MCPB
  excludes agent instructions, tests, CI files, and development artifacts from
  the `.mcpb` archive. The manifest and release tooling now use the current
  MCPB names instead of their deprecated DXT equivalents. CI packages the
  bundle before release, and PyPI
  publication now waits for a successful MCPB bundle build so a partial release
  cannot recur. GitHub Actions were also upgraded to Node.js 24-compatible
  releases.

## [0.10.0] - 2026-08-30

### Added

- **Attachment migration: convert existing `imported_*` attachments to
  `linked_file`, reclaiming Zotero cloud storage quota without losing a
  file.** `attach_pdf` stopped *creating* quota-consuming attachments, but
  every attachment created before that change still occupies the quota. This
  adds the one-way migration for them. (`attachment_migration.py`)

  New tools: `plan_attachment_migration` (read-only dry run),
  `migrate_attachments` (dry run by default; `apply=true` to act), and
  `inspect_trash` (list trash contents before an irreversible purge). Also
  available as a CLI: `zotero-migrate-attachments`, which is a dry run
  unless `--apply` is passed.

  `imported_file` is the safe default. `imported_url` snapshots require
  explicit opt-in and are accepted only when already downloaded locally and
  validated as exactly one regular file; cloud-only or multi-resource
  snapshots are skipped so companion files cannot be silently lost. Where
  PDF bytes live is detected per attachment rather than assumed: files
  present under `storage/<key>/` are copied, and cloud-only ones are fetched
  via `GET /items/<key>/file`. Its redirect is followed with a clean HTTP
  client so the Zotero API key is never forwarded, and presigned URL details
  are redacted from failure output.

  Ordering is chosen so that a crash at any point leaves the library
  recoverable: bytes are written and hash-verified on local disk (the digest
  is computed from the file re-read from disk, and downloads are additionally
  checked against Zotero's server-side MD5), then the replacement
  `linked_file` attachment is created, and only then is the original
  trashed. An attachment whose replacement failed is never trashed, and one
  failure does not abort the run.

  `DELETE /items/trash` is global. Before trashing, the migration atomically
  persists its library-bound ownership keys in the destination directory.
  The separate `--apply --empty-trash` invocation loads that journal and
  refuses to fire when the trash holds anything the migration did not put
  there, including across process restarts. It also refuses outright after a
  partially failed one-shot run.

  Attachments with no bytes anywhere (absent locally, no server-side file)
  are reported and skipped, never trashed. Attachments the server holds no
  file for free no quota and are skipped by default
  (`--include-local-only` converts them anyway). Re-runs are idempotent: a
  parent that already links the same path is skipped. Local storage lookup
  rejects traversal and symlink sources and requires resolved containment.
  A destination inside Zotero's own `storage/` tree is rejected, since Zotero
  prunes that tree and would delete the links out from under itself. An empty
  replacement key also fails closed before the original can be trashed.

  **Tradeoff, same as `attach_pdf`'s:** linked files do not sync across
  devices. After migrating, the bytes exist only on this machine — back up
  the linked-attachment directory accordingly.

### Changed

- **Full-text indexing now bounds memory and transaction growth.** PDF extraction keeps at most twice the worker count in flight and commits successful text in 100-record SQLite batches, preventing unbounded futures and per-record commits on large libraries. (`knowledge_graph.py`, `tests/test_knowledge_graph.py`)

- **Entity persistence is batched into one transaction.** `store_entities` writes entities and paper links together, while creation status and identifier lookup no longer require a preliminary existence query. (`graph_store.py`, `tests/test_entities.py`, `tests/test_graph_store.py`)

- **Knowledge-graph materialization now has an explicit resource ceiling.** NetworkX builds refuse persisted input above a configurable 100,000-record limit and rebuild author state from one paper-author snapshot without changing public MCP response shapes or positional `Config` compatibility. (`config.py`, `graph_store.py`, `knowledge_graph.py`, `server.py`, `README.md`, `tests/test_config.py`, `tests/test_graph_store.py`, `tests/test_knowledge_graph.py`, `tests/test_server.py`)

- **`attach_pdf` now stores PDFs locally by default instead of uploading them
  to Zotero cloud storage.** The previous implementation created every
  attachment as `linkMode: "imported_file"` and pushed the bytes to Zotero
  storage via the Web API, which consumes the account's file-storage quota.
  Once that quota filled, the upload-authorization request began returning
  HTTP 413 (`File would exceed quota`) and PDF attachment failed for every
  paper — 15 of 27 papers in one weekly literature scan were banked with no
  PDF. Local-only storage was the intended behavior all along.

  `attach_pdf` now writes the PDF into a local directory and creates a
  `linked_file` attachment pointing at it. Nothing is uploaded, so the quota
  is never touched and HTTP 413 cannot occur. Re-attaching byte-identical
  content reuses the existing file rather than duplicating it, and a name
  collision with *different* content gets a numeric suffix instead of
  silently overwriting. If item creation fails, only a file this call wrote
  is removed — a caller-supplied source file is never deleted.

  **Tradeoff:** linked files do not sync across devices. The attachment item
  syncs; the bytes stay on the machine that fetched them. Set
  `ZOTERO_ATTACHMENT_MODE=imported` to restore the previous upload behavior
  (note that linked-file attachments are also not valid in group libraries).

  New env vars: `ZOTERO_LINKED_ATTACHMENT_DIR` (default
  `<ZOTERO_DATA_DIR>/linked-attachments`) and `ZOTERO_ATTACHMENT_MODE`
  (`linked` | `imported`, default `linked`).
  (`web_client.py`, `config.py`)

- **`check_retractions` and `check_published_versions` cap `item_keys` at 50
  per call (ZOT-42).** Each key costs a Zotero read plus CrossRef and
  OpenAlex round-trips, so an unbounded batch could run for minutes and
  hammer external APIs. Oversized batches are truncated (first 50 processed,
  never rejected) and the response reports `truncated`/`submitted`/
  `processed` plus a note telling the caller to resubmit the rest.
  (`server.py`)

- **Docs corrected (audit ride-alongs).** CLAUDE.md/AGENTS.md told
  developers to `pip install zotero-mcp[graph]` — a *different project* on
  PyPI; the correct name is `zotero-mcp-plus`, and the docs now warn about
  the collision explicitly. Stale "36 tools" counts updated to 39. README
  gains a "Developing against a live install" note documenting that a
  `uvx --from '… @ file://…'` install caches the built wheel, so local
  source edits don't reach the running server until a version bump,
  `uvx --reinstall`, or `uv cache clean` — plus a full client restart.
  (`CLAUDE.md`, `AGENTS.md`, `README.md`)

### Fixed

- **`manage_tags(action="remove")` no longer 405s.** `WebClient.remove_tag`
  issued `DELETE /tags/<name>` (path form), which the Zotero Web API rejects
  with HTTP 405 Method Not Allowed — tags with spaces or emoji (e.g.
  `❓ Multiple DOI`) failed outright. Tag deletion now uses the documented
  `DELETE /tags?tag=<url-encoded name>` query-parameter form (httpx handles the
  encoding), keeping the `If-Unmodified-Since-Version` guard. Added a
  special-character regression test. (`web_client.py`)

Static-audit hardening pass, findings ZOT-37…ZOT-43 (ZOT-42 and the doc
corrections are listed under Changed above):

- **`manage_tags` is now annotated destructive (ZOT-37).** It carried `_WR`
  (`destructiveHint: False`), but `action="remove"` deletes a tag from every
  item in the library and `action="rename"` rewrites it library-wide — the
  same blast radius as `update_item`, which is `_DR`. Clients that gate
  confirmation on `destructiveHint` were silently skipping it. The tool
  description now says so and tells the LLM to confirm first. (`server.py`)

- **`find_related_papers` no longer leaks an HTTP client per call, and
  Semantic Scholar failures surface instead of vanishing (ZOT-38).** Each
  call built a fresh `SemanticScholarClient` (a pooled `httpx.Client` that
  was never closed), violating the repo's own pooling rule. The client is
  now a lazy module-level singleton (`_get_s2()`, mirroring
  `_get_openalex()`) closed via the `_cleanup_clients` atexit handler. The
  blanket `except Exception → []` in `get_recommendations` is gone: an API
  or transport failure now propagates to `_handle_tool_errors` as a
  structured error instead of masquerading as "no recommendations". The
  429 Retry-After header is parsed tolerantly (HTTP-date falls back to 5s
  rather than crashing `int()`). (`semantic_scholar_client.py`, `server.py`)

- **OpenAlex no longer receives a fake polite-pool identity (ZOT-39).** The
  client sent `mailto:zotero-mcp@example.com` (the placeholder default)
  unconditionally in its User-Agent, contradicting the documented
  `_polite_user_agent()` behavior. It now routes through that shared helper,
  which omits `mailto:` for placeholder domains. The UA product string is
  also `zotero-mcp-plus/<version>` everywhere instead of the frozen
  `zotero-mcp/1.0` — `zotero-mcp` is a different project on PyPI, so the
  old string misattributed traffic. (`openalex_client.py`, `web_client.py`)

- **Zotero Web API reads now retry on 429 and honor the `Backoff` header
  (ZOT-40).** All library read GETs, including attachment-migration inventory
  and trash inspection, pagination for DOI/tag operations, attachment
  downloads, and destructive-operation version probes, now go through
  `WebClient._read_get()`: 429s retry with Retry-After honored, and Zotero's
  `Backoff` header (sent on 2xx when the server is overloaded) is recorded so
  the next read sleeps out the window (capped at 30s). (`web_client.py`,
  `attachment_migration.py`)

- **`query_knowledge_graph(query_type="trending")` respects the limit clamp
  (ZOT-41).** The trending branch passed the raw `limit` to `get_trending()`
  while every sibling branch used the clamped value — a `limit=100000`
  request could blow the context window the clamp exists to protect.
  (`server.py`)

- **`insert_citations` can no longer destroy the document it is editing
  (ZOT-43).** With no `output_path` it overwrites its source .docx in place;
  a crash mid-`doc.save()` (disk full, interrupt) left a truncated,
  unopenable file with the original already gone. Saves now write to a
  sibling temp file and `os.replace()` over the target, so the overwrite is
  all-or-nothing and the original survives any failure.
  (`citation_writer.py`)

## [0.9.0] - 2026-06-02

Stability and distributability hardening pass (audit findings ZOT-13…ZOT-31).

### Security

- **graph_renderer XSS fixed (ZOT-13).** The exported knowledge-graph HTML
  embedded paper/author metadata into a `<script>` tag via `json.dumps`, which
  does not escape `<`/`>`/`&`. A title containing `</script>…` could break out
  and execute arbitrary JS when the file was opened. `_json_for_script` now
  unicode-escapes those characters plus U+2028/U+2029, and the info panel and
  legend build DOM nodes with `textContent` instead of `innerHTML`.
  (`graph_renderer.py`)

### Fixed

- **Opaque MCP errors eliminated (ZOT-14).** `_handle_tool_errors` now also
  catches `httpx.HTTPError` (DNS/connect/transport) as `network_error`,
  data-shape errors (`KeyError`/`IndexError`/`TypeError`/`AttributeError`) and
  any other exception as `internal_error`, so no tool raises raw to the protocol
  layer. `api_error` responses include a truncated Zotero response body for
  actionable 4xx (e.g. "item version mismatch"). (`server.py`)
- **Local-API failures fall back to the Web API (ZOT-15).** `_read_local_or_web`
  previously fell back only on `RuntimeError`; a transiently unhealthy Zotero
  desktop (mid-sync 500, locked DB, read timeout) surfaced an error instead of
  using the cloud path. It now falls back on any `httpx.HTTPError`. (`server.py`)
- **DOI casing no longer breaks graph back-links (ZOT-22).** Zotero stores DOIs
  verbatim (often uppercase) while OpenAlex lowercases them, so uppercase DOIs
  were stored with an empty `zotero_key`. A single `_norm_doi` chokepoint
  normalizes DOIs across the citation-graph, knowledge-graph, and full-text
  index paths. (`server.py`)
- **`get_citation_graph` caps `references` (ZOT-19).** Previously unbounded; a
  heavily-referenced review returned a large payload and fired one Zotero
  duplicate-check call per reference. Now capped to `limit`. (`server.py`)
- **`Retry-After` tolerates HTTP-date values (ZOT-24).** Parsing a date-valued
  header no longer crashes the retry loop. (`web_client.py`, `openalex_client.py`)
- **Destructive-op version probe hardened (ZOT-25).** `trash_items`,
  `empty_trash`, and `remove_tag` fetch the library version through
  `_retry_request` (429-aware) and raise if the header is absent instead of
  defaulting to `"0"` (which guaranteed a 412 or acted on a stale assumption).
  (`web_client.py`)
- **Correct package name in install hints (ZOT-30).** Optional-extra error
  messages now say `zotero-mcp-plus[graph]` / `[fulltext]` (the real
  distribution name). (`knowledge_graph.py`, `text_extractor.py`)
- **Stale tool reference fixed (ZOT-30).** The "build the graph first" error now
  names `build_index(type='graph')`, not the removed `build_knowledge_graph`.
  (`server.py`)

### Added

- **OpenAlex retry/backoff (ZOT-18).** `_get_with_retry` handles 429
  (`Retry-After`), 5xx, and transport errors with capped exponential backoff on
  every OpenAlex GET, so large graph builds no longer silently produce
  incomplete data under rate limits. `get_references` resolves references in one
  batched query instead of N threaded GETs. (`openalex_client.py`)
- **SQLite hardening (ZOT-20, ZOT-21).** `GraphStore` opens with
  `journal_mode=WAL` + `busy_timeout`, raises an actionable error on a corrupt
  database, closes the connection if initialization fails, gates
  schema-create/migrate on `PRAGMA user_version`, and offers a `batch()` context
  that commits a bulk load once. (`graph_store.py`)
- **NCBI eutils API key support (ZOT-28).** Set `NCBI_API_KEY` to raise the
  PubMed rate limit from 3 to 10 req/s; all eutils calls go through a 429-aware
  `_pubmed_get` wrapper. (`config.py`, `web_client.py`)
- **HTTP client cleanup (ZOT-23).** `WebClient`, `LocalClient`, and the OpenAlex
  client expose `close()`, registered via `atexit` for clean shutdown.
- **Duplicate-check failures are surfaced (ZOT-26).** When the pre-create
  duplicate search fails transiently, `create_item`/`create_item_manual`/
  `create_item_from_url` return `dedup_check_failed: true` with a warning rather
  than silently creating a possible duplicate. (`web_client.py`)
- **Bounded knowledge-graph analytics (ZOT-30).** `clusters`, `timeline`,
  `topic_evolution`, and author `clusters` results are capped via `_cap_list`
  (returns `{items, count, total, truncated}` when capped). (`server.py`)
- **CI/release artifact testing (ZOT-27).** New jobs build the wheel and import
  it in a clean venv (base + extras), assert `pyproject`/`manifest`/tag versions
  match, and run `uv lock --check`. Release is gated on these.

### Changed

- **`fastmcp` capped to the tested major: `>=3.2,<4` (ZOT-16).** An unbounded
  floor let a fresh install resolve an untested major across fastmcp's 2.x→3.x
  break. (`pyproject.toml`)
- **DXT manifest documents the `uv` prerequisite (ZOT-17)** and the macOS
  GUI-PATH caveat via `long_description`. (`manifest.json`)
- **`get_notes` and `get_item_attachments` now return `{items, count}`** instead
  of a bare list, for shape consistency with the paginated read tools (ZOT-31).
  **(behavior change for callers parsing those tools' output.)** (`server.py`)
- **Typed `Literal` enums** on `query_authors`, `search_entities`, and
  `export_knowledge_graph`; `update_item.fields` accepts a JSON-string via
  `_parse_dict_param`; bidirectional disambiguation pointers and a documented
  `content_source` discriminator added to tool descriptions (ZOT-31).
- **Graph DB default path is OS-native** (`~/Library/Application Support` on
  macOS, `%LOCALAPPDATA%` on Windows), with backward-compatible reuse of an
  existing `~/.local/share` database (ZOT-31). (`config.py`)

### Fixed (adversarial-review follow-ups, same release)

- **DOI normalization extended to query paths (ZOT-32).** The build-time
  `_norm_doi` change had relocated the uppercase-DOI back-link breakage to
  query-time; `query_knowledge_graph` (path/neighborhood/citation_velocity) and
  the entity subsystem (`store_entities` write, `search_entities`
  paper_entities/shared_entities reads) now normalize too. (`server.py`)
- **`Retry-After` rejects hostile values (ZOT-33).** Negative / `nan` / `inf`
  fall back instead of crashing `time.sleep`. (`web_client.py`, `openalex_client.py`)
- **`get_pdf_content(extract_text=True)` degrades gracefully (ZOT-34)** when the
  `[fulltext]` extra is missing, returning the PDF path with a note instead of
  an `internal_error`. (`server.py`)
- **No stale dedup-warning leak (ZOT-35).** `_dedup_check_failed` is reset at
  each create entrypoint so a prior transient failure can't flag a later
  DOI-less URL create. (`web_client.py`)
- **Knowledge-graph build batches its writes (ZOT-36).** `_index_works` wraps
  upserts in `store.batch()` (network resolution outside the transaction),
  realizing the single-commit perf win. (`server.py`)

> Note: 0.8.4 and 0.8.5 were tagged without dedicated changelog sections; their
> changes (deferred ZOT-05/07/08 and the MCP best-practices pass) are captured in
> the project's TASKS.md history and are superseded by this release.

## [0.8.3] - 2026-05-14

### Fixed

- **DXT manifest schema:** renamed top-level `manifest_version` to `dxt_version`
  to match the current `@anthropic-ai/dxt` CLI schema. The v0.8.2 release
  workflow's `build-dxt` job failed because the CLI rejected the old field name.
  (`manifest.json`)

## [0.8.2] - 2026-05-14

### Fixed

- **`get_attachment_path` now resolves `imported_url` PDFs from local storage.**
  For attachments with `linkMode: imported_url` (the most common type — PDFs
  downloaded via Zotero's browser connector), the Zotero API's `path` field is
  empty. The method now falls back to constructing the real filesystem path from
  `{ZOTERO_DATA_DIR}/storage/{attachment_key}/{filename}` and verifies the file
  exists before returning it. Also skips `storage:` URI prefixes returned by
  some Zotero API versions, which are not valid filesystem paths.
  (`local_client.py:get_attachment_path`)

- **`get_pdf_content` `not_found` response now includes `routes_tried`.**
  When no PDF source is found, the response includes which routes were attempted
  (e.g. `["local_storage_path", "web_api_download", "free_pdf_unpaywall"]`) so
  callers can diagnose why a paywalled paper returned `not_found` instead of
  silently failing. (`server.py:get_pdf_content`)

- **Unhandled exceptions in `check_retractions` and `check_published_versions` ThreadPoolExecutor futures** now return per-item `{"key": ..., "error": ...}` entries instead of crashing the entire tool call. (`server.py`)

- **`httpx.TimeoutException` added to `_handle_tool_errors`** — write-path tools (`create_item`, `update_item`, `trash_items`, `attach_pdf`, etc.) now return a structured `{"error": "timeout"}` response instead of an unhandled exception when the Zotero API times out. (`server.py`)

- **Tag name URL-encoding in `remove_tag`** — tags containing `/`, `?`, `#`, or spaces are now percent-encoded before being interpolated into the DELETE path, preventing silent mismatches. (`web_client.py`)

- **FTS5 query parse errors in `search_fulltext`** — malformed queries (unbalanced quotes, bare `AND`) now raise a `ValueError` with a clear message instead of crashing with an uncaught `sqlite3.OperationalError`. (`graph_store.py`)

- **`export_knowledge_graph` now validates the `path` parameter** via `_validate_path` before writing, consistent with every other file-writing tool. (`server.py`)

- **`polite_user_agent` helper** — CrossRef and OpenAlex User-Agent headers now use `_polite_user_agent()` which omits the `mailto:` clause when the email is a placeholder (`@example.com` etc.), preventing fake identities being sent to polite-pool APIs. (`web_client.py`)

### Added

- **`TOOL_MODES` now includes `check_ssl_health` and `audit_local_keys`** — both tools were missing from the capability map, making them invisible to `server_status`. Both require no credentials (`[]`). (`capabilities.py`)

- **`store_entities` validates `entity_type`** against the documented set (`condition`, `biomarker`, `drug`, `method`, `gene`, `organism`, `outcome`, `dataset`). Invalid types raise `ValueError` instead of silently fragmenting the vocabulary. (`server.py`)

- **`GraphStore` context manager** (`__enter__` / `__exit__`) — all tool functions now close their `GraphStore` connections deterministically via `with GraphStore() as store:` or `try/finally store.close()`, replacing reliance on CPython GC. (`graph_store.py`, `server.py`)

- **`OpenAlexClient` module-level singleton** (`_openalex`, `_get_openalex()`) — eliminates per-tool-call TCP connection churn; pooled connection is reused across `check_retractions`, `get_citation_graph`, `check_published_versions`, and `build_index`. (`server.py`)

- **13 new tests** covering `_parse_list_param` (all four input shapes), `_validate_path` path traversal rejection, `get_pdf_content` with `extract_text=True`, `batch_organize` normal case and 412 retry, and `write_cited_document` path-traversal guard. Dead code removed from `test_text_extractor.py`. (`tests/`)

- **README: Environment variables section** documenting all 9 env vars (`ZOTERO_API_KEY`, `ZOTERO_USER_ID`, `OPENALEX_API_KEY`, `ZOTERO_MCP_EMAIL`, `SEMANTIC_SCHOLAR_API_KEY`, `ZOTERO_DATA_DIR`, `ZOTERO_MCP_GRAPH_DB`, `XDG_DATA_HOME`, `PARENT_WATCHDOG_DISABLE`).

- **README: Diagnostic tools section** for `check_ssl_health` and `audit_local_keys`, with SSL troubleshooting and Unpaywall `ZOTERO_MCP_EMAIL` entries in the troubleshooting table.

### Changed

- CI matrix drops Python 3.14 (not yet stable; `allow-prereleases` was not set). (`ci.yml`)
- GitHub Release workflow: fixed wrong step-output expression `steps.changelog.notes.outputs.notes` → `steps.changelog.outputs.notes` that produced empty release bodies. (`release.yml`)
- `find_duplicates` `readOnlyHint: True` annotation removed — the tool requires Web API credentials and was inconsistently annotated as read-only.

## [0.8.1] - 2026-04-29

### Added

- `_orphan_watchdog` module — daemon thread that calls `os._exit(0)` when the launching parent (Claude.app disclaimer, `uv run`, `uvx`, etc.) exits without propagating stdin-close, eliminating orphan-process accumulation across session restarts. Installed automatically from `__main__.py`; disable with `PARENT_WATCHDOG_DISABLE=1`.

### Changed

- CI now installs the `graph` and `fulltext` optional extras alongside `dev`, so the knowledge-graph, graph-renderer, and full-text tests actually run. Every main-branch CI run since 0.7.0 failed because these tests were being imported without `networkx` / `pypdf` available.

### Fixed

- `check_ssl_health` reported `BROKEN` on macOS + Homebrew Python whenever `ssl.get_ca_certs()` returned 0 entries, even when both HTTPS probes succeeded. The default `capath` (e.g. `/private/etc/ssl/certs`) holds the CAs but `get_ca_certs()` only enumerates `cafile`-loaded ones, so `ca_count == 0` is a normal state. The verdict now trusts probe success over the static count, and the "Zero CAs" remediation hint is suppressed when probes empirically work. Offline (`probe=False`) calls still treat `ca_count == 0` as a fault.
- Full-text index build (`build_index(type="fulltext")`) was writing to SQLite from worker threads, which sqlite3 connections don't support — extraction now runs in parallel and writes are serialized on the main thread. Prevents silent data corruption and thread-safety warnings on large builds.

## [0.8.0] - 2026-04-16

### Added

- Centralized configuration module (`config.py`) — all environment variables read once, exposed as typed attributes with validation properties
- 4 MCP prompts for guided multi-tool workflows: `literature_audit`, `build_and_explore`, `add_and_verify`, `extract_entities`
- `check_ssl_health` tool — diagnoses Python SSL/TLS configuration (cert bundle paths, CA count, env-var overrides, live HTTPS probes) and returns a HEALTHY/DEGRADED/BROKEN verdict with concrete remediation steps. Use when any tool reports `CERTIFICATE_VERIFY_FAILED`.
- `audit_local_keys` tool — scans the local Zotero SQLite for collection/item keys containing forbidden characters (`0`, `1`, `O`) that the Zotero sync server rejects with "not a valid collection key", halting sync.
- `ZOTERO_DATA_DIR` env var — overrides the Zotero desktop data directory (defaults to `~/Zotero`); used by the local-key audit.
- `truststore` dependency — server now uses the OS trust store (macOS Keychain, Windows CertStore, Linux CA bundle) instead of Python's bundled CA file, working around broken/stale interpreter cert bundles (e.g. Homebrew Python 3.14 shipping a `cert.pem` whose root CA fails Basic Constraints verification). Falls back gracefully if `truststore` is not installed.
- `get_pdf_content` arXiv fallback — for arXiv DOIs (`10.48550/arxiv.*`), fetches the PDF directly from arXiv when no published version exists. CrossRef's `is-preprint-of` relation is checked first so users still get the canonical published PDF when one is available.

### Changed

- Tool count reduced from 37 to 34 via consolidation:
  - `get_tags` + `remove_tag` + `rename_tag` → `manage_tags(action="list|remove|rename")`
  - `build_knowledge_graph` + `build_fulltext_index` → `build_index(type="graph|fulltext|both")`
- All 34 tool descriptions rewritten to be LLM-actionable ("Use this when...") per MCP best practices
- Environment variable reads centralized: `capabilities.py`, `openalex_client.py`, `web_client.py`, `graph_store.py`, and `server.py` now use `config.py` instead of scattered `os.environ.get()` calls
- `store_entities` and `search_entities` now use `GraphStore` public methods (`entity_exists`, `get_entities_by_type`) instead of reaching into `store._conn` directly
- `get_pdf_content` PDF downloads (Unpaywall, PMC, arXiv, bioRxiv/medRxiv) now retry up to 3 times with exponential backoff on transient network errors and 5xx responses instead of failing on the first glitch.

### Fixed

- `export_knowledge_graph` missing `@_handle_tool_errors` — errors now return structured JSON instead of propagating to MCP transport
- `build_index` validated `type` parameter after execution; now fails fast before any work
- Deadlock on first web-client initialization when Zotero desktop was running — `_get_web()` now resolves the optional local client before acquiring the non-reentrant `_init_lock`.
- `find_duplicates` was silently including notes in its DOI scan — the Zotero Web API only honors single-value `itemType` negation, so notes are now filtered client-side after a `-attachment` request.
- `get_pdf_content` Unpaywall fallback silently returned `not_found` for every item when `ZOTERO_MCP_EMAIL` was unset or a placeholder (`@example.com`, etc.) — Unpaywall rejects these with HTTP 422. The call is now skipped with a clear warning telling the user to set a real email, and placeholder-domain emails are treated as unset.

### Removed

- `get_tags`, `remove_tag`, `rename_tag` tools (replaced by `manage_tags`)
- `build_knowledge_graph`, `build_fulltext_index` tools (replaced by `build_index`)

## [0.7.0] - 2026-04-15

### Added

- **Temporal analytics** — 4 new `query_knowledge_graph` query types:
  - `timeline` — papers per month, filterable by topic and year range
  - `topic_evolution` — per-subfield monthly publication counts over time
  - `citation_velocity` — month-by-month citation accumulation for a paper
  - `trending` — papers with accelerating recent citation rates (velocity ratio)
- **Full-text PDF search** — `search_fulltext` tool for searching indexed full text with
  highlighted snippets. Build the index with `build_index(type='fulltext')`. Uses pypdf
  for bulk text extraction and SQLite FTS5 with BM25 ranking. Hybrid approach: pypdf for
  keyword search index, LLM reads PDFs natively for deep understanding of tables/figures
- **Entity extraction** — 3 new tools (two-tool LLM-in-the-loop pattern):
  - `get_unextracted_abstracts` — returns papers with abstracts not yet entity-extracted
  - `store_entities` — persist typed entities (biomarker, drug, gene, etc.) extracted
    by the calling LLM from abstracts
  - `search_entities` — query entity index: by_name, by_type, by_doi, co_occurrence,
    shared_entities, entity_network, paper_entities
- `publication_date TEXT` column in papers table (YYYY-MM granularity) — populated from
  OpenAlex, enables month-level temporal analytics
- `abstract TEXT` column in papers table — reconstructed from OpenAlex inverted index
  via `OpenAlexClient.reconstruct_abstract()`. COALESCE prevents NULL overwrites
- `entities` + `paper_entities` tables in GraphStore for entity persistence
- `paper_fulltext` FTS5 virtual table + `fulltext_state` tracking table
- `text_extractor.py` module — PDF text extraction via pypdf + FTS5 indexing helpers
- `_migrate()` method in GraphStore for transparent schema upgrade from v0.6.0 databases
- `[fulltext]` optional extra: `pip install zotero-mcp[fulltext]` adds pypdf
- `get_pdf_content` gains `extract_text` parameter for inline text extraction

### Changed

- Tool count changed from 32 to 34 (5 new tools added, 3 consolidated away)
- `_index_works()` now captures `publication_date` and `abstract` from OpenAlex responses
- `query_knowledge_graph` description updated with temporal query types and new parameters
  (`topic`, `start_year`, `end_year`, `years`)
- KnowledgeGraph `build_from_store()` loads `publication_date` into paper node data

## [0.6.0] - 2026-04-15

### Added

- **Topic-labeled clusters** — `query_knowledge_graph(query_type="clusters")` now returns
  `label`, `secondary_labels`, and `topic_distribution` per cluster, derived from OpenAlex
  topic hierarchy (subfield level). Graceful degradation: clusters from pre-0.6.0 databases
  are labeled "Unlabeled"
- **Author co-citation network** — `query_authors` tool with query types: prolific (by paper
  count), influential (by summed PageRank), coauthors_of, network (ego network within N hops),
  clusters. Fuzzy name resolution (substring + SequenceMatcher > 0.85)
- **Graph visualization** — `export_knowledge_graph` tool generates interactive HTML with
  D3.js force-directed layout. Three views: `citations` (paper nodes colored by cluster),
  `authors` (co-authorship edges), `full` (both layers, papers capped at 200 by PageRank).
  Drag, zoom, click-to-inspect info panel
- `GraphStore` schema: 3 new tables (`paper_topics`, `authors`, `paper_authors`) with
  `CREATE TABLE IF NOT EXISTS` for transparent upgrade from v0.5.0 databases
- `OpenAlexClient.extract_topics()` and `extract_authorships()` static methods — parse
  topic hierarchy and structured author records from already-fetched work dicts (no new API calls)
- `graph_renderer.py` module — HTML template with embedded D3.js visualization
- `build_knowledge_graph` now indexes topics and authors from OpenAlex responses, reporting
  `topics_indexed` and `authors_indexed` in stats. Auto-detects incremental sync vs full
  build (set `full_rebuild=true` to force)

### Changed

- Tool count stays at 32 (3 new tools added, 3 consolidated away)
- Consolidated `create_item_from_identifier` + `create_item_from_url` → `create_item`
  (auto-routes URLs vs bare identifiers)
- Consolidated `get_author_network` into `query_authors(query_type="network")`
- Consolidated `sync_knowledge_graph` into `build_knowledge_graph` (auto-detects sync)
- `KnowledgeGraph` now maintains a separate `nx.Graph` for co-authorship (keeps citation
  DiGraph clean for PageRank and community detection)
- `build_from_store()` loads topic data for cluster labeling and builds author/co-authorship
  structures from `GraphStore`

### Removed

- `create_item_from_identifier` — replaced by `create_item`
- `create_item_from_url` — replaced by `create_item`
- `get_author_network` — replaced by `query_authors(query_type="network")`
- `sync_knowledge_graph` — replaced by `build_knowledge_graph` (auto-detects)

## [0.5.0] - 2026-04-08

### Added

- **Knowledge Graph** — 4 new tools for library-wide citation analysis:
  - `build_knowledge_graph` — batch-fetch citation data for all library DOIs via OpenAlex,
    resolve references to DOIs (two-pass), build persistent citation network in SQLite
  - `query_knowledge_graph` — PageRank (influential papers), community detection (clusters),
    betweenness centrality (bridge papers), shortest paths, neighborhood queries, graph stats
  - `find_related_papers` — Semantic Scholar recommendations from library seeds, each flagged
    with `in_library` status. Similar to Connected Papers / ResearchRabbit
  - `sync_knowledge_graph` — incremental update for new/changed items since last build
- `GraphStore` module — SQLite persistence for papers (nodes) and citations (edges) at
  `~/.local/share/zotero-mcp/knowledge_graph.db`
- `KnowledgeGraph` module — NetworkX DiGraph with cached graph analytics
- `SemanticScholarClient` module — paper recommendations via raw httpx (no third-party wrapper)
- `OpenAlexClient.bulk_get_works(dois)` — batch-fetch work metadata (up to 50 DOIs per query)
- `OpenAlexClient.resolve_ids_to_dois(openalex_ids)` — convert OpenAlex work IDs to DOIs
  for DOI-keyed citation graph construction
- `WebClient.get_all_items_with_dois()` — paginated fetcher for all library items with DOIs
- `check_published_versions` tool — checks whether preprints in the library have been
  formally published in a peer-reviewed journal. Uses CrossRef `relation.is-preprint-of`
  (authoritative) and OpenAlex location data (journal name)
- `[graph]` optional extra in pyproject.toml — `pip install zotero-mcp[graph]` adds networkx, numpy, scipy

### Changed

- Tool count increased from 27 to 32
- OpenAlex client now requires API key authentication via `OPENALEX_API_KEY` env var
  (required since Feb 2026 — register free at openalex.org/users/me)
- `capabilities.py` warns if `OPENALEX_API_KEY` is not set
- manifest.json updated to v0.5.0 with `OPENALEX_API_KEY` and `SEMANTIC_SCHOLAR_API_KEY` config fields
- medRxiv DOI detection extended to `10.64898/` prefix (migration from `10.1101/`)
- Development setup simplified to `pip install -e ".[dev,graph]"`
- Python 3.14 compatibility verified

### Removed

- Completed feature plans (Features 1–5, hardening) and original roadmap spec
- Duplicate `[dependency-groups]` section in pyproject.toml

### Breaking

- OpenAlex API key now required for citation graph, retraction checks, and knowledge graph
  tools. Set `OPENALEX_API_KEY` environment variable. The previous polite-pool email
  approach no longer works as of Feb 2026.

## [0.4.0] - 2026-04-07

### Added

- `get_tags` tool — list all tags in the library, optionally filtered by prefix
- `remove_tag` tool — remove a tag from every item in the library (destructive)
- `rename_tag` tool — rename a tag across every item in the library
- `_retry_request` helper in `web_client.py` — exponential backoff for write operations
- `_error_response` / `_handle_tool_errors` decorator in `server.py` — structured JSON error responses instead of unhandled exceptions

### Changed

- Tool count increased from 24 to 27
- Web API reads now catch `httpx.TimeoutException` and return actionable error messages instead of silent hangs
- Search requests use a dedicated 45s timeout to accommodate large libraries via Web API
- Tool descriptions shortened for faster LLM processing
- Read-only tools annotated with `readOnlyHint`, `empty_trash` marked `destructiveHint`
- Citation graph library-membership checks parallelized (5 concurrent workers)
- OpenAlex `get_references` parallelized (5 concurrent workers, was sequential)
- `find_duplicates` computes title similarity during iteration instead of redundant recomputation
- `check_retractions` and `get_item_attachments` return only populated fields, reducing response size

### Fixed

- `_read_local_or_web` web fallback could let `httpx.TimeoutException` propagate unhandled

## [0.3.0] - 2026-04-06

### Added

- `get_pdf_content` tool — smart content router: returns PMCID (for PubMed MCP), local PDF path, web-downloaded PDF, or DOI/URL fallback
- `check_retractions` tool — batch check items for retractions (CrossRef) and corrections/errata (OpenAlex) with citation counts
- `find_duplicates` tool — scan library for duplicate items by exact DOI match and fuzzy title similarity
- `get_citation_graph` tool — get citing and referenced works via OpenAlex with in-library flags
- `trash_items` tool — move items to Zotero trash (reversible), with automatic batching for >50 items
- `empty_trash` tool — permanently delete all trashed items (irreversible, LLM confirms with user)
- `OpenAlexClient` module for retraction checks and citation graph traversal
- `WebClient.resolve_pmid_to_pmcid()` for PMID-to-PMCID conversion via pooled PubMed client
- `WebClient.check_crossref_updates()` for retraction and correction detection via CrossRef
- `WebClient.download_attachment()` for downloading PDFs from Zotero cloud storage
- `LocalClient.get_attachment_path()` for finding local PDF file paths
- Duplicate detection on `create_item_from_url` (DOI check after URL resolution) and `create_item_manual` (DOI check + title similarity)

### Changed

- Tool count increased from 18 to 24
- `create_item_from_url` and `create_item_manual` now check for duplicates before creating items

## [0.2.0] - 2026-04-02

### Added

- Web API read path — all 18 tools work with just API credentials, Zotero desktop no longer required
- `server_status` tool reports available operating modes with fix instructions
- `get_item_attachments` tool with canonical availability states (stored_remote, stored_local, linked_local, metadata_only)
- Capability detection module with Cloud (primary) and Local (optional speedup) operating modes
- Input validation for item keys, collection keys, limits, file paths, and identifiers
- GitHub Actions CI for Python 3.11 and 3.12
- Claude Desktop DXT manifest for one-click extension install

### Changed

- Read routing: local API is now an optional fast path, Web API is the primary read path with automatic fallback
- Read-modify-write operations (update_item, batch_organize, add_to_collection) fall back to web reads when desktop is closed
- Citation tools (write_cited_document, insert_citations) no longer require Zotero desktop
- batch_organize retries once on 412 version conflict and handles 429 rate limits
- All list parameters now use _parse_list_param consistently
- README rewritten with quickstart, operating modes, tool table, and troubleshooting
- Error messages reference operating mode names with actionable fix instructions

### Fixed

- Web client initializes independently when Zotero desktop is closed
- test_server.py expected tool set was missing get_notes and create_note

## [0.1.0] - 2026-04-02

### Added

#### Read tools (Local API)

- `search_items` — keyword search across library, excludes attachments and notes
- `get_item` — full metadata or BibTeX for a single item
- `get_collections` — list all collections with parent info and item counts
- `get_collection_items` — list items in a specific collection
- `get_notes` — child notes attached to an item

#### Write tools (Web API)

- `create_item_from_identifier` — create item from DOI, PMID, or PubMed URL with duplicate detection
- `create_item_from_url` — create item from any URL with translation server scraping
- `create_item_manual` — create item with manually supplied metadata
- `create_note` — create a child note attached to an item
- `batch_organize` — bulk-add tags and/or collection to multiple items
- `create_collection` — create a collection, optionally nested
- `add_to_collection` — add an existing item to a collection
- `update_item` — patch metadata fields with optimistic locking
- `attach_pdf` — attach local PDF or auto-download via Unpaywall/PMC/bioRxiv

#### Citation and document tools

- `write_cited_document` — create new .docx with live Zotero field codes from markdown
- `insert_citations` — insert citations into existing .docx, preserving formatting

#### Identifier resolution

- Zotero translation server as primary resolver
- PubMed efetch fallback with abstract extraction and publication type mapping
- CrossRef fallback for all DOI-registered content (books, conference papers, datasets)
- DOI extraction from arxiv, biorxiv, medrxiv, and doi.org URLs
