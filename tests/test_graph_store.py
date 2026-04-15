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
    store.upsert_paper(
        doi="10.1/a",
        zotero_key="A",
        title="Paper A",
        year=2020,
        authors="X",
        openalex_id="W1",
    )
    store.upsert_paper(
        doi="10.1/b",
        zotero_key="B",
        title="Paper B",
        year=2022,
        authors="Y",
        openalex_id="W2",
    )
    store.upsert_citation(citing_doi="10.1/b", cited_doi="10.1/a")
    refs = store.get_references("10.1/b")
    assert len(refs) == 1
    assert refs[0]["doi"] == "10.1/a"


def test_get_citing_papers(tmp_db):
    """Can retrieve papers that cite a given DOI."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1/a",
        zotero_key="A",
        title="A",
        year=2020,
        authors="X",
        openalex_id="W1",
    )
    store.upsert_paper(
        doi="10.1/b",
        zotero_key="B",
        title="B",
        year=2022,
        authors="Y",
        openalex_id="W2",
    )
    store.upsert_citation(citing_doi="10.1/b", cited_doi="10.1/a")
    citers = store.get_citing_papers("10.1/a")
    assert len(citers) == 1
    assert citers[0]["doi"] == "10.1/b"


def test_get_all_papers(tmp_db):
    """Can retrieve all papers for graph construction."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1/a",
        zotero_key="A",
        title="A",
        year=2020,
        authors="X",
        openalex_id="W1",
    )
    store.upsert_paper(
        doi="10.1/b",
        zotero_key="B",
        title="B",
        year=2022,
        authors="Y",
        openalex_id="W2",
    )
    papers = store.get_all_papers()
    assert len(papers) == 2


def test_get_all_citations(tmp_db):
    """Can retrieve all edges for graph construction."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1/a",
        zotero_key="A",
        title="A",
        year=2020,
        authors="X",
        openalex_id="W1",
    )
    store.upsert_paper(
        doi="10.1/b",
        zotero_key="B",
        title="B",
        year=2022,
        authors="Y",
        openalex_id="W2",
    )
    store.upsert_citation(citing_doi="10.1/b", cited_doi="10.1/a")
    edges = store.get_all_citations()
    assert len(edges) == 1
    assert edges[0] == ("10.1/b", "10.1/a")


def test_get_sync_state(tmp_db):
    """Sync state tracks last build time and library version."""
    store = GraphStore(tmp_db)
    assert store.get_last_sync() is None
    store.set_last_sync("2026-04-09T12:00:00Z", library_version=42)
    sync = store.get_last_sync()
    assert sync["timestamp"] == "2026-04-09T12:00:00Z"
    assert sync["library_version"] == 42


def test_get_doi_set(tmp_db):
    """Can retrieve set of all DOIs for fast membership checks."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1/a",
        zotero_key="A",
        title="A",
        year=2020,
        authors="X",
        openalex_id="W1",
    )
    store.upsert_paper(
        doi="10.1/b",
        zotero_key="B",
        title="B",
        year=2022,
        authors="Y",
        openalex_id="W2",
    )
    doi_set = store.get_doi_set()
    assert doi_set == {"10.1/a", "10.1/b"}
