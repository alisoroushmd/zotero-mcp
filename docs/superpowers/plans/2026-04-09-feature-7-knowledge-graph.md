# Knowledge Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add library-wide knowledge graph capabilities: batch-fetch citation data for all library DOIs via OpenAlex, build a persistent citation network with NetworkX, compute graph analytics (PageRank, clusters, bridge papers), and discover related papers via Semantic Scholar recommendations. 4 new tools, 28 to 32 total.

**Architecture:** New `KnowledgeGraph` class in `knowledge_graph.py` manages graph construction, persistence (SQLite), and analysis (NetworkX). New `SemanticScholarClient` in `semantic_scholar_client.py` for paper recommendations. Both gated behind `[graph]` optional extra with graceful degradation. Extends existing `OpenAlexClient` with bulk-query support.

**Tech Stack:** networkx (graph analysis), sqlite3 (persistence, stdlib), semanticscholar (recommendations API). Optional: pyalex (ergonomic OpenAlex queries).

**Prerequisite:** Existing `openalex_client.py` and `web_client.py` provide the foundation.

**Important:** OpenAlex now requires a free API key as of Feb 2026. The existing polite-pool email approach must be updated. Register at https://openalex.org/users/me and set `OPENALEX_API_KEY` env var.

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Graph library | NetworkX | Pure Python, BSD license, no C extensions, rich algorithm set. Fine for typical Zotero libraries (100s-10,000s papers). |
| Persistence | SQLite (stdlib) | Zero infrastructure, survives between MCP sessions, incremental updates. No need for Neo4j or external DB. |
| Recommendations | Semantic Scholar API | Free, pre-computed SPECTER2 embeddings, recommendations endpoint replicates Connected Papers / ResearchRabbit functionality. |
| Bulk data source | OpenAlex | Already integrated. Batch filter by DOI list (up to 50 per query via pipe separator). CC0 data. |
| Optional extra | `[graph]` | Keeps base install lightweight. NetworkX + semanticscholar are the only new deps. |
| LLM-based extraction | Deferred | nano-graphrag / LightRAG add cost and complexity. Citation structure from APIs is sufficient for v1. Can layer on later. |

---

## New Tools Summary

| Tool | Description | Mode |
|------|-------------|------|
| `build_knowledge_graph` | Batch-fetch citation data for all library DOIs, build and persist the graph | cloud_crud |
| `query_knowledge_graph` | Find influential papers, clusters, paths, bridge papers | any_read |
| `find_related_papers` | Semantic Scholar recommendations from library seeds | cloud_crud |
| `sync_knowledge_graph` | Incremental update for new/changed items since last build | cloud_crud |

---

### Task 1: Add `[graph]` optional extra to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the graph extra**

In `pyproject.toml`, add to `[project.optional-dependencies]`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "respx>=0.22.0",
]
graph = [
    "networkx>=3.2",
    "semanticscholar>=0.8",
]
```

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add [graph] optional extra for knowledge graph dependencies"
```

---

### Task 2: Create SQLite persistence layer

**Files:**
- Create: `src/zotero_mcp/graph_store.py`
- Test: `tests/test_graph_store.py` (new)

- [ ] **Step 1: Write failing tests for GraphStore**

Create `tests/test_graph_store.py`:

```python
"""Tests for GraphStore — SQLite persistence for the knowledge graph."""

import os
import tempfile
import pytest

from zotero_mcp.graph_store import GraphStore


@pytest.fixture
def tmp_db():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


def test_upsert_and_get_paper(tmp_db):
    """Papers can be stored and retrieved by DOI."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1234/test",
        zotero_key="ABC123",
        title="Test Paper",
        year=2024,
        authors="Smith J; Lee A",
        openalex_id="W12345",
    )
    paper = store.get_paper("10.1234/test")
    assert paper is not None
    assert paper["zotero_key"] == "ABC123"
    assert paper["title"] == "Test Paper"


def test_upsert_citation(tmp_db):
    """Citation edges can be stored and queried."""
    store = GraphStore(tmp_db)
    store.upsert_paper(doi="10.1/a", zotero_key="A", title="Paper A",
                       year=2020, authors="X", openalex_id="W1")
    store.upsert_paper(doi="10.1/b", zotero_key="B", title="Paper B",
                       year=2022, authors="Y", openalex_id="W2")
    store.upsert_citation(citing_doi="10.1/b", cited_doi="10.1/a")
    refs = store.get_references("10.1/b")
    assert len(refs) == 1
    assert refs[0]["doi"] == "10.1/a"


def test_get_citing_papers(tmp_db):
    """Can retrieve papers that cite a given DOI."""
    store = GraphStore(tmp_db)
    store.upsert_paper(doi="10.1/a", zotero_key="A", title="A",
                       year=2020, authors="X", openalex_id="W1")
    store.upsert_paper(doi="10.1/b", zotero_key="B", title="B",
                       year=2022, authors="Y", openalex_id="W2")
    store.upsert_citation(citing_doi="10.1/b", cited_doi="10.1/a")
    citers = store.get_citing_papers("10.1/a")
    assert len(citers) == 1
    assert citers[0]["doi"] == "10.1/b"


def test_get_all_papers(tmp_db):
    """Can retrieve all papers for graph construction."""
    store = GraphStore(tmp_db)
    store.upsert_paper(doi="10.1/a", zotero_key="A", title="A",
                       year=2020, authors="X", openalex_id="W1")
    store.upsert_paper(doi="10.1/b", zotero_key="B", title="B",
                       year=2022, authors="Y", openalex_id="W2")
    papers = store.get_all_papers()
    assert len(papers) == 2


def test_get_all_citations(tmp_db):
    """Can retrieve all edges for graph construction."""
    store = GraphStore(tmp_db)
    store.upsert_paper(doi="10.1/a", zotero_key="A", title="A",
                       year=2020, authors="X", openalex_id="W1")
    store.upsert_paper(doi="10.1/b", zotero_key="B", title="B",
                       year=2022, authors="Y", openalex_id="W2")
    store.upsert_citation(citing_doi="10.1/b", cited_doi="10.1/a")
    edges = store.get_all_citations()
    assert len(edges) == 1
    assert edges[0] == ("10.1/b", "10.1/a")


def test_get_sync_state(tmp_db):
    """Sync state tracks last build time."""
    store = GraphStore(tmp_db)
    assert store.get_last_sync() is None
    store.set_last_sync("2026-04-09T12:00:00Z")
    assert store.get_last_sync() == "2026-04-09T12:00:00Z"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_graph_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zotero_mcp.graph_store'`

- [ ] **Step 3: Implement GraphStore**

Create `src/zotero_mcp/graph_store.py`:

```python
"""SQLite persistence for the Zotero knowledge graph.

Stores papers (nodes) and citations (edges) locally so the graph
survives between MCP sessions and supports incremental updates.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


_DEFAULT_DB_PATH = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "zotero-mcp",
    "knowledge_graph.db",
)


class GraphStore:
    """SQLite-backed storage for knowledge graph nodes and edges."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or os.environ.get(
            "ZOTERO_MCP_GRAPH_DB", _DEFAULT_DB_PATH
        )
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS papers (
                doi TEXT PRIMARY KEY,
                zotero_key TEXT,
                title TEXT,
                year INTEGER,
                authors TEXT,
                openalex_id TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS citations (
                citing_doi TEXT NOT NULL,
                cited_doi TEXT NOT NULL,
                PRIMARY KEY (citing_doi, cited_doi)
            );
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_citations_cited
                ON citations(cited_doi);
            CREATE INDEX IF NOT EXISTS idx_papers_zotero_key
                ON papers(zotero_key);
        """)
        self._conn.commit()

    def upsert_paper(self, doi: str, zotero_key: str, title: str,
                     year: int, authors: str, openalex_id: str) -> None:
        self._conn.execute(
            """INSERT INTO papers (doi, zotero_key, title, year, authors, openalex_id)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(doi) DO UPDATE SET
                   zotero_key=excluded.zotero_key,
                   title=excluded.title,
                   year=excluded.year,
                   authors=excluded.authors,
                   openalex_id=excluded.openalex_id,
                   updated_at=CURRENT_TIMESTAMP""",
            (doi, zotero_key, title, year, authors, openalex_id),
        )
        self._conn.commit()

    def upsert_citation(self, citing_doi: str, cited_doi: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO citations (citing_doi, cited_doi) VALUES (?, ?)",
            (citing_doi, cited_doi),
        )
        self._conn.commit()

    def get_paper(self, doi: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM papers WHERE doi = ?", (doi,)
        ).fetchone()
        return dict(row) if row else None

    def get_references(self, doi: str) -> list[dict]:
        rows = self._conn.execute(
            """SELECT p.* FROM citations c
               JOIN papers p ON p.doi = c.cited_doi
               WHERE c.citing_doi = ?""",
            (doi,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_citing_papers(self, doi: str) -> list[dict]:
        rows = self._conn.execute(
            """SELECT p.* FROM citations c
               JOIN papers p ON p.doi = c.citing_doi
               WHERE c.cited_doi = ?""",
            (doi,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_papers(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM papers").fetchall()
        return [dict(r) for r in rows]

    def get_all_citations(self) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            "SELECT citing_doi, cited_doi FROM citations"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def get_last_sync(self) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM sync_state WHERE key = 'last_sync'"
        ).fetchone()
        return row[0] if row else None

    def set_last_sync(self, timestamp: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO sync_state (key, value) VALUES ('last_sync', ?)",
            (timestamp,),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_graph_store.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/zotero_mcp/graph_store.py tests/test_graph_store.py
git commit -m "feat: add GraphStore — SQLite persistence for knowledge graph"
```

---

### Task 3: Create KnowledgeGraph class with NetworkX analysis

**Files:**
- Create: `src/zotero_mcp/knowledge_graph.py`
- Test: `tests/test_knowledge_graph.py` (new)

- [ ] **Step 1: Write failing tests for KnowledgeGraph**

Create `tests/test_knowledge_graph.py` with tests for:
- `build_from_store(store)` — constructs a NetworkX DiGraph from GraphStore data
- `get_influential_papers(top_n=10)` — returns papers ranked by PageRank
- `get_clusters()` — returns community clusters via greedy modularity
- `get_bridge_papers(top_n=10)` — returns papers with highest betweenness centrality
- `get_path(doi_a, doi_b)` — returns shortest citation path between two papers
- `get_neighborhood(doi, depth=1)` — returns papers within N hops
- `get_stats()` — returns graph summary (node count, edge count, density, components)

Use a small test graph (5-6 nodes with known structure) so expected results are deterministic.

- [ ] **Step 2: Implement KnowledgeGraph**

Create `src/zotero_mcp/knowledge_graph.py`:

```python
"""Knowledge graph analysis using NetworkX over Zotero citation data."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

if TYPE_CHECKING:
    from zotero_mcp.graph_store import GraphStore

logger = logging.getLogger(__name__)


class KnowledgeGraph:
    """Citation network analysis for a Zotero library.

    Builds a NetworkX DiGraph from the GraphStore and provides
    graph analytics: PageRank, community detection, betweenness
    centrality, shortest paths, and neighborhood queries.
    """

    def __init__(self) -> None:
        if not HAS_NETWORKX:
            raise ImportError(
                "Knowledge graph requires networkx. "
                "Install with: pip install zotero-mcp[graph]"
            )
        self._graph: nx.DiGraph = nx.DiGraph()
        self._paper_data: dict[str, dict] = {}

    def build_from_store(self, store: GraphStore) -> dict:
        """Build the graph from persisted data."""
        self._graph.clear()
        self._paper_data.clear()

        papers = store.get_all_papers()
        for p in papers:
            doi = p["doi"]
            self._graph.add_node(doi)
            self._paper_data[doi] = p

        for citing, cited in store.get_all_citations():
            if citing in self._graph and cited in self._graph:
                self._graph.add_edge(citing, cited)

        return self.get_stats()

    def get_stats(self) -> dict:
        """Return graph summary statistics."""
        g = self._graph
        return {
            "nodes": g.number_of_nodes(),
            "edges": g.number_of_edges(),
            "density": round(nx.density(g), 4) if g.number_of_nodes() > 1 else 0,
            "components": nx.number_weakly_connected_components(g),
        }

    def get_influential_papers(self, top_n: int = 10) -> list[dict]:
        """Return papers ranked by PageRank (most influential first)."""
        if not self._graph.nodes:
            return []
        pr = nx.pagerank(self._graph)
        ranked = sorted(pr.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [
            {**self._paper_data.get(doi, {"doi": doi}), "pagerank": round(score, 6)}
            for doi, score in ranked
        ]

    def get_clusters(self) -> list[dict]:
        """Detect research clusters via greedy modularity on undirected projection."""
        if self._graph.number_of_nodes() < 2:
            return []
        undirected = self._graph.to_undirected()
        try:
            from networkx.algorithms.community import greedy_modularity_communities
            communities = greedy_modularity_communities(undirected)
        except Exception:
            return []

        clusters = []
        for i, community in enumerate(communities):
            papers = [self._paper_data.get(doi, {"doi": doi}) for doi in community]
            clusters.append({
                "cluster_id": i,
                "size": len(community),
                "papers": papers,
            })
        return sorted(clusters, key=lambda c: c["size"], reverse=True)

    def get_bridge_papers(self, top_n: int = 10) -> list[dict]:
        """Return papers with highest betweenness centrality (bridge papers)."""
        if self._graph.number_of_nodes() < 3:
            return []
        bc = nx.betweenness_centrality(self._graph)
        ranked = sorted(bc.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [
            {**self._paper_data.get(doi, {"doi": doi}), "betweenness": round(score, 6)}
            for doi, score in ranked
            if score > 0
        ]

    def get_path(self, doi_a: str, doi_b: str) -> list[dict]:
        """Find shortest citation path between two papers."""
        undirected = self._graph.to_undirected()
        try:
            path = nx.shortest_path(undirected, doi_a, doi_b)
            return [self._paper_data.get(doi, {"doi": doi}) for doi in path]
        except (nx.NodeNotFound, nx.NetworkXNoPath):
            return []

    def get_neighborhood(self, doi: str, depth: int = 1) -> dict:
        """Get papers within N citation hops of a given paper."""
        if doi not in self._graph:
            return {"center": doi, "papers": [], "edges": []}
        undirected = self._graph.to_undirected()
        neighbors = nx.single_source_shortest_path_length(undirected, doi, cutoff=depth)
        papers = [
            {**self._paper_data.get(d, {"doi": d}), "distance": dist}
            for d, dist in neighbors.items()
        ]
        subgraph = self._graph.subgraph(neighbors.keys())
        edges = [{"from": u, "to": v} for u, v in subgraph.edges()]
        return {"center": doi, "papers": papers, "edges": edges}
```

- [ ] **Step 3: Run tests to verify they pass**
- [ ] **Step 4: Commit**

```bash
git add src/zotero_mcp/knowledge_graph.py tests/test_knowledge_graph.py
git commit -m "feat: add KnowledgeGraph — NetworkX analysis for citation networks"
```


---

### Task 4: Create SemanticScholarClient

**Files:**
- Create: `src/zotero_mcp/semantic_scholar_client.py`
- Test: `tests/test_semantic_scholar.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_semantic_scholar.py` with tests for:
- `get_recommendations(seed_dois, limit=10)` — returns recommended papers based on seed DOIs
- `get_paper_embedding(doi)` — returns SPECTER2 embedding vector (if available)
- `search_similar(doi, limit=10)` — finds papers similar to a given DOI
- Handles 404 (unknown DOI) gracefully
- Handles rate limiting (429) with retry

- [ ] **Step 2: Implement SemanticScholarClient**

Create `src/zotero_mcp/semantic_scholar_client.py`:

```python
"""Semantic Scholar API client — paper recommendations and similarity."""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

S2_BASE = "https://api.semanticscholar.org"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class SemanticScholarClient:
    """Client for Semantic Scholar API.

    Provides paper recommendations (similar to Connected Papers /
    ResearchRabbit) and SPECTER2 embedding access for similarity search.
    """

    def __init__(self, api_key: str | None = None) -> None:
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key
        self._client = httpx.Client(
            base_url=S2_BASE,
            headers=headers,
            timeout=TIMEOUT,
        )

    def get_recommendations(
        self, seed_dois: list[str], limit: int = 10
    ) -> list[dict]:
        """Get paper recommendations based on seed papers.

        Uses Semantic Scholar's recommendations endpoint which finds
        papers related to the given seed set (similar to Connected
        Papers / ResearchRabbit).

        Args:
            seed_dois: List of DOIs to use as positive seeds.
            limit: Max recommendations to return.

        Returns:
            List of recommended paper dicts with title, doi, year, authors.
        """
        # Convert DOIs to Semantic Scholar paper IDs
        paper_ids = [{"doi": doi} for doi in seed_dois[:50]]

        try:
            resp = self._client.post(
                "/recommendations/v1/papers/",
                json={"positivePaperIds": paper_ids, "negativePaperIds": []},
                params={"limit": min(limit, 50), "fields": "title,year,authors,externalIds"},
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                time.sleep(min(retry_after, 10))
                resp = self._client.post(
                    "/recommendations/v1/papers/",
                    json={"positivePaperIds": paper_ids, "negativePaperIds": []},
                    params={"limit": min(limit, 50), "fields": "title,year,authors,externalIds"},
                )
            resp.raise_for_status()
            papers = resp.json().get("recommendedPapers", [])
            return [self._format_paper(p) for p in papers]
        except Exception as exc:
            logger.warning("Semantic Scholar recommendations failed: %s", exc)
            return []

    def search_similar(self, doi: str, limit: int = 10) -> list[dict]:
        """Find papers similar to a given DOI.

        Args:
            doi: DOI of the seed paper.
            limit: Max results.

        Returns:
            List of similar paper dicts.
        """
        return self.get_recommendations([doi], limit=limit)

    @staticmethod
    def _format_paper(paper: dict) -> dict:
        """Format a Semantic Scholar paper for display."""
        authors = paper.get("authors", [])
        author_str = "; ".join(a.get("name", "") for a in authors[:3])
        if len(authors) > 3:
            author_str += " et al."
        ext_ids = paper.get("externalIds", {})
        return {
            "title": paper.get("title", ""),
            "doi": ext_ids.get("DOI", ""),
            "year": paper.get("year"),
            "authors": author_str,
            "s2_id": paper.get("paperId", ""),
        }
```

- [ ] **Step 3: Run tests to verify they pass**
- [ ] **Step 4: Commit**

```bash
git add src/zotero_mcp/semantic_scholar_client.py tests/test_semantic_scholar.py
git commit -m "feat: add SemanticScholarClient — paper recommendations via S2 API"
```


---

### Task 5: Add OpenAlex bulk-query support

**Files:**
- Modify: `src/zotero_mcp/openalex_client.py`
- Test: `tests/test_openalex.py` (modify)

- [ ] **Step 1: Write failing tests for bulk_get_works**

Add to `tests/test_openalex.py`:

```python
@respx.mock
def test_bulk_get_works_batches_dois():
    """bulk_get_works fetches metadata for multiple DOIs in batches."""
    respx.get(
        f"{OPENALEX_BASE}/works",
        params__contains={"filter": "doi:10.1/a|10.1/b"},
    ).mock(
        return_value=httpx.Response(200, json={
            "results": [
                {"id": "W1", "doi": "https://doi.org/10.1/a", "title": "Paper A",
                 "publication_year": 2020, "authorships": [],
                 "referenced_works": ["https://openalex.org/W99"]},
                {"id": "W2", "doi": "https://doi.org/10.1/b", "title": "Paper B",
                 "publication_year": 2022, "authorships": [],
                 "referenced_works": []},
            ]
        })
    )
    client = OpenAlexClient()
    results = client.bulk_get_works(["10.1/a", "10.1/b"])
    assert len(results) == 2
    assert results[0]["doi"] == "https://doi.org/10.1/a"
```

- [ ] **Step 2: Implement `bulk_get_works`**

Add to `OpenAlexClient`:

```python
def bulk_get_works(self, dois: list[str], batch_size: int = 50) -> list[dict]:
    """Batch-fetch work metadata for multiple DOIs.

    OpenAlex supports filtering by pipe-separated DOI list
    (up to ~50 per query to stay within URL length limits).

    Args:
        dois: List of DOI strings.
        batch_size: Max DOIs per API request.

    Returns:
        List of raw OpenAlex work dicts.
    """
    all_works: list[dict] = []
    for i in range(0, len(dois), batch_size):
        batch = dois[i:i + batch_size]
        doi_filter = "|".join(f"doi:{d}" for d in batch)
        try:
            resp = self._client.get(
                "/works",
                params={"filter": doi_filter, "per_page": batch_size},
            )
            resp.raise_for_status()
            all_works.extend(resp.json().get("results", []))
        except Exception as exc:
            logger.warning("OpenAlex bulk query failed for batch %d: %s", i, exc)
    return all_works
```

- [ ] **Step 3: Run tests, commit**

```bash
git add src/zotero_mcp/openalex_client.py tests/test_openalex.py
git commit -m "feat: add bulk_get_works to OpenAlexClient for batch DOI queries"
```


---

### Task 6: Add server tools — `build_knowledge_graph`, `query_knowledge_graph`, `find_related_papers`, `sync_knowledge_graph`

**Files:**
- Modify: `src/zotero_mcp/server.py`
- Modify: `src/zotero_mcp/capabilities.py`
- Test: `tests/test_knowledge_graph_tools.py` (new)

- [ ] **Step 1: Write failing tests for all four tools**

Create `tests/test_knowledge_graph_tools.py` with tests covering:

**`build_knowledge_graph`:**
- Fetches all library items with DOIs
- Batch-queries OpenAlex for citation data
- Stores papers and citations in GraphStore
- Builds NetworkX graph and returns stats
- Returns `{"nodes": N, "edges": M, "clusters": K, ...}`

**`query_knowledge_graph(query_type, ...)`:**
- `query_type="influential"` returns PageRank-ranked papers
- `query_type="clusters"` returns community groupings
- `query_type="bridges"` returns high-betweenness papers
- `query_type="path", doi_a="...", doi_b="..."` returns shortest path
- `query_type="neighborhood", doi="...", depth=2` returns N-hop neighborhood
- `query_type="stats"` returns graph summary
- Returns error if graph not yet built

**`find_related_papers(item_keys, limit=10)`:**
- Resolves Zotero item keys to DOIs
- Calls Semantic Scholar recommendations
- Flags `in_library` for each recommendation
- Returns list of recommended papers

**`sync_knowledge_graph`:**
- Fetches only items modified since last sync (using Zotero API `since` parameter)
- Updates GraphStore incrementally
- Rebuilds NetworkX graph
- Returns stats with `new_papers` and `new_citations` counts

- [ ] **Step 2: Implement the four tools**

Add to `src/zotero_mcp/server.py`:

```python
@mcp.tool(
    description=(
        "Build a knowledge graph from your entire Zotero library. "
        "Fetches citation data for all items with DOIs via OpenAlex, "
        "stores in a local database, and computes graph analytics. "
        "Run this once to initialize, then use sync_knowledge_graph for updates. "
        "Returns graph statistics including node/edge counts and cluster count."
    ),
)
@_handle_tool_errors
def build_knowledge_graph() -> str:
    """Build the full knowledge graph from library items."""
    from zotero_mcp.graph_store import GraphStore
    from zotero_mcp.knowledge_graph import KnowledgeGraph
    from zotero_mcp.openalex_client import OpenAlexClient

    web = _get_web()
    openalex = OpenAlexClient()
    store = GraphStore()
    kg = KnowledgeGraph()

    # Step 1: Fetch all library items with DOIs
    items = web.search_items("", limit=100)
    dois = [(item["key"], item.get("DOI", "")) for item in items if item.get("DOI")]

    if not dois:
        return json.dumps({"error": "No items with DOIs found in library"})

    # Step 2: Batch-fetch from OpenAlex
    doi_list = [doi for _, doi in dois]
    key_by_doi = {doi: key for key, doi in dois}
    works = openalex.bulk_get_works(doi_list)

    # Step 3: Store papers and citations
    papers_added = 0
    citations_added = 0
    for work in works:
        doi = (work.get("doi") or "").replace("https://doi.org/", "")
        if not doi:
            continue
        authorships = work.get("authorships", [])
        authors = "; ".join(
            a.get("author", {}).get("display_name", "") for a in authorships[:3]
        )
        store.upsert_paper(
            doi=doi,
            zotero_key=key_by_doi.get(doi, ""),
            title=work.get("title", ""),
            year=work.get("publication_year", 0),
            authors=authors,
            openalex_id=work.get("id", ""),
        )
        papers_added += 1

        for ref_url in work.get("referenced_works", []):
            ref_id = ref_url.split("/")[-1]
            # We store the reference edge even if the cited paper
            # isn't in our library — it enriches the graph
            store.upsert_citation(citing_doi=doi, cited_doi=ref_id)
            citations_added += 1

    # Step 4: Build graph and compute stats
    from datetime import datetime, timezone
    store.set_last_sync(datetime.now(timezone.utc).isoformat())
    stats = kg.build_from_store(store)
    stats["papers_indexed"] = papers_added
    stats["citations_indexed"] = citations_added

    return json.dumps(stats, ensure_ascii=False)


@mcp.tool(
    description=(
        "Query the knowledge graph for insights about your library. "
        "Query types: 'influential' (PageRank-ranked papers), "
        "'clusters' (research topic groupings), "
        "'bridges' (papers connecting different clusters), "
        "'path' (shortest citation path between two DOIs — requires doi_a and doi_b), "
        "'neighborhood' (papers within N hops of a DOI — requires doi and optional depth), "
        "'stats' (graph summary). "
        "Requires build_knowledge_graph to be run first."
    ),
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def query_knowledge_graph(
    query_type: str,
    doi: str = "",
    doi_a: str = "",
    doi_b: str = "",
    depth: int = 1,
    limit: int = 10,
) -> str:
    """Query the knowledge graph."""
    from zotero_mcp.graph_store import GraphStore
    from zotero_mcp.knowledge_graph import KnowledgeGraph

    store = GraphStore()
    if store.get_last_sync() is None:
        raise RuntimeError(
            "Knowledge graph not yet built. Run build_knowledge_graph first."
        )

    kg = KnowledgeGraph()
    kg.build_from_store(store)

    if query_type == "influential":
        result = kg.get_influential_papers(top_n=limit)
    elif query_type == "clusters":
        result = kg.get_clusters()
    elif query_type == "bridges":
        result = kg.get_bridge_papers(top_n=limit)
    elif query_type == "path":
        if not doi_a or not doi_b:
            raise ValueError("path query requires doi_a and doi_b")
        result = kg.get_path(doi_a, doi_b)
    elif query_type == "neighborhood":
        if not doi:
            raise ValueError("neighborhood query requires doi")
        result = kg.get_neighborhood(doi, depth=depth)
    elif query_type == "stats":
        result = kg.get_stats()
    else:
        raise ValueError(
            f"Unknown query_type: {query_type!r}. "
            "Must be: influential, clusters, bridges, path, neighborhood, stats"
        )

    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description=(
        "Find papers related to items in your Zotero library using "
        "Semantic Scholar recommendations. Similar to Connected Papers "
        "or ResearchRabbit. Provide one or more item keys as seeds — "
        "the more seeds, the better the recommendations. Each result "
        "is flagged with in_library (true/false)."
    ),
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def find_related_papers(
    item_keys: str | list[str],
    limit: str | int = 10,
) -> str:
    """Get paper recommendations from Semantic Scholar."""
    from zotero_mcp.semantic_scholar_client import SemanticScholarClient

    keys = _parse_list_param(item_keys) or []
    if not keys:
        raise ValueError("item_keys must not be empty")
    for k in keys:
        _validate_key(k, "item_key")

    limit_int = _clamp_limit(limit, lo=1, hi=50)
    web = _get_web()

    # Resolve keys to DOIs
    dois: list[str] = []
    for key in keys:
        item = web.get_item(key.strip())
        if isinstance(item, dict) and item.get("DOI"):
            dois.append(item["DOI"])

    if not dois:
        return json.dumps({
            "error": "None of the provided items have DOIs",
            "item_keys": keys,
        })

    s2 = SemanticScholarClient()
    recommendations = s2.get_recommendations(dois, limit=limit_int)

    # Flag which recommendations are already in library
    for rec in recommendations:
        rec_doi = rec.get("doi", "")
        if rec_doi:
            existing = web._check_duplicate_doi(rec_doi)
            if existing:
                rec["in_library"] = True
                rec["zotero_key"] = existing["key"]
            else:
                rec["in_library"] = False
        else:
            rec["in_library"] = False

    return json.dumps({
        "seed_count": len(dois),
        "recommendations": recommendations,
    }, ensure_ascii=False)


@mcp.tool(
    description=(
        "Incrementally update the knowledge graph with items added or "
        "modified since the last build. Faster than a full rebuild. "
        "Run build_knowledge_graph first, then use this for updates."
    ),
)
@_handle_tool_errors
def sync_knowledge_graph() -> str:
    """Incrementally update the knowledge graph."""
    from datetime import datetime, timezone
    from zotero_mcp.graph_store import GraphStore
    from zotero_mcp.knowledge_graph import KnowledgeGraph
    from zotero_mcp.openalex_client import OpenAlexClient

    store = GraphStore()
    last_sync = store.get_last_sync()
    if last_sync is None:
        raise RuntimeError(
            "Knowledge graph not yet built. Run build_knowledge_graph first."
        )

    web = _get_web()
    openalex = OpenAlexClient()

    # Fetch items modified since last sync
    # Zotero API supports ?since=<version> but we use date-based
    items = web.search_items("", limit=100)
    dois = [(item["key"], item.get("DOI", "")) for item in items if item.get("DOI")]
    doi_list = [doi for _, doi in dois]
    key_by_doi = {doi: key for key, doi in dois}

    # Find new DOIs not in store
    new_dois = [d for d in doi_list if store.get_paper(d) is None]

    if not new_dois:
        kg = KnowledgeGraph()
        stats = kg.build_from_store(store)
        stats["new_papers"] = 0
        stats["new_citations"] = 0
        return json.dumps(stats, ensure_ascii=False)

    works = openalex.bulk_get_works(new_dois)

    new_papers = 0
    new_citations = 0
    for work in works:
        doi = (work.get("doi") or "").replace("https://doi.org/", "")
        if not doi:
            continue
        authorships = work.get("authorships", [])
        authors = "; ".join(
            a.get("author", {}).get("display_name", "") for a in authorships[:3]
        )
        store.upsert_paper(
            doi=doi,
            zotero_key=key_by_doi.get(doi, ""),
            title=work.get("title", ""),
            year=work.get("publication_year", 0),
            authors=authors,
            openalex_id=work.get("id", ""),
        )
        new_papers += 1
        for ref_url in work.get("referenced_works", []):
            ref_id = ref_url.split("/")[-1]
            store.upsert_citation(citing_doi=doi, cited_doi=ref_id)
            new_citations += 1

    store.set_last_sync(datetime.now(timezone.utc).isoformat())
    kg = KnowledgeGraph()
    stats = kg.build_from_store(store)
    stats["new_papers"] = new_papers
    stats["new_citations"] = new_citations

    return json.dumps(stats, ensure_ascii=False)
```

- [ ] **Step 3: Update capabilities.py TOOL_MODES**

Add to `TOOL_MODES` dict:

```python
"build_knowledge_graph": ["cloud_crud"],
"query_knowledge_graph": ["any_read"],
"find_related_papers": ["cloud_crud"],
"sync_knowledge_graph": ["cloud_crud"],
```

- [ ] **Step 4: Update test_server.py**

Add the four new tool names to the `expected` set and update the tool count from 28 to 32.

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/zotero_mcp/server.py src/zotero_mcp/capabilities.py tests/
git commit -m "feat: add knowledge graph tools — build, query, find_related, sync"
```


---

### Task 7: Update OpenAlex authentication (breaking change)

**Files:**
- Modify: `src/zotero_mcp/openalex_client.py`
- Modify: `src/zotero_mcp/capabilities.py`

As of Feb 2026, OpenAlex requires a free API key. The current polite-pool email approach will stop working.

- [ ] **Step 1: Update OpenAlexClient to use API key**

```python
import os

OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY", "")

class OpenAlexClient:
    def __init__(self, api_key: str = OPENALEX_API_KEY, email: str = POLITE_EMAIL) -> None:
        headers = {"User-Agent": f"zotero-mcp/1.0 (mailto:{email})"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=OPENALEX_BASE,
            headers=headers,
            timeout=TIMEOUT,
        )
```

- [ ] **Step 2: Add OPENALEX_API_KEY to capabilities check**

In `capabilities.py`, add a warning if `OPENALEX_API_KEY` is not set:

```python
openalex_key = os.environ.get("OPENALEX_API_KEY", "")
if not openalex_key:
    # Not blocking — but warn about degraded citation graph / retraction checks
    logger.warning("OPENALEX_API_KEY not set — citation graph and retraction checks may fail")
```

- [ ] **Step 3: Update manifest.json with new env var**
- [ ] **Step 4: Run full test suite, commit**

```bash
git add src/zotero_mcp/openalex_client.py src/zotero_mcp/capabilities.py manifest.json
git commit -m "feat: update OpenAlex auth to use API key (required since Feb 2026)"
```

---

### Task 8: Documentation and final integration

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update README with knowledge graph section**

Add a new section covering:
- Installation: `pip install zotero-mcp[graph]`
- New env vars: `OPENALEX_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY` (optional)
- Tool usage examples for build, query, find_related, sync
- Graph query types with example outputs

- [ ] **Step 2: Update CHANGELOG**

Add v0.5.0 entry with all new tools and breaking changes (OpenAlex API key).

- [ ] **Step 3: Bump version to 0.5.0**

Update `pyproject.toml` version and `__init__.py` version.

- [ ] **Step 4: Run full test suite, commit, tag**

```bash
python -m pytest tests/ -v
git add .
git commit -m "docs: update README and CHANGELOG for v0.5.0 — knowledge graph"
git tag v0.5.0
```

---

## Architecture Diagram

```
                    Zotero Library
                    (Web API / Local)
                          |
                    search_items()
                    get all DOIs
                          |
                    +-----v-----+
                    | OpenAlex  |  bulk_get_works()
                    | API       |  referenced_works, cited_by
                    +-----------+
                          |
                    +-----v-----+
                    | GraphStore|  SQLite: papers + citations
                    | (SQLite)  |  ~/.local/share/zotero-mcp/
                    +-----------+
                          |
                    +-----v--------+
                    | KnowledgeGraph|  NetworkX DiGraph
                    | (NetworkX)    |  PageRank, clusters,
                    |               |  betweenness, paths
                    +---------------+
                          |
          +---------------+---------------+
          |                               |
  build/query/sync              find_related_papers
  knowledge_graph                         |
                                  +-------v--------+
                                  | Semantic Scholar|
                                  | Recommendations |
                                  | API             |
                                  +-----------------+
```

## Future Extensions (v0.6.0+)

1. **Author co-citation network** — Add author nodes and co-authorship edges from OpenAlex authorships data
2. **Topic clustering** — Use OpenAlex topic/concept classifications to label clusters
3. **nano-graphrag integration** — Extract entities from abstracts for semantic knowledge graph layer
4. **Graph visualization** — Export to D3.js-compatible JSON or Graphviz DOT for rendering
5. **Cross-library analysis** — Support Zotero group libraries for collaborative research mapping
