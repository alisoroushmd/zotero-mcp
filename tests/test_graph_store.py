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


def test_upsert_paper_with_publication_date(tmp_db):
    """Papers can store and retrieve publication_date and abstract."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1234/test",
        zotero_key="ABC123",
        title="Test Paper",
        year=2024,
        authors="Smith J",
        openalex_id="W12345",
        publication_date="2024-03",
        abstract="This is a test abstract.",
    )
    paper = store.get_paper("10.1234/test")
    assert paper["publication_date"] == "2024-03"
    assert paper["abstract"] == "This is a test abstract."


def test_upsert_paper_abstract_coalesce(tmp_db):
    """Abstract uses COALESCE — NULL update preserves existing abstract."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1/a",
        zotero_key="A",
        title="A",
        year=2024,
        authors="X",
        openalex_id="W1",
        abstract="Original abstract",
    )
    # Upsert again with abstract=None should preserve original
    store.upsert_paper(
        doi="10.1/a",
        zotero_key="A",
        title="A",
        year=2024,
        authors="X",
        openalex_id="W1",
        abstract=None,
    )
    paper = store.get_paper("10.1/a")
    assert paper["abstract"] == "Original abstract"


def test_upsert_entity_normalization(tmp_db):
    """'CDX2' and 'cdx2' with same type map to the same entity_id."""
    store = GraphStore(tmp_db)
    id1 = store.upsert_entity("CDX2", "biomarker")
    id2 = store.upsert_entity("cdx2", "biomarker")
    id3 = store.upsert_entity("  Cdx2  ", "biomarker")
    assert id1 == id2 == id3


def test_upsert_entity_different_types(tmp_db):
    """Same name with different types creates separate entities."""
    store = GraphStore(tmp_db)
    id1 = store.upsert_entity("p53", "gene")
    id2 = store.upsert_entity("p53", "biomarker")
    assert id1 != id2


def test_upsert_and_get_entities_for_doi(tmp_db):
    """Store entities for a paper, retrieve them."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1/a",
        zotero_key="A",
        title="Paper A",
        year=2024,
        authors="X",
        openalex_id="W1",
    )
    eid1 = store.upsert_entity("gastric cancer", "condition")
    eid2 = store.upsert_entity("CDX2", "biomarker")
    store.upsert_paper_entity("10.1/a", eid1, confidence=0.95)
    store.upsert_paper_entity("10.1/a", eid2, confidence=0.8)

    entities = store.get_entities_for_doi("10.1/a")
    assert len(entities) == 2
    names = {e["name"] for e in entities}
    assert names == {"gastric cancer", "cdx2"}
    # Check confidence is returned
    conf_map = {e["name"]: e["confidence"] for e in entities}
    assert conf_map["gastric cancer"] == 0.95


def test_get_papers_for_entity(tmp_db):
    """Store entities across papers, query by entity_id."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1/a",
        zotero_key="A",
        title="Paper A",
        year=2024,
        authors="X",
        openalex_id="W1",
    )
    store.upsert_paper(
        doi="10.1/b",
        zotero_key="B",
        title="Paper B",
        year=2023,
        authors="Y",
        openalex_id="W2",
    )
    eid = store.upsert_entity("h. pylori", "organism")
    store.upsert_paper_entity("10.1/a", eid)
    store.upsert_paper_entity("10.1/b", eid)

    papers = store.get_papers_for_entity(eid)
    assert len(papers) == 2
    dois = {p["doi"] for p in papers}
    assert dois == {"10.1/a", "10.1/b"}


def test_get_unextracted_dois(tmp_db):
    """Papers with abstracts but no entities are returned; papers with entities are excluded."""
    store = GraphStore(tmp_db)
    # Paper with abstract and no entities
    store.upsert_paper(
        doi="10.1/a",
        zotero_key="A",
        title="Paper A",
        year=2024,
        authors="X",
        openalex_id="W1",
        abstract="This paper studies gastric cancer.",
    )
    # Paper with abstract and entities (should be excluded)
    store.upsert_paper(
        doi="10.1/b",
        zotero_key="B",
        title="Paper B",
        year=2023,
        authors="Y",
        openalex_id="W2",
        abstract="This paper studies CDX2 expression.",
    )
    eid = store.upsert_entity("cdx2", "biomarker")
    store.upsert_paper_entity("10.1/b", eid)
    # Paper with no abstract (should be excluded)
    store.upsert_paper(
        doi="10.1/c",
        zotero_key="C",
        title="Paper C",
        year=2022,
        authors="Z",
        openalex_id="W3",
    )

    unextracted = store.get_unextracted_dois()
    assert len(unextracted) == 1
    assert unextracted[0]["doi"] == "10.1/a"
    assert unextracted[0]["abstract"] == "This paper studies gastric cancer."


def test_entity_co_occurrence(tmp_db):
    """Entities that share papers are returned."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1/a",
        zotero_key="A",
        title="Paper A",
        year=2024,
        authors="X",
        openalex_id="W1",
    )
    eid1 = store.upsert_entity("gastric cancer", "condition")
    eid2 = store.upsert_entity("cdx2", "biomarker")
    eid3 = store.upsert_entity("metformin", "drug")
    store.upsert_paper_entity("10.1/a", eid1)
    store.upsert_paper_entity("10.1/a", eid2)
    store.upsert_paper_entity("10.1/a", eid3)

    co = store.get_entity_co_occurrence(eid1)
    assert len(co) == 2
    co_names = {e["name"] for e in co}
    assert co_names == {"cdx2", "metformin"}
    # Each co-occurrence should have shared_papers = 1
    for entry in co:
        assert entry["shared_papers"] == 1


def test_get_shared_entities(tmp_db):
    """Entities common to two papers are returned."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1/a",
        zotero_key="A",
        title="Paper A",
        year=2024,
        authors="X",
        openalex_id="W1",
    )
    store.upsert_paper(
        doi="10.1/b",
        zotero_key="B",
        title="Paper B",
        year=2023,
        authors="Y",
        openalex_id="W2",
    )
    eid_shared = store.upsert_entity("gastric cancer", "condition")
    eid_only_a = store.upsert_entity("cdx2", "biomarker")
    eid_only_b = store.upsert_entity("metformin", "drug")
    store.upsert_paper_entity("10.1/a", eid_shared)
    store.upsert_paper_entity("10.1/a", eid_only_a)
    store.upsert_paper_entity("10.1/b", eid_shared)
    store.upsert_paper_entity("10.1/b", eid_only_b)

    shared = store.get_shared_entities("10.1/a", "10.1/b")
    assert len(shared) == 1
    assert shared[0]["name"] == "gastric cancer"


def test_get_all_entity_types(tmp_db):
    """Entity types are grouped and counted correctly."""
    store = GraphStore(tmp_db)
    store.upsert_entity("gastric cancer", "condition")
    store.upsert_entity("crohn's disease", "condition")
    store.upsert_entity("cdx2", "biomarker")

    types = store.get_all_entity_types()
    type_map = {t["entity_type"]: t["count"] for t in types}
    assert type_map["condition"] == 2
    assert type_map["biomarker"] == 1


def test_search_entities_by_name(tmp_db):
    """Case-insensitive name search works."""
    store = GraphStore(tmp_db)
    store.upsert_entity("gastric cancer", "condition")
    store.upsert_entity("gastric intestinal metaplasia", "condition")
    store.upsert_entity("cdx2", "biomarker")

    results = store.search_entities_by_name("gastric")
    assert len(results) == 2
    names = {r["name"] for r in results}
    assert "gastric cancer" in names
    assert "gastric intestinal metaplasia" in names


def test_migration_adds_columns(tmp_db):
    """Migration adds publication_date and abstract columns to existing DBs."""
    import sqlite3

    # Create a v0.6.0-style database without the new columns
    conn = sqlite3.connect(tmp_db)
    conn.executescript("""
        CREATE TABLE papers (
            doi TEXT PRIMARY KEY,
            zotero_key TEXT,
            title TEXT,
            year INTEGER,
            authors TEXT,
            openalex_id TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute(
        "INSERT INTO papers (doi, zotero_key, title, year, authors, openalex_id) "
        "VALUES ('10.1/old', 'OLD', 'Old Paper', 2020, 'X', 'W1')"
    )
    conn.commit()
    conn.close()

    # Opening with GraphStore should migrate transparently
    store = GraphStore(tmp_db)
    paper = store.get_paper("10.1/old")
    assert paper is not None
    assert paper["publication_date"] is None  # not backfilled yet

    # Can now upsert with new columns
    store.upsert_paper(
        doi="10.1/new",
        zotero_key="NEW",
        title="New Paper",
        year=2024,
        authors="Y",
        openalex_id="W2",
        publication_date="2024-06",
        abstract="Test abstract",
    )
    new_paper = store.get_paper("10.1/new")
    assert new_paper["publication_date"] == "2024-06"
    assert new_paper["abstract"] == "Test abstract"
