# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- `check_published_versions` tool — checks whether preprints in the library have been
  formally published in a peer-reviewed journal. Uses CrossRef `relation.is-preprint-of`
  (authoritative) and OpenAlex location data (journal name). Reports published DOI, journal
  name, and whether the published version is already saved in the library.
- `OpenAlexClient.check_published_version(doi)` — detects preprint type and finds journal
  locations in OpenAlex work metadata
- `WebClient.check_crossref_published(doi)` — reads CrossRef `relation.is-preprint-of` field

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
