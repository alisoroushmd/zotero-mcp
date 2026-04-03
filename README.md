# zotero-mcp

[![CI](https://github.com/alisoroushmd/zotero-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/alisoroushmd/zotero-mcp/actions/workflows/ci.yml)

MCP server that lets AI assistants search, create, organize, and cite from a Zotero library. Produces Word documents with live Zotero field codes.

## Quickstart

Add to your MCP client config (Claude Code, Claude Desktop, etc.):

```json
{
  "zotero": {
    "command": "uvx",
    "args": ["--from", "git+https://github.com/alisoroushmd/zotero-mcp", "zotero-mcp"],
    "env": {
      "ZOTERO_API_KEY": "your-api-key",
      "ZOTERO_USER_ID": "your-user-id"
    }
  }
}
```

Get your API key and user ID at [zotero.org/settings/keys](https://www.zotero.org/settings/keys).

## Operating modes

**All 18 tools work with just API credentials** — Zotero desktop does not need to be running.

| Mode | What it provides | Requirements |
|------|-----------------|--------------|
| **Cloud** (primary) | All reads, writes, citations, and attachments via Zotero Web API | `ZOTERO_API_KEY` + `ZOTERO_USER_ID` env vars |
| **Local** (optional) | Faster reads via Zotero desktop's local API — no rate limits | Zotero 7 desktop running with local API enabled |

When Zotero desktop is running, reads automatically use the faster local API. When it is not, reads fall back to the Web API transparently.

Call `server_status` to check which modes are available.

## Tools

| Tool | Description |
|------|-------------|
| `server_status` | Check which modes are available |
| `search_items` | Search library items by keyword |
| `get_item` | Fetch item metadata or BibTeX |
| `get_collections` | List all collections |
| `get_collection_items` | List items in a collection |
| `get_notes` | List child notes on an item |
| `get_item_attachments` | List attachments with availability status |
| `create_item_from_identifier` | Create item from DOI, PMID, or PubMed URL |
| `create_item_from_url` | Create item from any URL |
| `create_item_manual` | Create item with manual metadata |
| `create_note` | Attach a note to an item |
| `create_collection` | Create a collection |
| `batch_organize` | Bulk-add tags/collection to items |
| `add_to_collection` | Add item to a collection |
| `update_item` | Patch metadata fields |
| `attach_pdf` | Attach a local or auto-downloaded PDF |
| `write_cited_document` | Create new .docx with citations |
| `insert_citations` | Insert citations into existing .docx |

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

## Architecture

```text
┌─────────────┐     reads      ┌──────────────────┐
│  MCP client │ ──────────────>│ Zotero Desktop   │
│             │                │ localhost:23119   │
│             │     writes     ├──────────────────┤
│             │ ──────────────>│ Zotero Web API   │
│             │                │ api.zotero.org   │
│             │   resolves     ├──────────────────┤
│             │ ──────────────>│ Translation Srv  │
│             │                │ PubMed/CrossRef  │
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

Your MCP client runs `uvx` directly from the quickstart config above.

**Option B — local install:**

```bash
git clone https://github.com/alisoroushmd/zotero-mcp.git
cd zotero-mcp
pip install -e .
```

Then configure your MCP client to run `python -m zotero_mcp`.

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `Cloud CRUD mode requires ZOTERO_API_KEY` | Missing env vars | Set `ZOTERO_API_KEY` and `ZOTERO_USER_ID` in your MCP client config |
| Reads are slow | Zotero desktop not running; reads go through Web API | Start Zotero and enable local API for faster reads (optional) |
| Item not found after creation | Zotero sync lag | Items created via Web API appear locally after Zotero syncs (usually seconds) |
| `Version conflict for item` | Item was modified between read and write | Retry the operation; the server uses optimistic locking |
| Translation server 503 | translate.zotero.org is intermittent | The server falls back to PubMed and CrossRef automatically |

## Development

```bash
pip install -e .
pip install pytest pytest-asyncio respx
python -m pytest tests/ -v
```

## License

MIT
