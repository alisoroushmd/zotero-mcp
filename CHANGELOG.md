# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

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
