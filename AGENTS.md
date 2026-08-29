<!-- SYNCED-FROM-CLAUDE source: /Users/ali.soroush/code/zotero-mcp/CLAUDE.md
     This file is a synced copy — edit the source, then re-run:
     bash /Users/ali.soroush/claude-memory/system/claude-port/sync_from_claude.sh --apply
     Last synced: 2026-08-21 -->
# Zotero MCP Server

MCP server for Zotero. Web API is the primary path for all operations. Local Zotero desktop API is an optional fast path for reads.

## Commands

| Command | Description |
|---------|-------------|
| `pip install -e ".[dev]"` | Install package + dev dependencies (editable) |
| `pip install -e ".[dev,graph]"` | Install with graph extras (networkx, numpy, scipy) |
| `pip install -e ".[dev,graph,fulltext]"` | Install with all extras (adds pypdf for PDF text extraction) |
| `python -m pytest tests/ -v` | Run all tests (mocked HTTP, no Zotero needed) |
| `python -m zotero_mcp` | Start MCP server (stdio transport) |

## Architecture

```
src/zotero_mcp/
├── server.py                  # FastMCP tools + prompts — thin layer, delegates to clients
├── config.py                  # Centralized env var config (singleton, typed)
├── capabilities.py            # Operating mode detection and status reporting
├── local_client.py            # optional fast reads via localhost:23119 (Zotero desktop)
├── web_client.py              # primary client: reads + writes via api.zotero.org
├── openalex_client.py         # OpenAlex API — retraction checks, citation graph, bulk queries
├── citation_writer.py         # .docx generation with Zotero field codes
├── graph_store.py             # SQLite persistence for knowledge graph (papers, citations, entities, fulltext)
├── knowledge_graph.py         # NetworkX graph analytics (PageRank, clusters, paths, temporal)
├── text_extractor.py          # PDF text extraction via pypdf + FTS5 indexing
├── graph_renderer.py          # D3.js HTML visualization of citation/author networks
└── semantic_scholar_client.py # Semantic Scholar recommendations (raw httpx)
```

## Key Files

- `server.py` — all 39 MCP tool definitions + 4 prompts, lazy client init, input validation, parameter parsing
- `ssl_health.py` — SSL/TLS configuration diagnostic: resolves cafile/capath, inspects `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`/etc, runs live HTTPS probes, returns HEALTHY/DEGRADED/BROKEN verdict + remediation
- `local_audit.py` — read-only scan of `~/Zotero/zotero.sqlite` for collection/item keys containing forbidden alphabet characters (`0`, `1`, `O`) that halt Zotero sync
- `config.py` — centralized Config dataclass, `load_config()` / `get_config()` singleton; all env vars read here
- `capabilities.py` — ServerCapabilities dataclass, check_capabilities(), format_status(), TOOL_MODES mapping
- `web_client.py` — largest file: reads + writes via Web API, item creation, PDF attachment, identifier resolution (translation server → PubMed efetch → CrossRef fallback chain), duplicate detection, retraction checks via CrossRef
- `openalex_client.py` — OpenAlex API wrapper: retraction flags, citation counts, citing works, references, published-version detection
- `citation_writer.py` — CSL-JSON conversion, Word field code XML generation, markdown→docx assembly
- `manifest.json` — Claude Desktop DXT extension manifest

## Operating Modes

| Mode | Requirements | What it provides |
|------|-------------|-----------------|
| Cloud (primary) | ZOTERO_API_KEY + ZOTERO_USER_ID env vars | All 39 tools — reads, writes, citations, attachments, analysis, knowledge graph, fulltext, entities, diagnostics |
| Local (optional) | Zotero 7 desktop running with local API enabled | Faster reads via localhost:23119 (no rate limits) |

All tools work with just API credentials. Zotero desktop is never required.
Read tools try local API first (if available), fall back to Web API transparently via `_read_local_or_web()` in server.py.

## Environment

- `ZOTERO_API_KEY` — required (get at zotero.org/settings/keys)
- `ZOTERO_USER_ID` — required (same page)
- `OPENALEX_API_KEY` — required for knowledge graph, citation graph, retraction checks (free at openalex.org/users/me)
- `SEMANTIC_SCHOLAR_API_KEY` — optional, improves rate limits for find_related_papers
- `NCBI_API_KEY` — optional, raises PubMed/eutils limit 3→10 req/s; injected on all eutils calls via `web._pubmed_get()`
- `ZOTERO_DATA_DIR` — optional, path to the Zotero desktop data directory (defaults to `~/Zotero`); used by `audit_local_keys` and `get_pdf_content` (constructs local storage path for `imported_url` attachments)
- `ZOTERO_LINKED_ATTACHMENT_DIR` — optional, where `attach_pdf` writes PDFs for linked-file attachments (defaults to `<ZOTERO_DATA_DIR>/linked-attachments`)
- `ZOTERO_ATTACHMENT_MODE` — optional, `linked` (default) or `imported`. `linked` stores PDFs on local disk as `linked_file` attachments and consumes **no** Zotero cloud storage quota. `imported` restores the legacy upload-into-Zotero-storage behavior, which syncs across devices but counts against the quota and fails with HTTP 413 once it is full. Linked files are not valid in group libraries — use `imported` there.
- Zotero 7 desktop — optional, enables faster local reads

## Code Style

- Type hints on all functions and return types
- Google-style docstrings (Args, Returns, Raises)
- `lowercase_with_underscores` for functions/variables, `PascalCase` for classes

## Performance Rules

MCP tools run synchronously in the LLM's request loop — every millisecond of tool latency is felt by the user. Treat performance as a correctness requirement:

- **Never create `httpx` connections per-request** — reuse pooled `httpx.Client()` instances from `__init__`
- **Parallelize independent I/O** — `write_cited_document` fetches N items via `ThreadPoolExecutor`, not a sequential loop
- **Minimize API round-trips** — trust creation responses instead of verifying with a read-back call
- **Fail fast on known errors** — check duplicate DOI locally before hitting the Web API
- **Lazy imports for heavy modules** — `citation_writer` is imported inside `write_cited_document`, not at module level, to avoid slowing server startup for tools that don't need it
- **Batch when possible** — if adding multiple items, prefer fewer API calls over one-per-item
- **Keep tool functions thin** — server.py should parse params and delegate; no business logic that adds latency

## Gotchas

- **MCP list params**: clients may send `["a","b"]` as a JSON string, not a list — always use `_parse_list_param()` in server.py
- **Connection pooling**: `httpx.Client()` instances are persistent in `__init__` — never create per-request clients
- **Thread safety**: `_get_local()` / `_get_web()` use `threading.Lock` — needed because `write_cited_document` uses ThreadPoolExecutor
- **itemType filter**: the Zotero Web API only honors single-value `itemType` negation (`-attachment`) — passing `-attachment || -note` silently falls back to returning notes. Use `-attachment` and filter notes client-side (see `find_duplicates` in web_client.py)
- **`_get_web()` / `_get_local()` deadlock**: `_init_lock` is a non-reentrant `threading.Lock`. `_get_web()` must call `_get_local()` BEFORE acquiring `_init_lock`, otherwise the nested acquire deadlocks on first initialization when Zotero desktop is running
- **`update_item` versioning**: read version from `Last-Modified-Version` response header, not the pre-update local data
- **`_extract_created_key()`**: single helper for extracting keys from Zotero API creation responses — do not duplicate this logic
- **Translation server**: `translate.zotero.org` is unreliable (503s) — fallback chain: PubMed efetch (abstracts + pub types) → CrossRef (books, CS, arXiv). `create_item` routes URLs through `web.create_item_from_url()` (which extracts DOIs from arxiv/biorxiv/doi.org URLs before falling back to bare webpage stubs) and bare identifiers through `web.create_item_from_identifier()`
- **Field codes**: `write_cited_document` and `insert_citations` produce `ADDIN ZOTERO_ITEM CSL_CITATION {json}` Word field codes — these are live citations, not static text
- **insert_citations vs write_cited_document**: `write_cited_document` creates a new .docx from markdown — use for fresh documents. `insert_citations` opens an existing .docx and replaces `[@KEY]` markers in-place — use when you need to preserve existing formatting (styles, images, headers, page layout)
- **Citation numbering**: `parse_citations()` runs once globally for consistent Vancouver numbers across paragraphs, then per-paragraph blocks get remapped
- **Zotero sync**: items created via Web API don't appear in local Zotero until it syncs — read tools won't find newly created items immediately
- **Child item tools**: `get_notes` and `get_item_attachments` both use `get_children()` on the local API; `create_note` writes through the Web API
- **Attachment availability**: `get_item_attachments` maps `linkMode` to canonical states: `stored_remote_available`, `stored_local_available`, `linked_local_available`, `metadata_only`
- **Input validation**: `_validate_key()` and `_clamp_limit()` in server.py — validation at the tool layer, not in clients
- **412 retry in batch_organize**: on version conflict, re-reads the item and retries the PATCH once. If the re-read reveals the item is already in the desired state (`new_patch` is empty), the item is counted as `skipped` instead of `updated`
- **429 rate limiting in batch_organize**: reads `Retry-After` header, sleeps (capped at 10s), retries once
- **`get_pdf_content` routing**: PMID → PMCID (via PubMed) → local PDF path → web PDF download → free PDF via Unpaywall → PMC → arXiv → bioRxiv/medRxiv → DOI/URL fallback. Uses `resolve_pmid_to_pmcid()` on the pooled PubMed client, not ad-hoc httpx calls. Temp files use `zotero_mcp_` prefix and are cleaned up on write failure
- **arXiv fallback skip**: for arXiv DOIs (`10.48550/arxiv.*`), `_fetch_free_pdf()` checks CrossRef's `relation.is-preprint-of` first. If the preprint has a published version, the arXiv leg is skipped so users get the canonical PDF via institutional access instead of the preprint
- **PDF fetch retry**: all PDF downloads (Unpaywall/PMC/arXiv/bioRxiv) go through `_fetch_pdf_with_retry()` — 3 attempts with exponential backoff (1.5s × 2^n) on transport errors and 5xx. Per-endpoint retry is cheap and avoids spurious failures on flaky hosts
- **Unpaywall placeholder email**: Unpaywall rejects `@example.com` / `@test.com` / `@localhost` etc. with HTTP 422. `_is_usable_polite_email()` returns False for these so `_fetch_free_pdf()` skips the call entirely and logs a warning telling the user to set `ZOTERO_MCP_EMAIL`. CrossRef and OpenAlex `User-Agent` headers use `_polite_user_agent()` which also omits the `mailto:` clause for placeholder emails, preventing fake identities being sent to polite-pool APIs. The UA product string is `_UA_PRODUCT` = `zotero-mcp-plus/<version>` — not the PyPI-colliding, frozen `zotero-mcp/1.0` (ZOT-39). `OpenAlexClient.__init__` routes its `email` override through the same helper
- **Duplicate detection**: `create_item` and `create_item_manual` check for duplicates before creating. DOI match first, then title similarity (SequenceMatcher > 0.90). `find_duplicates` audit uses a lower threshold (0.85) to surface near-misses
- **`check_retractions`**: CrossRef is authoritative for retraction status (`update-to` field). OpenAlex `is_retracted` is a backup signal. Both are checked sequentially per item inside `_check_one()`; the ThreadPoolExecutor parallelizes across items, not across CrossRef/OpenAlex for a single item
- **OpenAlex client**: module-level singleton `_openalex` (initialized via `_get_openalex()`, thread-safe double-checked locking). All tools that call OpenAlex share one pooled `httpx.Client` — never instantiate `OpenAlexClient()` per request. Lazy-imported to avoid startup cost when graph tools aren't used
- **Knowledge graph cache**: `_kg_cache` at module level avoids rebuilding from SQLite on every query. `_invalidate_kg_cache()` after build. Each `GraphStore` instance creates its own sqlite3 connection; connections are closed deterministically via `with GraphStore() as store:` or `try/finally store.close()`
- **Centralized config**: all env vars read via `config.py` singleton (`get_config()`). Capabilities uses `load_config()` (fresh read) since it probes current env state. Tests must call `_reset_config()` when patching env vars
- **Consolidated tools**: `manage_tags(action=list|remove|rename)` replaces `get_tags`/`remove_tag`/`rename_tag`. `build_index(type=graph|fulltext|both)` replaces `build_knowledge_graph`/`build_fulltext_index`. Internal helpers `_build_knowledge_graph()` and `_build_fulltext_index()` hold the logic. `manage_tags` is annotated `_DR` (destructive), not `_WR` — `remove`/`rename` rewrite every item bearing the tag, library-wide (ZOT-37)
- **MCP prompts**: `literature_audit`, `build_and_explore`, `add_and_verify`, `extract_entities` — multi-tool workflow guides registered via `@mcp.prompt()`
- **Knowledge graph build auto-detect**: `build_index(type='graph')` auto-detects full build vs incremental sync based on whether a prior build exists. Set `full_rebuild=True` to force a complete rebuild. Shared indexing logic lives in `_index_works()`
- **referenced_works resolution**: OpenAlex `referenced_works` are OpenAlex IDs, not DOIs. `resolve_ids_to_dois()` does a second-pass batch query to map IDs to DOIs for the DOI-keyed graph
- **Knowledge graph optional extra**: `pip install "zotero-mcp-plus[graph]"` adds networkx. `KnowledgeGraph` raises `ImportError` with install instructions if networkx is missing. NEVER write `zotero-mcp` as the package name — that is a *different project* on PyPI; this package is `zotero-mcp-plus` (pyproject.toml)
- **`get_citation_graph` in_library flag**: checks each work's DOI against the library via `_check_duplicate_doi()`. Parallelized with `ThreadPoolExecutor(max_workers=5)` for speed
- **`trash_items` batching**: Zotero API limits DELETE to 50 keys per request. The method chunks automatically. `empty_trash` is irreversible — tool description warns the LLM to confirm with user
- **Temporal analytics**: `query_knowledge_graph` supports 4 temporal query types (timeline, topic_evolution, citation_velocity, trending). Dates stored as YYYY-MM in `publication_date` column. `_migrate()` adds the column to pre-0.7.0 databases via ALTER TABLE
- **Full-text search**: `build_index(type='fulltext')` uses pypdf (optional `[fulltext]` extra) for bulk FTS5 indexing. Hybrid approach: pypdf extracts searchable text for keyword lookup; the calling LLM reads PDFs natively for deep understanding of tables/figures. Scanned PDFs return no text and won't be FTS5-searchable but are still accessible via `get_pdf_content`
- **Fulltext index thread-safety**: `_build_fulltext_index()` extracts PDF text in a `ThreadPoolExecutor` but writes to SQLite on the main thread only (sqlite3 connections are not thread-safe). The worker returns `{"status": "extracted", "text": ...}` and the loop calls `index_paper_text(store, ...)` serially as futures complete
- **FTS5 virtual table**: `paper_fulltext` uses porter+unicode61 tokenizer. FTS5 rank is negative (more negative = better match). snippet() function extracts context around matches
- **Entity two-tool pattern**: `get_unextracted_abstracts` provides abstracts, the calling LLM extracts entities, `store_entities` persists them. No external LLM dependency — the LLM in the conversation does the extraction
- **Abstract reconstruction**: `OpenAlexClient.reconstruct_abstract()` converts OpenAlex inverted index to plain text. COALESCE in `upsert_paper()` prevents NULL overwrites of existing abstracts
- **Entity normalization**: entity names are lowercased and stripped before storage for deduplication
- **GraphStore encapsulation**: entity queries must use public methods (`entity_exists`, `get_entities_by_type`, `search_entities_by_name`, etc.) — do not access `store._conn` directly from server.py
- **TYPE_CHECKING guard**: `KnowledgeGraph` type annotation in server.py uses `if TYPE_CHECKING:` import — the class is only imported at runtime inside `_get_or_build_kg()`
- **DOI normalization (`_norm_doi`)**: all DOIs entering the graph (storage key, `key_by_doi`, incremental/fulltext dedup) go through `server._norm_doi()` (strip doi.org prefix + lowercase). The `papers.doi` PRIMARY KEY is case-sensitive; OpenAlex returns lowercase while Zotero stores verbatim — normalize at every chokepoint or back-links break (ZOT-22)
- **`_handle_tool_errors` is exhaustive**: catches ValueError→invalid_input, HTTPStatusError→api_error (with truncated body), TimeoutException→timeout, other httpx.HTTPError→network_error, RuntimeError→unavailable, (KeyError/IndexError/TypeError/AttributeError)→internal_error, and a final Exception catch-all. No tool should raise raw to the MCP layer
- **`_read_local_or_web` fallback**: catches `(RuntimeError, httpx.HTTPError)` on the local attempt so an unhealthy desktop (500/timeout/locked) still falls back to the Web API (ZOT-15)
- **OpenAlex retry**: all OpenAlex GETs go through `OpenAlexClient._get_with_retry()` (429 Retry-After + 5xx/transport backoff). `get_references` is a single batched `openalex:` filter query, not N threaded GETs
- **Retry-After parsing**: use `_parse_retry_after(header, fallback)` (web_client, openalex_client, and semantic_scholar_client each carry a copy) — tolerates HTTP-date values per RFC 7231 instead of crashing on `float()`
- **Zotero read retry + Backoff (ZOT-40)**: all Web API read GETs go through `WebClient._read_get()` — 429s retry via `_retry_request` (Retry-After honored), and Zotero's `Backoff` header (sent on 2xx under load) is recorded so the next read sleeps out the window (capped by `_MAX_BACKOFF_SLEEP`). Write paths do not consult the backoff state
- **Semantic Scholar singleton (ZOT-38)**: `_get_s2()` in server.py mirrors `_get_openalex()` — never instantiate `SemanticScholarClient()` per call (it leaks a pooled httpx.Client). S2 failures propagate to `_handle_tool_errors` instead of being swallowed into an empty list
- **Batch key cap (ZOT-42)**: `check_retractions`/`check_published_versions` truncate `item_keys` to `_MAX_BATCH_KEYS` (50) via `_truncate_batch_keys()`; the response carries `truncated`/`submitted`/`processed` fields so the caller can resubmit the rest
- **Atomic .docx overwrite (ZOT-43)**: `insert_citations` saves through `_save_docx_atomic()` (temp file + `os.replace`) because it overwrites its own source document by default — a mid-write crash must never truncate the user's original
- **Destructive version probe**: `trash_items`/`empty_trash`/`remove_tag` use `web._library_version()` (routes the version GET through `_retry_request`, raises if `Last-Modified-Version` absent — never defaults to `"0"`)
- **GraphStore lifecycle**: `__init__` sets WAL + busy_timeout, raises a clear RuntimeError on a corrupt DB, and closes the connection if init fails. Schema create/migrate are gated on `PRAGMA user_version` (bump `SCHEMA_VERSION` when changing tables). Bulk loads should use `with store.batch():` to commit once
- **Client cleanup**: WebClient/LocalClient/OpenAlexClient expose `close()`; server.py registers `_cleanup_clients` via atexit alongside `_cleanup_temp_files`
- **dedup_check_failed flag**: `web._check_duplicate_doi/_check_duplicate_title` set `self._dedup_check_failed` on transient failure; create methods wrap their result via `_with_dedup_warning()` so the caller knows the no-duplicate guarantee didn't hold (ZOT-26)
- **graph_renderer XSS**: data is embedded via `_json_for_script()` (unicode-escapes `<`/`>`/`&`/U+2028/9) and the info panel/legend use `textContent`/DOM nodes, never `innerHTML` with external data
- **pypdf optional handling**: `text_extractor.extract_text_from_pdf` RAISES `ImportError` (with `[fulltext]` hint) when pypdf is missing; `_build_fulltext_index` guards up front with `pypdf_available()` and returns one actionable error instead of N opaque per-PDF failures
- **`_cap_list` / bounded KG outputs**: `query_knowledge_graph` (clusters/timeline/topic_evolution) and `query_authors` (clusters) wrap large lists in `{items, count, total, truncated}` via `server._cap_list()` to protect the context window
- **`_parse_dict_param`**: the dict analogue of `_parse_list_param` — `update_item.fields` tolerates a JSON-string object from clients that send object args as strings
- **Return-shape note**: `get_notes` and `get_item_attachments` return `{items, count}` (not bare lists). `get_pdf_content` returns a `content_source` discriminator (pmc/local_pdf/web_pdf/free_pdf_*/extracted_text/not_found)
- **fastmcp pinned `>=3.2,<4`**: capped to the tested major; bump only after testing the next major. CI builds the wheel and smoke-tests it in a clean venv (base + extras) and asserts pyproject==manifest==tag versions
