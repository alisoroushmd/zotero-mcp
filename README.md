# zotero-mcp

MCP server for [Zotero](https://www.zotero.org/) that lets AI assistants search your library, add papers, and write Word documents with live Zotero citations.

**Hybrid architecture:** reads from Zotero's local API (fast, no auth needed) and writes via the Zotero Web API (proper sync). Metadata resolution uses Zotero's translation server with PubMed fallback.

## Features

| Tool | Description |
|------|-------------|
| `search_items` | Search your library by keyword |
| `get_item` | Get full metadata or BibTeX for an item |
| `get_collections` | List all collections |
| `get_collection_items` | List items in a collection |
| `create_item_from_identifier` | Add a paper by DOI, PMID, or PubMed URL |
| `add_to_collection` | Add an item to a collection |
| `update_item` | Update metadata fields on an item |
| `write_cited_document` | Write a Word doc with live Zotero citations |

Key behaviors:
- Duplicate detection before creating items (checks DOI against local library)
- PubMed fallback when Zotero's translation server is unavailable
- Live Zotero field codes in Word documents (recognized by Zotero Word plugin)
- Connection pooling and parallel fetching for performance
- Optimistic locking on updates (version conflict detection)

## Requirements

- Python 3.11+
- [Zotero 7](https://www.zotero.org/) desktop app (must be running for read operations)
- Zotero Web API key (for write operations)

## Setup

### 1. Enable Zotero's local API

In Zotero: **Settings > Advanced > General** check **"Allow other applications on this computer to communicate with Zotero"**

### 2. Get your Zotero Web API key

1. Go to https://www.zotero.org/settings/keys
2. Create a new key with read/write access to your library
3. Note your **User ID** from https://www.zotero.org/settings/keys (shown at the top of the page)

### 3. Install

```bash
git clone https://github.com/asoroush/zotero-mcp.git
cd zotero-mcp
pip install -e .
```

### 4. Configure your AI client

#### Claude Code

Add to your Claude Code settings (Settings > MCP Servers):

```json
{
  "zotero": {
    "command": "python",
    "args": ["-m", "zotero_mcp"],
    "cwd": "/path/to/zotero-mcp",
    "env": {
      "ZOTERO_API_KEY": "your-api-key",
      "ZOTERO_USER_ID": "your-user-id"
    }
  }
}
```

#### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "zotero": {
      "command": "python",
      "args": ["-m", "zotero_mcp"],
      "cwd": "/path/to/zotero-mcp",
      "env": {
        "ZOTERO_API_KEY": "your-api-key",
        "ZOTERO_USER_ID": "your-user-id"
      }
    }
  }
}
```

> **Note:** You may need to use the full path to your Python binary (e.g., `/opt/miniconda3/bin/python`) if your AI client doesn't inherit your shell PATH.

## Usage examples

Once configured, you can ask your AI assistant:

- "Search my Zotero library for papers about gastric cancer screening"
- "Add this paper to my Zotero: PMID 35486828"
- "Add PMID 35486828 to my Oncology collection"
- "Get the BibTeX for item ABC123"
- "What collections do I have in Zotero?"

### Writing manuscripts with live citations

The `write_cited_document` tool creates Word documents with live Zotero field codes. Use `[@ITEM_KEY]` markers in the text:

```
Gastric cancer screening reduces mortality [@ABC123]. Multiple studies
confirm this finding [@DEF456, @GHI789].
```

The generated `.docx` contains real Zotero field codes with Vancouver-style superscript numbers. When you open it in Word:

1. Zotero will recognize the citations as live
2. Click "Refresh" in the Zotero toolbar once
3. The bibliography is generated automatically
4. You can change citation styles and everything updates

### Full research workflow

With a PubMed MCP server configured alongside this one:

1. **Discover**: "Find recent papers on colorectal cancer screening" (PubMed MCP)
2. **Add**: "Add that paper to my Zotero library" (this MCP)
3. **Organize**: "Add it to my Screening collection" (this MCP)
4. **Write**: "Write a manuscript section citing these papers" (this MCP)
5. **Polish**: Open in Word, Zotero refreshes citations and generates bibliography

## Architecture

```
┌─────────────┐     reads      ┌──────────────────┐
│  AI Client  │ ──────────────>│ Zotero Desktop   │
│  (Claude)   │                │ localhost:23119   │
│             │     writes     ├──────────────────┤
│             │ ──────────────>│ Zotero Web API   │
│             │                │ api.zotero.org   │
│             │   resolves     ├──────────────────┤
│             │ ──────────────>│ Translation Srv  │
│             │                │ + PubMed fallback│
└─────────────┘                └──────────────────┘
```

- **Reads** go to the local API (fast, no auth, works offline)
- **Writes** go to the Web API (triggers proper sync to all your devices)
- **Identifier resolution** tries the Zotero translation server first, falls back to PubMed E-utilities

## Development

```bash
pip install -e .
pip install pytest pytest-asyncio respx

# Run tests
python -m pytest tests/ -v
```

## License

MIT
