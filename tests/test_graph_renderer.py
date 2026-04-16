"""Tests for graph_renderer — HTML visualization of knowledge graph."""

import json
import os
import tempfile

import pytest

from zotero_mcp.graph_renderer import (
    _render_html,
    render_authors_view,
    render_citations_view,
    render_full_view,
)
from zotero_mcp.graph_store import GraphStore
from zotero_mcp.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg_with_data():
    """Build a KnowledgeGraph with papers, citations, topics, and authors."""
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

    # Topics
    for doi in ["10.1/a", "10.1/b", "10.1/d"]:
        store.upsert_topic(
            doi=doi,
            topic_id="T1",
            topic_name="GI Cancer",
            subfield="Gastroenterology",
            field="Medicine",
            domain="Health Sciences",
            score=0.9,
        )
    for doi in ["10.1/c", "10.1/e"]:
        store.upsert_topic(
            doi=doi,
            topic_id="T2",
            topic_name="Tumor Biology",
            subfield="Oncology",
            field="Medicine",
            domain="Health Sciences",
            score=0.85,
        )

    # Authors
    store.upsert_author("A1", "Alice Smith", "0000-0001", "MIT")
    store.upsert_author("A2", "Bob Jones", "0000-0002", "Stanford")
    store.upsert_paper_author("10.1/a", "A1", 0)
    store.upsert_paper_author("10.1/a", "A2", 1)
    store.upsert_paper_author("10.1/b", "A1", 0)
    store.upsert_paper_author("10.1/d", "A2", 0)

    kg = KnowledgeGraph()
    kg.build_from_store(store)
    yield kg
    os.unlink(path)


@pytest.fixture
def empty_kg():
    """Empty KnowledgeGraph with no data."""
    return KnowledgeGraph()


def test_render_html_embeds_json():
    """_render_html injects JSON data into the template."""
    data = {"nodes": [{"id": "test"}], "edges": [], "group_labels": {}}
    html = _render_html(data)
    assert "window.__GRAPH_DATA" in html
    assert '"test"' in html
    assert "d3.v7.min.js" in html


def test_render_html_valid_structure():
    """Output is well-formed HTML with required elements."""
    data = {"nodes": [], "edges": [], "group_labels": {}}
    html = _render_html(data)
    assert html.startswith("<!DOCTYPE html>")
    assert "<svg>" in html
    assert "info-panel" in html
    assert "legend" in html


def test_citations_view_nodes_and_edges(kg_with_data):
    """Citations view has correct node/edge counts."""
    html, stats = render_citations_view(kg_with_data)
    assert stats["view"] == "citations"
    assert stats["nodes"] == 5
    assert stats["edges"] == 6
    assert "window.__GRAPH_DATA" in html


def test_citations_view_data_structure(kg_with_data):
    """Citations view embeds well-formed graph data."""
    html, _ = render_citations_view(kg_with_data)
    # Extract JSON from HTML
    marker = "window.__GRAPH_DATA = "
    start = html.index(marker) + len(marker)
    end = html.index(";</script>", start)
    data = json.loads(html[start:end])
    assert len(data["nodes"]) == 5
    assert len(data["edges"]) == 6
    # All edges are citations
    assert all(e["type"] == "citation" for e in data["edges"])
    # Nodes have required fields
    for n in data["nodes"]:
        assert "id" in n
        assert "label" in n
        assert "type" in n
        assert n["type"] == "paper"
        assert "size" in n
        assert "meta" in n


def test_citations_view_empty(empty_kg):
    """Citations view on empty graph returns 0 counts."""
    html, stats = render_citations_view(empty_kg)
    assert stats["nodes"] == 0
    assert stats["edges"] == 0
    assert "window.__GRAPH_DATA" in html


def test_authors_view_nodes_and_edges(kg_with_data):
    """Authors view has correct node/edge counts."""
    html, stats = render_authors_view(kg_with_data)
    assert stats["view"] == "authors"
    assert stats["nodes"] == 2  # Alice and Bob
    assert stats["edges"] == 1  # co-authored paper A
    assert "window.__GRAPH_DATA" in html


def test_authors_view_data_structure(kg_with_data):
    """Authors view nodes are typed 'author' with coauthor edges."""
    html, _ = render_authors_view(kg_with_data)
    marker = "window.__GRAPH_DATA = "
    start = html.index(marker) + len(marker)
    end = html.index(";</script>", start)
    data = json.loads(html[start:end])
    assert all(n["type"] == "author" for n in data["nodes"])
    assert all(e["type"] == "coauthor" for e in data["edges"])
    # Edges have weight
    for e in data["edges"]:
        assert "weight" in e


def test_authors_view_empty(empty_kg):
    """Authors view on empty graph returns 0 counts."""
    html, stats = render_authors_view(empty_kg)
    assert stats["nodes"] == 0
    assert stats["edges"] == 0


def test_full_view_contains_both(kg_with_data):
    """Full view includes paper and author nodes, citation and coauthor edges."""
    html, stats = render_full_view(kg_with_data)
    assert stats["view"] == "full"
    # 5 papers + 2 authors
    assert stats["nodes"] == 7
    # 6 citations + 1 coauthor
    assert stats["edges"] == 7

    marker = "window.__GRAPH_DATA = "
    start = html.index(marker) + len(marker)
    end = html.index(";</script>", start)
    data = json.loads(html[start:end])
    types = {n["type"] for n in data["nodes"]}
    assert types == {"paper", "author"}
    edge_types = {e["type"] for e in data["edges"]}
    assert edge_types == {"citation", "coauthor"}
