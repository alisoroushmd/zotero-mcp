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


def test_upsert_and_get_topic(tmp_db):
    """Topics can be stored and retrieved by DOI."""
    store = GraphStore(tmp_db)
    store.upsert_topic(
        doi="10.1/a",
        topic_id="T1",
        topic_name="Gastric Cancer",
        subfield="Oncology",
        field="Medicine",
        domain="Health Sciences",
        score=0.95,
    )
    topics = store.get_topics_for_doi("10.1/a")
    assert len(topics) == 1
    assert topics[0]["topic_id"] == "T1"
    assert topics[0]["topic_name"] == "Gastric Cancer"
    assert topics[0]["score"] == 0.95


def test_upsert_topic_updates_on_conflict(tmp_db):
    """Upserting the same (doi, topic_id) updates fields."""
    store = GraphStore(tmp_db)
    store.upsert_topic(
        doi="10.1/a",
        topic_id="T1",
        topic_name="Old Name",
        subfield="Old Subfield",
        field="Old Field",
        domain="Old Domain",
        score=0.5,
    )
    store.upsert_topic(
        doi="10.1/a",
        topic_id="T1",
        topic_name="Gastric Cancer",
        subfield="Oncology",
        field="Medicine",
        domain="Health Sciences",
        score=0.95,
    )
    topics = store.get_topics_for_doi("10.1/a")
    assert len(topics) == 1
    assert topics[0]["topic_name"] == "Gastric Cancer"
    assert topics[0]["subfield"] == "Oncology"
    assert topics[0]["score"] == 0.95


def test_upsert_and_get_author(tmp_db):
    """Authors can be stored and retrieved."""
    store = GraphStore(tmp_db)
    store.upsert_author(
        openalex_author_id="A100",
        display_name="Jane Smith",
        orcid="0000-0001-2345-6789",
        institution="Mount Sinai",
    )
    authors = store.get_all_authors()
    assert len(authors) == 1
    assert authors[0]["openalex_author_id"] == "A100"
    assert authors[0]["display_name"] == "Jane Smith"
    assert authors[0]["institution"] == "Mount Sinai"


def test_upsert_author_updates_on_conflict(tmp_db):
    """Upserting the same author_id updates fields."""
    store = GraphStore(tmp_db)
    store.upsert_author(
        openalex_author_id="A100",
        display_name="Jane Smith",
        orcid=None,
        institution="Old University",
    )
    store.upsert_author(
        openalex_author_id="A100",
        display_name="Jane A. Smith",
        orcid="0000-0001-2345-6789",
        institution="Mount Sinai",
    )
    authors = store.get_all_authors()
    assert len(authors) == 1
    assert authors[0]["display_name"] == "Jane A. Smith"
    assert authors[0]["orcid"] == "0000-0001-2345-6789"
    assert authors[0]["institution"] == "Mount Sinai"


def test_upsert_and_get_paper_author(tmp_db):
    """Paper-author links can be stored and retrieved."""
    store = GraphStore(tmp_db)
    store.upsert_paper_author(
        doi="10.1/a",
        openalex_author_id="A100",
        position=0,
    )
    store.upsert_paper_author(
        doi="10.1/a",
        openalex_author_id="A200",
        position=1,
    )
    links = store.get_all_paper_authors()
    assert len(links) == 2
    assert ("10.1/a", "A100", 0) in links
    assert ("10.1/a", "A200", 1) in links


def test_get_all_topics(tmp_db):
    """Can retrieve topics across multiple DOIs."""
    store = GraphStore(tmp_db)
    store.upsert_topic(
        doi="10.1/a",
        topic_id="T1",
        topic_name="Gastric Cancer",
        subfield="Oncology",
        field="Medicine",
        domain="Health Sciences",
        score=0.9,
    )
    store.upsert_topic(
        doi="10.1/b",
        topic_id="T2",
        topic_name="Machine Learning",
        subfield="AI",
        field="Computer Science",
        domain="Physical Sciences",
        score=0.8,
    )
    topics = store.get_all_topics()
    assert len(topics) == 2
    topic_ids = {t["topic_id"] for t in topics}
    assert topic_ids == {"T1", "T2"}


def test_get_topics_for_doi_filters(tmp_db):
    """get_topics_for_doi only returns topics for the requested DOI."""
    store = GraphStore(tmp_db)
    store.upsert_topic(
        doi="10.1/a",
        topic_id="T1",
        topic_name="Gastric Cancer",
        subfield="Oncology",
        field="Medicine",
        domain="Health Sciences",
        score=0.9,
    )
    store.upsert_topic(
        doi="10.1/b",
        topic_id="T2",
        topic_name="Machine Learning",
        subfield="AI",
        field="Computer Science",
        domain="Physical Sciences",
        score=0.8,
    )
    topics_a = store.get_topics_for_doi("10.1/a")
    assert len(topics_a) == 1
    assert topics_a[0]["topic_id"] == "T1"

    topics_b = store.get_topics_for_doi("10.1/b")
    assert len(topics_b) == 1
    assert topics_b[0]["topic_id"] == "T2"
