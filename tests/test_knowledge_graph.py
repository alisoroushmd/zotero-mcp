"""Tests for KnowledgeGraph — NetworkX analysis over citation data."""

import os
import tempfile

import pytest

from zotero_mcp.graph_store import GraphStore
from zotero_mcp.knowledge_graph import KnowledgeGraph


@pytest.fixture
def populated_store():
    """Create a GraphStore with a small test graph.

    Graph structure (directed, A cites B means edge A->B):
        A -> B -> D
        A -> C -> D
        C -> E
        E -> D

    So D is the most "cited" (3 incoming), C is a bridge between A/E cluster and D.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = GraphStore(path)

    for doi, key, title, year in [
        ("10.1/a", "A", "Paper A", 2018),
        ("10.1/b", "B", "Paper B", 2019),
        ("10.1/c", "C", "Paper C", 2020),
        ("10.1/d", "D", "Paper D", 2017),
        ("10.1/e", "E", "Paper E", 2021),
    ]:
        store.upsert_paper(
            doi=doi,
            zotero_key=key,
            title=title,
            year=year,
            authors="Author",
            openalex_id=f"W{key}",
        )

    store.upsert_citation("10.1/a", "10.1/b")
    store.upsert_citation("10.1/a", "10.1/c")
    store.upsert_citation("10.1/b", "10.1/d")
    store.upsert_citation("10.1/c", "10.1/d")
    store.upsert_citation("10.1/c", "10.1/e")
    store.upsert_citation("10.1/e", "10.1/d")

    yield store
    os.unlink(path)


def test_build_from_store(populated_store):
    """build_from_store constructs graph with correct node/edge counts."""
    kg = KnowledgeGraph()
    stats = kg.build_from_store(populated_store)
    assert stats["nodes"] == 5
    assert stats["edges"] == 6
    assert stats["components"] == 1


def test_get_stats(populated_store):
    """get_stats returns summary with density and components."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store)
    stats = kg.get_stats()
    assert stats["nodes"] == 5
    assert stats["edges"] == 6
    assert stats["density"] > 0
    assert stats["components"] == 1


def test_get_influential_papers(populated_store):
    """PageRank ranks papers; D should be highly ranked (most cited)."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store)
    top = kg.get_influential_papers(top_n=5)
    assert len(top) == 5
    # D has most incoming edges, so should appear near the top
    dois = [p["doi"] for p in top]
    assert "10.1/d" in dois[:3]
    assert all("pagerank" in p for p in top)


def test_get_clusters(populated_store):
    """Cluster detection returns at least one cluster."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store)
    clusters = kg.get_clusters()
    assert len(clusters) >= 1
    total_papers = sum(c["size"] for c in clusters)
    assert total_papers == 5


def test_get_bridge_papers(populated_store):
    """Bridge papers have non-zero betweenness centrality."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store)
    bridges = kg.get_bridge_papers(top_n=5)
    # At least some papers should be bridges in this connected graph
    assert len(bridges) >= 1
    assert all("betweenness" in p for p in bridges)


def test_get_path(populated_store):
    """Shortest path finds a route between connected papers."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store)
    path = kg.get_path("10.1/a", "10.1/d")
    assert len(path) >= 2
    assert path[0]["doi"] == "10.1/a"
    assert path[-1]["doi"] == "10.1/d"


def test_get_path_no_path(populated_store):
    """get_path returns empty list for disconnected or nonexistent DOIs."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store)
    path = kg.get_path("10.1/a", "10.999/nonexistent")
    assert path == []


def test_get_neighborhood(populated_store):
    """Neighborhood returns papers within N hops."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store)
    result = kg.get_neighborhood("10.1/a", depth=1)
    assert result["center"] == "10.1/a"
    # A connects to B and C at depth 1
    neighbor_dois = {p["doi"] for p in result["papers"]}
    assert "10.1/a" in neighbor_dois  # center included at distance 0
    assert "10.1/b" in neighbor_dois
    assert "10.1/c" in neighbor_dois


def test_get_neighborhood_nonexistent(populated_store):
    """Neighborhood for nonexistent DOI returns empty."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store)
    result = kg.get_neighborhood("10.999/none")
    assert result["papers"] == []
    assert result["edges"] == []


def test_empty_graph():
    """Operations on empty graph return sensible defaults."""
    kg = KnowledgeGraph()
    assert kg.get_stats() == {"nodes": 0, "edges": 0, "density": 0, "components": 0}
    assert kg.get_influential_papers() == []
    assert kg.get_clusters() == []
    assert kg.get_bridge_papers() == []
