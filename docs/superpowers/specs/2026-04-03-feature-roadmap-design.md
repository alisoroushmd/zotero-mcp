# Zotero MCP Feature Roadmap Design Spec

Date: 2026-04-03
Status: Draft
Scope: 5 features, 9 new tools, 18 → 27 total tools

## Context

This spec covers the next set of features for the Zotero MCP server, prioritized by
impact on the primary user's workflows: weekly literature scanning, manuscript writing,
grant preparation, and library management across 10+ active research projects.

### Design Decisions (from brainstorming)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| PDF handling | Content locator, not parser | PubMed MCP provides structured full text for PMC articles; Claude reads PDFs natively via Read tool. Building PyMuPDF parsing duplicates existing capabilities with worse quality. |
| Semantic search deps | Fully local (sentence-transformers + ChromaDB) | No external API dependency. Gated behind `[semantic]` optional extra. |
| Retraction data source | CrossRef (retractions) + OpenAlex (corrections, citation graph) | CrossRef is authoritative for retraction status. OpenAlex provides broader context and reuse for citation graph feature. |
| Duplicate detection | Automatic on create + standalone audit tool | Extends existing `_check_duplicate_doi()`. Title similarity via stdlib `difflib`. |

---

## Feature 1: PDF Content Locator

**Priority:** High (unblocks quality assessment in weekly lit scan)
**New tool:** `get_pdf_content`
**New dependencies:** None
**New files:** None

### Tool: `get_pdf_content(item_key)`

Smart router that finds the best path to a paper's content, returning identifiers or
file paths for the LLM to use with its existing tools.

**Logic (in order):**

1. Read item metadata to get DOI and PMID (from `extra` field).
2. If PMID exists, convert to PMCID via PubMed esearch. If PMCID found, return it so
   the LLM can call PubMed MCP's `get_full_text_article` (structured text, best quality).
3. Get attachment children. Filter for `contentType == "application/pdf"`.
4. If Zotero desktop is running (local API available), return the local file path from
   the attachment's `path` field. Claude reads the PDF natively via Read tool.
5. If cloud-only PDF, download via `GET /items/{attachment_key}/file` from Web API,
   save to a temp file in `tempfile.gettempdir()` with prefix `zotero_mcp_`. Temp files
   are not auto-cleaned (the LLM may read them across multiple turns); they are small
   (single PDFs) and live in the OS temp directory which is cleaned on reboot.
6. If no PDF attached, return the DOI/URL so the LLM can try other sources or ask the
   user.

**Return format:**

```json
{
  "item_key": "ABC123",
  "content_source": "pmc",
  "pmcid": "PMC9046468",
  "message": "Use PubMed MCP get_full_text_article with this PMCID for structured full text."
}
```

or:

```json
{
  "item_key": "ABC123",
  "content_source": "local_pdf",
  "pdf_path": "/Users/.../storage/ABC123/paper.pdf",
  "message": "PDF available locally. Read this file path for full content."
}
```

or:

```json
{
  "item_key": "ABC123",
  "content_source": "web_pdf",
  "pdf_path": "/tmp/zotero_mcp_ABC123.pdf",
  "message": "PDF downloaded from Zotero cloud storage. Read this file path for full content."
}
```

or:

```json
{
  "item_key": "ABC123",
  "content_source": "not_found",
  "doi": "10.1038/...",
  "url": "https://...",
  "message": "No PDF attached. Try accessing via DOI or ask the user for the file."
}
```

**Files touched:**
- `web_client.py` -- add `download_attachment(attachment_key) -> bytes` method
- `local_client.py` -- add `get_attachment_path(attachment_key) -> str | None` method
- `server.py` -- add `get_pdf_content` tool

**Performance:** Local path lookup is near-instant. Web download bounded by Zotero CDN.
PMCID lookup adds one esearch call (~200ms) but avoids downloading a PDF entirely.

---

## Feature 2: Semantic Search

**Priority:** High (grows in value with library size)
**New tools:** `semantic_search`, `build_index`, `rebuild_index`
**New dependencies:** `sentence-transformers`, `chromadb` (in `[semantic]` optional extra)
**New files:** `src/zotero_mcp/semantic.py`

### Architecture

- **Embedding model:** `all-MiniLM-L6-v2` via sentence-transformers (~80MB). Fast
  inference, good quality for scientific text.
- **Vector store:** ChromaDB with SQLite backend. Single persistent file, no external
  server process. Stored at path from `ZOTERO_SEMANTIC_DB` env var, defaulting to
  `~/.zotero-mcp/semantic.db`.
- **What gets embedded:** Title + abstract concatenated. Abstracts are available for
  nearly every item and are the right granularity for "do I already have this?" queries.

### SemanticIndex class (`semantic.py`)

```
class SemanticIndex:
    __init__(db_path, model_name="all-MiniLM-L6-v2")
    embed(text) -> list[float]
    add_items(items: list[dict]) -> int         # batch upsert
    search(query: str, limit: int, collection_filter: str | None) -> list[dict]
    sync(web_client, since_version: int) -> int # incremental update
    rebuild(web_client) -> int                  # full rebuild
    get_library_version() -> int                # stored version watermark
```

### Index Lifecycle

1. **First run:** `build_index` tool fetches all items via Web API (paginated, 100 per
   request), embeds title+abstract, stores in ChromaDB. One-time cost (~2-3 min for a
   few hundred papers). Stores the `library_version` from the API response.
2. **Incremental sync:** On each `semantic_search` call, compare stored
   `library_version` against current version from `GET /users/{id}/items?since={v}&limit=1`.
   If library has advanced, fetch changed items with `since=` parameter, upsert
   embeddings. This is transparent and cheap.
3. **Manual rebuild:** `rebuild_index` tool drops and recreates. For troubleshooting or
   after schema changes.

### Tool: `semantic_search(query, limit=10, collection_key=None)`

1. Auto-sync if library version has advanced (incremental, cheap).
2. Embed the query string.
3. Nearest-neighbor search in ChromaDB. If `collection_key` provided, filter results to
   items in that collection (stored as metadata in ChromaDB).
4. Return ranked results with similarity scores.

```json
{
  "results": [
    {
      "key": "ABC123",
      "title": "Risk stratification of gastric intestinal metaplasia...",
      "score": 0.87,
      "date": "2024",
      "collections": ["K08_REFS"]
    }
  ],
  "index_version": 4523,
  "items_indexed": 312
}
```

### Tool: `build_index()`

Full library indexing. Returns `{items_indexed, duration_seconds}`. Idempotent -- safe
to call multiple times.

### Tool: `rebuild_index()`

Drop existing index, rebuild from scratch. Returns same format as `build_index`.

### Graceful Degradation

If `sentence-transformers` is not installed, all three tools register normally but
return a clear error: `"Semantic search requires: pip install zotero-mcp[semantic]"`.
The import is lazy (inside the tool function body), consistent with how
`citation_writer` is handled. No import-time failure, no impact on other tools.

### Files touched:
- New: `src/zotero_mcp/semantic.py` -- `SemanticIndex` class
- `server.py` -- 3 new tool definitions
- `pyproject.toml` -- add `[semantic]` extra: `sentence-transformers>=2.0`, `chromadb>=0.4`

---

## Feature 3: Retraction Alerts + Correction Checks

**Priority:** Medium-high (safety net for manuscripts and grants)
**New tool:** `check_retractions`
**New dependencies:** None (uses httpx)
**New files:** `src/zotero_mcp/openalex_client.py`

### OpenAlexClient (`openalex_client.py`)

Lightweight wrapper around the OpenAlex API. Reused by Feature 5 (citation graph).

```
class OpenAlexClient:
    __init__(email="zotero-mcp@example.com")   # polite pool User-Agent
    get_work(doi) -> dict | None               # full work metadata
    get_citing_works(doi, limit) -> list[dict]  # Feature 5
    get_references(doi) -> list[dict]           # Feature 5
```

Uses a pooled `httpx.Client` in `__init__`, consistent with existing client patterns.
OpenAlex is free, no API key required.

### CrossRef Extension

Add `check_crossref_updates(doi) -> dict` method to `web_client.py`. Calls existing
CrossRef endpoint (`GET /works/{doi}`), extracts `update-to` field which contains
retractions, corrections, and errata with their DOIs and dates.

### Tool: `check_retractions(item_keys)`

Batch check multiple items at once.

**Logic per item:**
1. Read item DOI (parallel fetch via existing `_fetch_item_metadata` pattern).
2. CrossRef: check `update-to` for retraction/correction entries.
3. OpenAlex: check `is_retracted` boolean, get `cited_by_count`.
4. Merge results, CrossRef is authoritative for retraction status.

**Return format:**

```json
{
  "results": [
    {
      "key": "ABC123",
      "doi": "10.1234/...",
      "title": "...",
      "retracted": false,
      "corrections": [],
      "cited_by_count": 42
    },
    {
      "key": "DEF678",
      "doi": "10.5678/...",
      "title": "...",
      "retracted": true,
      "retraction_doi": "10.5678/retraction",
      "retraction_date": "2025-03-15",
      "corrections": [
        {"type": "erratum", "doi": "10.5678/erratum", "date": "2025-01-10"}
      ],
      "cited_by_count": 8
    }
  ],
  "checked": 5,
  "retracted_count": 1,
  "corrected_count": 0
}
```

### Files touched:
- New: `src/zotero_mcp/openalex_client.py`
- `web_client.py` -- add `check_crossref_updates(doi)` method
- `server.py` -- add `check_retractions` tool

---

## Feature 4: Duplicate Detection

**Priority:** Medium (prevents library entropy)
**New tool:** `find_duplicates`
**New dependencies:** None (uses stdlib `difflib`)
**New files:** None

### Automatic Detection on Create (extend existing paths)

Current state: `create_item_from_identifier` already calls `_check_duplicate_doi()`.

**Extensions:**

| Create path | Current check | Added check |
|-------------|--------------|-------------|
| `create_item_from_identifier` | DOI exact match | PMID match via `extra` field search |
| `create_item_from_url` | None | DOI check after URL resolution (if DOI extracted) |
| `create_item_manual` | None | DOI check if provided; title similarity if no DOI |

**Title similarity:** Normalize both titles (lowercase, strip punctuation/whitespace),
compare with `difflib.SequenceMatcher`. Threshold: ratio > 0.90. This catches "Gastric
intestinal metaplasia detection..." vs "Gastric Intestinal Metaplasia Detection: A..."
without false positives on unrelated papers.

All create paths already return `{duplicate: True, key, title}` format. Extended with
`match_type: "doi" | "pmid" | "title_similarity"` and `similarity: 0.93` for title
matches.

### Tool: `find_duplicates(collection_key=None, limit=100)`

Library-wide or collection-scoped audit.

**Logic:**
1. Fetch items (from collection or full library). Uses existing read path.
2. Group by exact DOI -- any DOI on 2+ items is a definite duplicate.
3. For items without DOIs, cluster by normalized title similarity
   (SequenceMatcher ratio > 0.85, slightly lower threshold for audit mode to surface
   near-misses for human review).
4. Return duplicate groups.

```json
{
  "duplicate_groups": [
    {
      "match_type": "doi",
      "doi": "10.1038/...",
      "items": [
        {"key": "ABC123", "title": "...", "date": "2024"},
        {"key": "DEF456", "title": "...", "date": "2024"}
      ]
    },
    {
      "match_type": "title_similarity",
      "similarity": 0.92,
      "items": [
        {"key": "GHI789", "title": "Risk stratification of gastric..."},
        {"key": "JKL012", "title": "Risk Stratification of Gastric..."}
      ]
    }
  ],
  "total_groups": 2,
  "total_duplicate_items": 4
}
```

### Files touched:
- `web_client.py` -- add `_check_duplicate_title(title)` method, add `find_duplicates()` method
- `server.py` -- add `find_duplicates` tool, update `create_item_from_url` and `create_item_manual` to call duplicate checks

---

## Feature 5: Delete/Trash + Citation Graph

**Priority:** Medium (trash is convenience; citation graph completes the weekly scan loop)
**New tools:** `trash_items`, `empty_trash`, `get_citation_graph`
**New dependencies:** None
**New files:** None (reuses `openalex_client.py` from Feature 3)

### 5a: Delete / Trash Management

**Tool: `trash_items(item_keys)`**

Move items to Zotero trash (reversible).

- Uses `DELETE /users/{id}/items?itemKey=KEY1,KEY2` with `If-Unmodified-Since-Version` header.
- Batch operation, up to 50 items per API call (Zotero limit).
- If more than 50 keys provided, chunk into multiple requests.
- Returns `{trashed: [...keys], failed: [...keys]}`.

**Tool: `empty_trash()`**

Permanently delete all trashed items.

- Uses `DELETE /users/{id}/items/trash`.
- Tool description warns the LLM: "This permanently deletes all items in the Zotero
  trash. Always confirm with the user before calling this tool."
- Returns `{status: "emptied"}`.

No `restore_from_trash` -- the Zotero API does not support it. Trash is the safety net;
`empty_trash` is the point of no return.

### 5b: Citation Graph

**Tool: `get_citation_graph(item_key, direction="both", limit=20)`**

Uses the OpenAlex client from Feature 3.

**Logic:**
1. Read item DOI from Zotero.
2. Query OpenAlex: `GET /works/doi:{doi}`.
3. `direction="cited_by"`: fetch from `cited_by_api_url`, return most recent citing works.
4. `direction="references"`: resolve `referenced_works` OpenAlex IDs.
5. `direction="both"`: return both directions.
6. For each related work, check if it exists in Zotero library (DOI match via
   `_check_duplicate_doi`). Flag `in_library: true/false`.

```json
{
  "item_key": "ABC123",
  "doi": "10.1038/...",
  "title": "...",
  "cited_by_count": 42,
  "cited_by": [
    {
      "title": "A newer paper...",
      "doi": "10.1234/newer",
      "year": 2025,
      "authors": "Smith J et al.",
      "in_library": false
    },
    {
      "title": "Already tracked paper...",
      "doi": "10.5678/tracked",
      "year": 2024,
      "authors": "Lee A et al.",
      "in_library": true,
      "zotero_key": "XYZ789"
    }
  ],
  "references": [...]
}
```

The `in_library` flag answers "what's citing my canonical references that I don't
already have?" -- the key question for the weekly literature scan.

### Files touched:
- `openalex_client.py` -- add `get_citing_works(doi, limit)` and `get_references(doi)` methods
- `web_client.py` -- add `trash_items(keys)` and `empty_trash()` methods
- `server.py` -- add `trash_items`, `empty_trash`, `get_citation_graph` tools

---

## Summary

### New File Map

| File | Purpose | Feature |
|------|---------|---------|
| `src/zotero_mcp/openalex_client.py` | OpenAlex API wrapper | 3, 5b |
| `src/zotero_mcp/semantic.py` | Embedding + ChromaDB vector search | 2 |

### Tool Inventory (18 existing + 9 new = 27 total)

| Tool | Category | Feature |
|------|----------|---------|
| `get_pdf_content` | Read | 1 |
| `semantic_search` | Read | 2 |
| `build_index` | Admin | 2 |
| `rebuild_index` | Admin | 2 |
| `check_retractions` | Read | 3 |
| `find_duplicates` | Read | 4 |
| `trash_items` | Write | 5a |
| `empty_trash` | Write | 5a |
| `get_citation_graph` | Read | 5b |

### Dependency Changes (pyproject.toml)

Base dependencies: unchanged (fastmcp, httpx, python-docx).

New optional extra:
```toml
[project.optional-dependencies]
semantic = [
    "sentence-transformers>=2.0",
    "chromadb>=0.4",
]
```

### Build Order

Features should be built in this order due to dependencies:

1. **Feature 1 (PDF content locator)** -- standalone, no dependencies on other features
2. **Feature 4 (Duplicate detection)** -- extends existing create paths, standalone
3. **Feature 3 (Retraction alerts)** -- introduces `openalex_client.py`
4. **Feature 5 (Trash + Citation graph)** -- reuses openalex_client from Feature 3
5. **Feature 2 (Semantic search)** -- largest scope, independent but benefits from having other features stable first

### Testing Strategy

Each feature gets its own test file following existing patterns (mocked HTTP, no live
Zotero needed):

- `tests/test_pdf_content.py` -- mock local path lookup, web download, PMCID resolution
- `tests/test_duplicates.py` -- mock search results, test title similarity thresholds
- `tests/test_retractions.py` -- mock CrossRef and OpenAlex responses
- `tests/test_openalex.py` -- unit tests for OpenAlex client
- `tests/test_trash.py` -- mock DELETE responses, batch chunking
- `tests/test_citation_graph.py` -- mock OpenAlex citing/reference responses, in_library flag
- `tests/test_semantic.py` -- requires `[semantic]` extra; test embedding, indexing, search, incremental sync, graceful degradation without deps
