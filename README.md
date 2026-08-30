# zotero-mcp

[![CI](https://github.com/alisoroushmd/zotero-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/alisoroushmd/zotero-mcp/actions/workflows/ci.yml)

MCP server that lets AI assistants search, create, organize, and cite from a Zotero library. Produces Word documents with live Zotero field codes. Checks for retractions, finds duplicates, maps citation graphs, and builds a knowledge graph with paper recommendations.

## Quickstart

Add to your MCP client config (Claude Code, Claude Desktop, etc.):

```json
{
  "zotero": {
    "command": "uvx",
    "args": ["zotero-mcp-plus"],
    "env": {
      "ZOTERO_API_KEY": "your-api-key",
      "ZOTERO_USER_ID": "your-user-id"
    }
  }
}
```

Get your API key and user ID at [zotero.org/settings/keys](https://www.zotero.org/settings/keys).

**`OPENALEX_API_KEY` is also required for the analysis tools** (`check_retractions`, `get_citation_graph`, `check_published_versions`). To enable those plus the knowledge graph and full-text search tools, add the extras and an OpenAlex key:

```json
{
  "zotero": {
    "command": "uvx",
    "args": ["zotero-mcp-plus[graph,fulltext]"],
    "env": {
      "ZOTERO_API_KEY": "your-api-key",
      "ZOTERO_USER_ID": "your-user-id",
      "OPENALEX_API_KEY": "your-openalex-key"
    }
  }
}
```

Free OpenAlex key at [openalex.org/users/me](https://openalex.org/users/me).

## Operating modes

**All 39 tools work with just API credentials** — Zotero desktop does not need to be running. Two diagnostic tools (`check_ssl_health`, `audit_local_keys`) require no credentials at all.

| Mode | What it provides | Requirements |
| --- | --- | --- |
| **Cloud** (primary) | All reads, writes, citations, attachments, analysis, fulltext, entities | `ZOTERO_API_KEY` + `ZOTERO_USER_ID` env vars |
| **Local** (optional) | Faster reads via Zotero desktop's local API (no rate limits) | Zotero 7 desktop running with local API enabled |

When Zotero desktop is running, reads automatically use the faster local API. When it is not, reads fall back to the Web API transparently.

Call `server_status` to check which modes are available.

## Tools

### Read tools

| Tool                     | Description                                                               |
| ------------------------ | ------------------------------------------------------------------------- |
| `server_status`        | Check which modes are available                                           |
| `search_items`         | Search library items by keyword                                           |
| `get_item`             | Fetch item metadata or BibTeX                                             |
| `get_collections`      | List all collections                                                      |
| `get_collection_items` | List items in a collection                                                |
| `get_notes`            | List child notes on an item                                               |
| `get_item_attachments` | List attachments with availability status                                 |
| `get_pdf_content`      | Find best path to a paper's full text (PMCID, local PDF, or web download). Pass `extract_text=true` for inline text |

### Write tools

| Tool                            | Description                                                          |
| ------------------------------- | -------------------------------------------------------------------- |
| `create_item`                 | Create item from DOI, PMID, or URL (with duplicate detection)        |
| `create_item_manual`          | Create item with manual metadata (with duplicate detection)          |
| `create_note`                 | Attach a note to an item                                             |
| `create_collection`           | Create a collection                                                  |
| `batch_organize`              | Bulk-add tags/collection to items                                    |
| `add_to_collection`           | Add item to a collection                                             |
| `update_item`                 | Patch metadata fields                                                |
| `attach_pdf`                  | Attach a local or auto-downloaded PDF                                |
| `trash_items`                 | Move items to trash (reversible)                                     |
| `inspect_trash`               | List trash contents — call before `empty_trash`                      |
| `empty_trash`                 | Permanently delete all trashed items (global, irreversible)          |
| `plan_attachment_migration`   | Dry-run plan for converting cloud attachments to linked files        |
| `migrate_attachments`         | Convert cloud attachments to linked files (dry run unless `apply=true`) |
| `manage_tags`                 | List, remove, or rename tags library-wide (`action="list\|remove\|rename"`) |

### Citation tools

| Tool                     | Description                                 |
| ------------------------ | ------------------------------------------- |
| `write_cited_document` | Create new .docx with live Zotero citations |
| `insert_citations`     | Insert citations into existing .docx        |

### Analysis tools

| Tool                   | Description                                                                  |
| ---------------------- | ---------------------------------------------------------------------------- |
| `check_retractions`        | Check items for retractions, corrections, and errata via CrossRef + OpenAlex      |
| `find_duplicates`          | Scan library for duplicate items by DOI and title similarity                      |
| `get_citation_graph`       | Get citing/referenced works via OpenAlex with in-library flags                    |
| `check_published_versions` | Check if preprints have been formally published; reports journal and in-library status |

### Knowledge graph tools

Requires the `graph` extra (`uvx "zotero-mcp-plus[graph]"` or `pip install "zotero-mcp-plus[graph]"`) and `OPENALEX_API_KEY` env var.

| Tool                       | Description                                                                       |
| -------------------------- | --------------------------------------------------------------------------------- |
| `build_index`              | Build or update citation graph and/or fulltext index (`type="graph\|fulltext\|both"`) |
| `query_knowledge_graph`    | PageRank, clusters, bridge papers, shortest paths, neighborhood, timeline, topic evolution, citation velocity, trending |
| `find_related_papers`      | Semantic Scholar recommendations from library seeds (like Connected Papers)        |
| `query_authors`            | Prolific/influential authors, co-author lookup, ego network, author clusters      |
| `export_knowledge_graph`   | Interactive HTML visualization (citations, authors, or full view) with D3.js      |

### Full-text search tools

Requires the `fulltext` extra (`uvx "zotero-mcp-plus[fulltext]"` or `pip install "zotero-mcp-plus[fulltext]"`).

| Tool                       | Description                                                                       |
| -------------------------- | --------------------------------------------------------------------------------- |
| `search_fulltext`          | Search indexed full text with BM25 ranking and highlighted snippets               |

Use `build_index(type='fulltext')` to extract text from library PDFs and build the FTS5 search index.

### Entity extraction tools

Two-tool pattern: the MCP server provides abstracts, the calling LLM extracts entities, and stores them back.

| Tool                       | Description                                                                       |
| -------------------------- | --------------------------------------------------------------------------------- |
| `get_unextracted_abstracts`| Get papers with abstracts not yet entity-extracted                                |
| `store_entities`           | Persist typed entities (biomarker, drug, gene, etc.) extracted by the LLM         |
| `search_entities`          | Query entity index: by_name, by_type, paper_entities (by DOI), co_occurrence, shared_entities |

### Diagnostic tools

These two tools require no API credentials and can be used to debug setup problems.

| Tool                | Description                                                                          |
| ------------------- | ------------------------------------------------------------------------------------ |
| `check_ssl_health`  | Diagnose Python SSL/TLS config — cert paths, CA count, env-var overrides, live HTTPS probes. Returns HEALTHY/DEGRADED/BROKEN verdict with remediation steps. Use when any tool reports `CERTIFICATE_VERIFY_FAILED`. |
| `audit_local_keys`  | Scan local Zotero SQLite for item/collection keys containing characters (`0`, `1`, `O`) that the Zotero sync server rejects, halting sync. |

### MCP Prompts

Pre-built multi-tool workflows that guide the AI through common tasks:

| Prompt                | Description                                                                     |
| --------------------- | ------------------------------------------------------------------------------- |
| `literature_audit`    | Check retractions, verify preprint publication status, scan for duplicates       |
| `build_and_explore`   | Build indexes and explore research landscape (influential papers, clusters, trends) |
| `add_and_verify`      | Add a paper, check retractions, attach PDF, find related work                   |
| `extract_entities`    | Extract biomedical entities from unprocessed abstracts and store them            |

## Writing with live citations

Both document tools use `[@ITEM_KEY]` markers in content:

```text
Gastric cancer screening reduces mortality [@ABC123]. Multiple studies
support this finding [@DEF456, @GHI789].
```

- `write_cited_document` creates a new .docx from markdown
- `insert_citations` modifies an existing .docx (preserves formatting, including tables)
- Citations are emitted as Vancouver-style superscript numbers
- A References section with a live Zotero bibliography field is appended

After opening in Word with the Zotero plugin: click Refresh to populate the bibliography and switch citation styles.

## Reclaiming Zotero cloud storage quota

`attach_pdf` writes linked files by default, so it no longer consumes quota.
Attachments created *before* that change are still imported (cloud-stored) and
still count against it. `zotero-migrate-attachments` converts `imported_file`
attachments in place by default: each file is copied or downloaded to local
disk, hash-verified, replaced with a `linked_file` attachment on the same parent
item, and the original is trashed.

Always start with the dry run — it is the default, and writes nothing:

```bash
zotero-migrate-attachments
```

Read the plan, then convert a small batch first:

```bash
zotero-migrate-attachments --apply --modes imported_file --limit 5
```

Check that those five items look right in Zotero (the PDF should open, and the
attachment icon shows a link), then run the rest:

```bash
zotero-migrate-attachments --apply
```

Trashing is reversible and the quota is not returned until the trash is
emptied. The migration records its keys in
`.zotero-attachment-migration-trash.json` under the destination directory
before trashing anything. `DELETE /items/trash` is global, so review the trash,
then use the separate purge-only invocation:

```bash
zotero-migrate-attachments --apply --empty-trash
```

That loads the keys persisted by the earlier invocation and refuses to run if
the trash holds anything the migration did not put there. If `--dest` was used
for migration, pass the same `--dest` here. The command never infers ownership
from whatever happens to be in the trash.

`imported_url` snapshots are excluded by default because one Zotero snapshot
can contain an HTML file plus companion resources. To opt in, use
`--modes imported_url`; even then, a snapshot is migrated only when it is
already downloaded locally and its storage directory contains exactly one
regular file. Cloud-only or multi-resource snapshots are skipped rather than
flattened and damaged.

Useful flags: `--modes imported_file` (the default), `--modes imported_url`
(explicit validated-snapshot opt-in), `--dest DIR` (default
`<ZOTERO_DATA_DIR>/linked-attachments`), `--no-trash` (create the links now,
trash later), `--include-local-only` (also convert attachments Zotero holds no
cloud file for — these free no quota), and `--limit N`.

The same operations are exposed as MCP tools: `plan_attachment_migration`,
`migrate_attachments`, `inspect_trash`.

**Back up the linked-attachment directory.** Linked files do not sync; after
migrating, those bytes exist only on this machine.

## Architecture

```text
┌─────────────┐  reads+writes  ┌──────────────────┐
│  MCP client │ ──────────────>│ Zotero Web API   │
│             │                │ api.zotero.org   │
│             │  reads (fast)  ├──────────────────┤
│             │ ─ ─ ─ ─ ─ ─ ─>│ Zotero Desktop   │
│             │    (optional)  │ localhost:23119  │
│             │   resolves     ├──────────────────┤
│             │ ──────────────>│ Translation Srv  │
│             │                │ PubMed/CrossRef  │
│             │   analysis     ├──────────────────┤
│             │ ──────────────>│ OpenAlex         │
│             │                │ CrossRef updates │
│             │  knowledge     ├──────────────────┤
│             │  graph + FTS5  │ SQLite + NetworkX│
│             │                │ (local cache)    │
│             │  related       ├──────────────────┤
│             │  papers        │ Semantic Scholar │
└─────────────┘                └──────────────────┘
```

## Setup

### 1. Create a Zotero Web API key (required)

1. Go to [zotero.org/settings/keys](https://www.zotero.org/settings/keys)
2. Create a key with write access to your library
3. Copy the API key and note your User ID

### 2. Enable Zotero's local API (optional, for faster reads)

In Zotero Desktop: **Settings > Advanced > General** > enable **Allow other applications on this computer to communicate with Zotero**. This is optional — without it, all reads go through the Web API.

### 3. Install

**Option A — uvx (recommended, no clone needed):**

Install `uv` first if you don't have it: [astral.sh/uv](https://astral.sh/uv) (one-line install on macOS/Linux). Then use `zotero-mcp-plus` for the base install or `zotero-mcp-plus[graph,fulltext]` for the full feature set (see Quickstart above). Your MCP client runs `uvx` directly — no separate install step.

**Option B — local install:**

```bash
git clone https://github.com/alisoroushmd/zotero-mcp.git
cd zotero-mcp
pip install -e ".[graph,fulltext]"
```

Then configure your MCP client to run `python -m zotero_mcp`.

**Developing against a live install (uvx + local source):**

If your MCP client runs the server via `uvx --from 'zotero-mcp-plus @ file:///path/to/zotero-mcp'` (or any `file://` reference to this checkout), be aware that **uvx builds a wheel once and caches it**. Editing the source afterwards silently does nothing — the running server keeps using the cached wheel until either:

- the `version` in `pyproject.toml` is bumped (a new version → a new cache entry), or
- the cache is invalidated explicitly: `uvx --reinstall --from '…' zotero-mcp` for one run, or `uv cache clean zotero-mcp-plus` to purge it.

Either way, the MCP client must then be fully restarted (not just reconnected) so it launches a fresh server process. If a fix you just made doesn't seem to take effect, this cache is almost always why. For rapid iteration, prefer Option B (`pip install -e`) — editable installs pick up source edits on every server start with no cache in the way.

### 4. Set up OpenAlex API key (required for analysis and knowledge graph tools)

`OPENALEX_API_KEY` is required for: `check_retractions`, `get_citation_graph`, `check_published_versions`, `build_index(type='graph')`, `query_knowledge_graph`, `query_authors`, and `export_knowledge_graph`. OpenAlex requires a free API key as of Feb 2026:

1. Register at [openalex.org/users/me](https://openalex.org/users/me)
2. Set `OPENALEX_API_KEY` in your MCP client config

### 5. Install knowledge graph support (optional)

With uvx (add to your MCP client config `args`):

```text
zotero-mcp-plus[graph]
```

With pip (local install):

```bash
pip install "zotero-mcp-plus[graph]"
```

Adds networkx, numpy, and scipy for `build_index(type='graph')`, `query_knowledge_graph`, `query_authors`, and `export_knowledge_graph`. `find_related_papers` works without it (uses Semantic Scholar API directly).

Optionally set `SEMANTIC_SCHOLAR_API_KEY` for improved rate limits.

### 6. Install full-text search support (optional)

With uvx (add to your MCP client config `args`):

```text
zotero-mcp-plus[fulltext]
```

With pip (local install):

```bash
pip install "zotero-mcp-plus[fulltext]"
```

Adds pypdf for extracting text from PDFs. Used by `build_index(type='fulltext')` to build a searchable FTS5 index. Without it, `search_fulltext` and `build_index(type='fulltext')` return an install prompt.

## Environment variables

| Variable                   | Required | Description |
| -------------------------- | -------- | ----------- |
| `ZOTERO_API_KEY`           | Yes      | Zotero Web API key — get at [zotero.org/settings/keys](https://www.zotero.org/settings/keys) |
| `ZOTERO_USER_ID`           | Yes      | Zotero user/group ID — same page as the API key |
| `OPENALEX_API_KEY`         | For analysis/graph tools | Required for `check_retractions`, `get_citation_graph`, `check_published_versions`, and all knowledge-graph tools — free at [openalex.org/users/me](https://openalex.org/users/me) |
| `ZOTERO_MCP_EMAIL`         | No       | Your email address. Sent in User-Agent headers to CrossRef/OpenAlex polite pools and required for Unpaywall PDF lookup in `attach_pdf`. Without it, Unpaywall PDF fetches are skipped. |
| `SEMANTIC_SCHOLAR_API_KEY` | No       | Improves rate limits for `find_related_papers` — free at [semanticscholar.org](https://www.semanticscholar.org/product/api) |
| `NCBI_API_KEY`             | No       | Raises the PubMed/eutils rate limit from 3 to 10 req/s — helps bulk identifier resolution and graph builds. Free at [ncbi.nlm.nih.gov/account](https://www.ncbi.nlm.nih.gov/account/settings/). |
| `ZOTERO_DATA_DIR`          | No       | Override path to Zotero desktop data directory (default: `~/Zotero`). Used by `audit_local_keys` and the local PDF path resolver in `get_pdf_content`. |
| `ZOTERO_LINKED_ATTACHMENT_DIR` | No   | Where `attach_pdf` writes PDFs for linked-file attachments (default: `<ZOTERO_DATA_DIR>/linked-attachments`). |
| `ZOTERO_ATTACHMENT_MODE`   | No       | `linked` (default) stores PDFs on local disk as `linked_file` attachments and consumes **no** Zotero cloud storage quota. `imported` uploads into Zotero storage instead — syncs across devices, but counts against the quota and fails with HTTP 413 once it is full. Linked-file attachments are not valid in group libraries; use `imported` there. |
| `ZOTERO_MCP_GRAPH_DB`      | No       | Override path for the knowledge-graph SQLite database (default: OS-native per-user data dir — `~/Library/Application Support/zotero-mcp/` on macOS, `%LOCALAPPDATA%\zotero-mcp\` on Windows, `~/.local/share/zotero-mcp/` elsewhere; an existing legacy `~/.local/share` DB is reused). |
| `ZOTERO_MCP_GRAPH_MATERIALIZATION_LIMIT` | No | Maximum combined persisted graph records loaded into NetworkX (papers, citations, topics, authors, and paper-author links). Defaults to `100000`; oversized graphs are refused before materialization to protect server memory. |
| `XDG_DATA_HOME`            | No       | Standard XDG override for the default graph DB location. |
| `PARENT_WATCHDOG_DISABLE`  | No       | Set to `1` to disable the orphan-process watchdog that kills the server when the parent process (Claude.app, uvx, etc.) exits. |

## Troubleshooting

| Problem | Cause | Fix |
| ------- | ----- | --- |
| `Cloud CRUD mode requires ZOTERO_API_KEY` | Missing env vars | Set `ZOTERO_API_KEY` and `ZOTERO_USER_ID` in your MCP client config |
| `CERTIFICATE_VERIFY_FAILED` on any tool | Python SSL misconfiguration | Run `check_ssl_health` — it diagnoses the cert bundle and returns remediation steps |
| `attach_pdf` never finds a free PDF | `ZOTERO_MCP_EMAIL` not set | Unpaywall requires a real email address. Set `ZOTERO_MCP_EMAIL` in your config. |
| Analysis tools fail without obvious error | `OPENALEX_API_KEY` missing | `check_retractions`, `get_citation_graph`, and `check_published_versions` all require it |
| Knowledge graph materialization is refused | The persisted graph exceeds the default 100,000-record in-memory safety ceiling | Reduce/rebuild the graph, or set `ZOTERO_MCP_GRAPH_MATERIALIZATION_LIMIT` higher only when the server has enough memory |
| Reads are slow | Zotero desktop not running; reads go through Web API | Start Zotero and enable local API for faster reads (optional) |
| Item not found after creation | Zotero sync lag | Items created via Web API appear locally after Zotero syncs (usually seconds) |
| `Version conflict for item` | Item was modified between read and write | Retry the operation; the server uses optimistic locking |
| Translation server 503 | translate.zotero.org is intermittent | The server falls back to PubMed and CrossRef automatically |
| Orphan server processes accumulate | Parent exits without closing stdin | Normal behavior is auto-handled by the watchdog. Disable with `PARENT_WATCHDOG_DISABLE=1` if it conflicts with your setup. |

## Development

```bash
pip install -e ".[dev,graph,fulltext]"
python -m pytest tests/ -v
```

## License

MIT
