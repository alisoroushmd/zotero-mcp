# v0.6.0 Design Spec — Knowledge Graph Enrichment

**Date:** 2026-04-09
**Status:** Completed
**Builds on:** v0.5.0 (knowledge graph foundation — GraphStore, KnowledgeGraph, SemanticScholarClient, OpenAlex bulk queries)

## Goal

Enrich the v0.5.0 knowledge graph with topic labels, author relationships, and interactive visualization. Three features, 32 → 35 tools.

## Scope

### In scope (v0.6.0)

1. **Topic clustering** — Fetch OpenAlex topic classifications at build time, label graph clusters by dominant subfield
2. **Graph visualization** — D3.js HTML export with 3 views (citations, authors, full), rendered via Claude Preview
3. **Author co-citation network** — Structured author records, co-authorship edges, 2 new query tools

### Deferred (v0.7.0+)

- **nano-graphrag** — LLM-based entity extraction from abstracts (adds cost and complexity)
- **Cross-library analysis** — Zotero group library support

---

## New Tools

| Tool | Description | Mode |
|------|-------------|------|
| `export_knowledge_graph` | Generate interactive D3.js HTML visualization | any_read |
| `query_authors` | Query author co-citation network (prolific, influential, coauthors, clusters) | any_read |
| `get_author_network` | Get ego network for a specific author | any_read |

## Modified Tools/Modules

| Component | Change |
|-----------|--------|
| `build_knowledge_graph` | Also fetch OpenAlex `topics` field, store topics, add author nodes + co-authorship edges |
| `sync_knowledge_graph` | Same enrichments for incremental updates |
| `KnowledgeGraph.get_clusters()` | Label clusters by dominant OpenAlex subfield |
| `KnowledgeGraph.build_from_store()` | Build unified graph with paper + author nodes |
| `GraphStore` | New tables: `authors`, `paper_authors`, `paper_topics` |

---

## Feature 1: Topic Clustering

### Data source

OpenAlex `topics` field on each work. Each work has 1–3 topics with hierarchical structure: topic → subfield → field → domain. All levels are stored, but `subfield` is the primary clustering label — sweet spot between too granular (topic) and too broad (domain).

### How cluster labeling works

1. v0.5.0's `get_clusters()` already detects communities via greedy modularity
2. For each community, count the `subfield` values across all member papers
3. Label the cluster with the most common subfield (e.g., "Gastroenterology", "Computer Vision")
4. Include runner-up subfields if they represent >20% of the cluster

### Updated `get_clusters()` output

```json
{
  "cluster_id": 0,
  "size": 12,
  "label": "Gastroenterology",
  "secondary_labels": ["Oncology"],
  "topic_distribution": {"Gastroenterology": 7, "Oncology": 3, "Pathology": 2},
  "papers": [...]
}
```

No new tool needed — this enriches the existing `query_knowledge_graph(query_type="clusters")` response.

### Data model: `paper_topics` table

```sql
CREATE TABLE IF NOT EXISTS paper_topics (
    doi TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    topic_name TEXT,
    subfield TEXT,
    field TEXT,
    domain TEXT,
    score REAL,
    PRIMARY KEY (doi, topic_id)
);
```

---

## Feature 2: Author Co-Citation Network

### Data capture

When `build_knowledge_graph` fetches works from OpenAlex, the `authorships` array is already in the response. Currently only author names are extracted as a semicolon-joined string. In v0.6.0, we also store structured author records:

- `openalex_author_id` (stable identifier)
- `display_name`
- `orcid` (from `authorships[].author.orcid`)
- `institution` (from `authorships[].institutions[0].display_name` — first listed)

### Data model: `authors` and `paper_authors` tables

```sql
CREATE TABLE IF NOT EXISTS authors (
    openalex_author_id TEXT PRIMARY KEY,
    display_name TEXT,
    orcid TEXT,
    institution TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paper_authors (
    doi TEXT NOT NULL,
    openalex_author_id TEXT NOT NULL,
    position INTEGER,
    PRIMARY KEY (doi, openalex_author_id)
);

CREATE INDEX IF NOT EXISTS idx_paper_authors_author
    ON paper_authors(openalex_author_id);
```

### Graph construction

During `KnowledgeGraph.build_from_store()`:

1. Add author nodes (`type="author"`) from the `authors` table
2. Add paper→author edges from `paper_authors`
3. Derive co-authorship edges: for each pair of authors sharing ≥1 paper, add an undirected edge weighted by shared paper count

Co-authorship edges are derived at graph-build time, not stored in SQLite. This keeps the source of truth in the join table and avoids sync issues.

### `query_authors(query_type, ...)` tool

| Query type | Parameters | Returns |
|------------|-----------|---------|
| `prolific` | `limit` (default 10) | Authors ranked by paper count in library |
| `influential` | `limit` (default 10) | Authors ranked by summed PageRank of their papers |
| `coauthors_of` | `author_name`, `limit` | Co-authors of a given author, ranked by shared papers |
| `clusters` | — | Author communities (greedy modularity on co-authorship subgraph) |

### `get_author_network(author_name, depth=1)` tool

Returns the ego network: the author, their co-authors within N hops, shared papers, and topic overlap between connected authors. Similar to v0.5.0's `get_neighborhood()` but filtered to the author subgraph.

### Author matching

`author_name` is fuzzy-matched against `display_name`:
1. Case-insensitive substring match first
2. `SequenceMatcher` (ratio > 0.85) if no exact hit
3. Returns error if ambiguous (multiple matches above threshold)

---

## Feature 3: Graph Visualization

### Output

A self-contained HTML file using D3.js v7 force-directed layout, styled consistently with the existing `knowledge-graph.html` prototype.

### `export_knowledge_graph(view, path)` tool

| Parameter | Type | Description |
|-----------|------|-------------|
| `view` | `str` | `"citations"` (default), `"authors"`, or `"full"` |
| `path` | `str` | Optional output path. Defaults to temp file with `zotero_mcp_` prefix |

Returns: `{"path": "/tmp/zotero_mcp_graph_abc123.html", "nodes": 150, "edges": 340, "view": "citations"}`

### Views

- **`citations`** — Paper nodes + citation edges, colored by topic cluster
- **`authors`** — Author nodes + co-authorship edges, sized by paper count, colored by dominant topic
- **`full`** — Both layers combined. Capped at top 200 nodes by PageRank with a warning if truncated

### Visual encoding

| Property | Papers | Authors |
|----------|--------|---------|
| Color | Topic cluster (consistent palette) | Dominant topic of their papers |
| Size | PageRank score | Paper count |
| Shape | Circle | Diamond (in `full` view) |

- Edge thickness: citation count (paper↔paper) or shared-paper weight (author↔author)
- Click: info panel with title/name, DOI, topics, co-authors
- Interactive: drag-to-move, scroll-to-zoom, click-to-pin

### Claude Preview integration

The tool is Preview-agnostic — it writes an HTML file and returns the path. The LLM uses `preview_start` to render it inline during conversation. This keeps the MCP tool decoupled from the display mechanism.

### Implementation: `graph_renderer.py`

New module containing:
- HTML/CSS/JS template as a string constant
- `render_citations_view(papers, edges, topics)` → HTML string
- `render_authors_view(authors, edges, topics)` → HTML string
- `render_full_view(papers, authors, all_edges, topics)` → HTML string

Data is JSON-serialized and injected into a `<script>` tag. No Jinja or external template engine — f-string substitution into a self-contained HTML document.

---

## Architecture

### Module dependency graph

```
server.py
├── graph_store.py      (SQLite: papers, citations, authors, paper_authors, paper_topics, sync_state)
├── knowledge_graph.py  (NetworkX: unified paper+author DiGraph, cluster labeling)
├── graph_renderer.py   (NEW: D3.js HTML template generation)
├── openalex_client.py  (bulk_get_works now also returns topics + structured authorships)
└── semantic_scholar_client.py (unchanged)
```

### Data flow

```
OpenAlex API
  └─ works[] with topics[], authorships[]
       │
       ├─ papers table (existing)
       ├─ paper_topics table (NEW)
       ├─ authors table (NEW)
       ├─ paper_authors table (NEW)
       └─ citations table (existing)
              │
         GraphStore
              │
         KnowledgeGraph (NetworkX)
           ├─ paper nodes + citation edges (existing)
           ├─ author nodes + authorship edges (NEW)
           ├─ co-authorship edges (derived, NEW)
           └─ cluster labels from paper_topics (NEW)
              │
         graph_renderer.py (NEW)
              │
         D3.js HTML file → Claude Preview
```

### Performance considerations

- Author and topic data is fetched in the same OpenAlex API call as paper data (no additional round-trips)
- Co-authorship edges are derived during `build_from_store()`, not stored (avoids O(n²) storage)
- `export_knowledge_graph` reads from the cached `_kg_cache`, does not rebuild
- `full` view caps at 200 nodes to keep the HTML performant in-browser
- New tables use appropriate indexes (`paper_authors.openalex_author_id`, existing `papers.doi`)

---

## Testing Strategy

- **GraphStore**: Unit tests for new tables — upsert/get authors, paper_authors, paper_topics
- **KnowledgeGraph**: Test cluster labeling with known topic distributions, test author subgraph queries
- **graph_renderer**: Test that each view produces valid HTML with correct node/edge counts in the embedded JSON
- **OpenAlex integration**: Mock tests verifying topics and structured authorships are extracted from bulk responses
- **Server tools**: End-to-end mock tests for `export_knowledge_graph`, `query_authors`, `get_author_network`

---

## Version & Breaking Changes

- **Version:** 0.6.0
- **No breaking changes** — all new features are additive
- **Optional extra:** Still `[graph]` — no new dependencies beyond networkx (D3.js is embedded in the HTML template)
- **GraphStore migration:** New tables use `CREATE TABLE IF NOT EXISTS` — existing v0.5.0 databases are upgraded transparently on first access
