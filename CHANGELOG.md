# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
