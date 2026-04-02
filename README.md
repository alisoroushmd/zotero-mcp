# zotero-mcp

MCP server for [Zotero](https://www.zotero.org/) that lets AI assistants search a local Zotero library, create and organize items through the Zotero Web API, attach PDFs, and generate Word documents with live Zotero citations.

The server uses a hybrid model:

- Reads use Zotero Desktop's local API at `http://localhost:23119/api`
- Writes use the Zotero Web API so changes sync normally
- Identifier and URL import use Zotero's translation server, with PubMed and CrossRef fallbacks for identifiers

## Current tool surface

The server currently exposes 16 MCP tools.

| Tool | Description |
| --- | --- |
| `search_items` | Search library items by keyword; excludes attachments and notes |
| `get_item` | Fetch a single item as JSON or BibTeX |
| `get_collections` | List collections with parent info and item counts |
| `get_notes` | List child notes attached to an item |
| `get_collection_items` | List items in a collection |
| `create_item_from_identifier` | Create an item from a DOI, PMID, or PubMed URL |
| `create_item_from_url` | Create an item from a URL using Zotero's `/web` translator when possible |
| `create_item_manual` | Create an item from manually supplied metadata |
| `create_note` | Create a child note attached to an item |
| `batch_organize` | Bulk-add tags and/or a collection to multiple items |
| `create_collection` | Create a collection, optionally nested under a parent |
| `add_to_collection` | Add an existing item to a collection |
| `update_item` | Patch metadata fields on an existing item |
| `attach_pdf` | Attach a local PDF or try to fetch a free PDF by DOI |
| `insert_citations` | Insert live Zotero citations into an existing `.docx` |
| `write_cited_document` | Create a new `.docx` with live Zotero citations |

## What the code currently does

- Duplicate detection on identifier imports checks DOI matches against the local Zotero library when Zotero Desktop is running.
- Identifier resolution tries Zotero translation first, then PubMed efetch, then CrossRef.
- URL imports try Zotero's `/web` translation endpoint first, then DOI extraction from the URL, then fall back to a basic `webpage` item.
- Bulk organization, collection updates, and item updates use read-modify-write against the local API plus version-aware PATCH requests to the Web API.
- `attach_pdf` can auto-download open PDFs from Unpaywall, PubMed Central, bioRxiv, and medRxiv; otherwise it returns guidance for supplying a local PDF path.
- Word document tools emit real Zotero field codes (`ADDIN ZOTERO_ITEM` / `ADDIN ZOTERO_BIBL`) that Zotero for Word can refresh.
- `insert_citations` scans both paragraphs and tables and preserves the document's existing structure and styling as much as `python-docx` allows.
- `write_cited_document` supports `[@ITEM_KEY]` and grouped citations like `[@KEY1, @KEY2]`, plus basic markdown headings, bold, and italics.

## Requirements

- Python 3.11+
- [Zotero 7](https://www.zotero.org/) desktop app
- Zotero local API enabled in Zotero Desktop for all read operations
- Zotero Web API key and user ID for write operations

Important runtime expectations:

- Zotero Desktop must be running for `search_items`, `get_item`, `get_collections`, `get_notes`, and `get_collection_items`.
- Zotero Desktop is also required for workflows that read local metadata before writing, including duplicate detection, `batch_organize`, `add_to_collection`, `update_item`, and both citation-writing tools.
- Pure web-write operations like `create_collection`, `create_note`, `create_item_manual`, and most item creation paths use the Web API, but you still want Zotero Desktop running if you expect immediate local visibility.

## Setup

### 1. Enable Zotero's local API

In Zotero Desktop, go to **Settings > Advanced > General** and enable **Allow other applications on this computer to communicate with Zotero**.

### 2. Create a Zotero Web API key

1. Go to https://www.zotero.org/settings/keys
2. Create a key with write access to the library you want to manage
3. Copy the API key
4. Note the Zotero **User ID** shown on the same page

### 3. Install

```bash
git clone https://github.com/asoroush/zotero-mcp.git
cd zotero-mcp
pip install -e .
```

### 4. Configure your MCP client

#### Claude Code

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

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS or `%APPDATA%\\Claude\\claude_desktop_config.json` on Windows:

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

If your client does not inherit your shell `PATH`, use the full path to the Python executable.

## Usage notes

Examples:

- "Search my Zotero library for papers about gastric cancer screening"
- "Get the BibTeX for item `ABC123`"
- "Show me the child notes on item `ABC123`"
- "Add this paper to Zotero: PMID 35486828"
- "Create a note on item `ABC123` summarizing the trial design"
- "Add these items to my Screening collection and tag them `review`"
- "Attach the PDF to item `ABC123`"

### Writing with live citations

Both document tools use `[@ITEM_KEY]` markers:

```text
Gastric cancer screening reduces mortality [@ABC123]. Multiple studies
support this finding [@DEF456, @GHI789].
```

- `write_cited_document` creates a new `.docx`
- `insert_citations` modifies an existing `.docx`, including citations inside tables
- Citations are emitted as Vancouver-style superscript numbers
- A `References` section with a live Zotero bibliography field is appended if needed

After opening the document in Word with the Zotero plugin installed:

1. Zotero should recognize the citations as live fields
2. Click Zotero's `Refresh`
3. Zotero will populate or refresh the bibliography
4. You can switch citation styles from Word through Zotero

## Architecture

```text
┌─────────────┐     reads      ┌──────────────────┐
│  MCP client │ ──────────────>│ Zotero Desktop   │
│             │                │ localhost:23119  │
│             │     writes     ├──────────────────┤
│             │ ──────────────>│ Zotero Web API   │
│             │                │ api.zotero.org   │
│             │   resolves     ├──────────────────┤
│             │ ──────────────>│ Translation Srv  │
│             │                │ PubMed/CrossRef  │
└─────────────┘                └──────────────────┘
```

- Local API: fast reads and local state needed for read-modify-write operations
- Web API: authoritative writes and file upload registration
- Translation server: first-pass metadata extraction for identifiers and URLs
- PubMed/CrossRef fallbacks: identifier recovery when translation is unavailable or incomplete

## Development

```bash
pip install -e .
pip install pytest pytest-asyncio respx
python -m pytest tests/ -q
```

At the time of this update, the local test suite passes with `61 passed`.

## License

MIT
