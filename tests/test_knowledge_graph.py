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


@pytest.fixture
def populated_store_with_topics():
    """GraphStore with papers and topic data for cluster labeling tests.

    Same graph structure as populated_store, but with topics:
    - A, B, D tagged as "Gastroenterology" (subfield)
    - C, E tagged as "Oncology" (subfield)
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

    # Add topics
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

    yield store
    os.unlink(path)


def test_get_clusters_with_topic_labels(populated_store_with_topics):
    """Clusters are labeled by dominant subfield."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store_with_topics)
    clusters = kg.get_clusters()
    assert len(clusters) >= 1
    # All clusters should have label, secondary_labels, topic_distribution
    for c in clusters:
        assert "label" in c
        assert "secondary_labels" in c
        assert "topic_distribution" in c
    # Check that known subfields appear in labels
    all_labels = {c["label"] for c in clusters}
    assert all_labels <= {"Gastroenterology", "Oncology", "Unlabeled"}


def test_get_clusters_without_topics_labels_unlabeled(populated_store):
    """Clusters from a store with no topics are labeled 'Unlabeled'."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store)
    clusters = kg.get_clusters()
    for c in clusters:
        assert c["label"] == "Unlabeled"
        assert c["topic_distribution"] == {}


def test_get_clusters_topic_distribution(populated_store_with_topics):
    """topic_distribution counts subfield occurrences across cluster papers."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store_with_topics)
    clusters = kg.get_clusters()
    # At least one cluster should have a non-empty distribution
    has_distribution = any(c["topic_distribution"] for c in clusters)
    assert has_distribution


@pytest.fixture
def populated_store_with_authors():
    """GraphStore with papers, citations, and author data for co-authorship tests.

    Authors:
        A1 = Alice Smith, A2 = Bob Jones, A3 = Carol Lee

    Paper-author assignments:
        Paper A: Alice (A1), Bob (A2)
        Paper B: Alice (A1), Carol (A3)
        Paper C: Bob (A2)
        Paper D: Alice (A1), Bob (A2), Carol (A3)
        Paper E: Carol (A3)

    Co-authorship edges:
        A1-A2: 2 shared papers (A, D)
        A1-A3: 2 shared papers (B, D)
        A2-A3: 1 shared paper (D)
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

    # Authors
    store.upsert_author("A1", "Alice Smith", "0000-0001", "MIT")
    store.upsert_author("A2", "Bob Jones", "0000-0002", "Stanford")
    store.upsert_author("A3", "Carol Lee", "0000-0003", "Harvard")

    # Paper-author assignments
    store.upsert_paper_author("10.1/a", "A1", 0)
    store.upsert_paper_author("10.1/a", "A2", 1)
    store.upsert_paper_author("10.1/b", "A1", 0)
    store.upsert_paper_author("10.1/b", "A3", 1)
    store.upsert_paper_author("10.1/c", "A2", 0)
    store.upsert_paper_author("10.1/d", "A1", 0)
    store.upsert_paper_author("10.1/d", "A2", 1)
    store.upsert_paper_author("10.1/d", "A3", 2)
    store.upsert_paper_author("10.1/e", "A3", 0)

    yield store
    os.unlink(path)


def test_build_from_store_loads_authors(populated_store_with_authors):
    """build_from_store populates _author_data and _author_papers."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store_with_authors)
    assert len(kg._author_data) == 3
    assert "A1" in kg._author_data
    assert "A2" in kg._author_data
    assert "A3" in kg._author_data
    # Alice has papers A, B, D
    assert kg._author_papers["A1"] == {"10.1/a", "10.1/b", "10.1/d"}
    # Bob has papers A, C, D
    assert kg._author_papers["A2"] == {"10.1/a", "10.1/c", "10.1/d"}
    # Carol has papers B, D, E
    assert kg._author_papers["A3"] == {"10.1/b", "10.1/d", "10.1/e"}


def test_get_prolific_authors(populated_store_with_authors):
    """Prolific authors ranked by paper count — all have 3 papers each."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store_with_authors)
    result = kg.get_prolific_authors(top_n=10)
    assert len(result) == 3
    # All authors have 3 papers
    for entry in result:
        assert entry["paper_count"] == 3
    # Check that display_name is present
    names = {r["display_name"] for r in result}
    assert names == {"Alice Smith", "Bob Jones", "Carol Lee"}


def test_get_influential_authors(populated_store_with_authors):
    """Influential authors have influence_score from PageRank."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store_with_authors)
    result = kg.get_influential_authors(top_n=10)
    assert len(result) == 3
    assert all("influence_score" in r for r in result)
    # Scores should be non-negative
    assert all(r["influence_score"] >= 0 for r in result)


def test_get_coauthors_of(populated_store_with_authors):
    """Alice's co-authors are Bob and Carol with correct shared_papers counts."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store_with_authors)
    result = kg.get_coauthors_of("A1", top_n=10)
    assert len(result) == 2
    coauthor_map = {r["display_name"]: r["shared_papers"] for r in result}
    # Alice co-authored with Bob on papers A and D
    assert coauthor_map["Bob Jones"] == 2
    # Alice co-authored with Carol on papers B and D
    assert coauthor_map["Carol Lee"] == 2


def test_get_author_clusters(populated_store_with_authors):
    """Author clustering returns at least 1 cluster with all 3 authors."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store_with_authors)
    clusters = kg.get_author_clusters()
    assert len(clusters) >= 1
    total_authors = sum(c["size"] for c in clusters)
    assert total_authors == 3


def test_get_author_network(populated_store_with_authors):
    """Ego network for Alice contains correct structure."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store_with_authors)
    result = kg.get_author_network("A1", depth=1)
    assert result["center"] == "A1"
    # Alice + Bob + Carol all within 1 hop
    assert len(result["authors"]) == 3
    # Check edges contain shared_papers
    assert len(result["edges"]) >= 2
    for edge in result["edges"]:
        assert "shared_papers" in edge
        assert edge["shared_papers"] >= 1


def test_resolve_author_exact(populated_store_with_authors):
    """Exact name 'Alice Smith' resolves to A1."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store_with_authors)
    assert kg._resolve_author("Alice Smith") == "A1"


def test_resolve_author_substring(populated_store_with_authors):
    """Substring 'alice' resolves to Alice Smith (A1)."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store_with_authors)
    assert kg._resolve_author("alice") == "A1"


def test_resolve_author_not_found(populated_store_with_authors):
    """Unknown name raises ValueError."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store_with_authors)
    with pytest.raises(ValueError, match="No author matching"):
        kg._resolve_author("Unknown Person")


def test_get_coauthors_empty_for_unknown(populated_store_with_authors):
    """Co-authors of a non-existent author returns empty list."""
    kg = KnowledgeGraph()
    kg.build_from_store(populated_store_with_authors)
    result = kg.get_coauthors_of("NONEXISTENT", top_n=10)
    assert result == []


def test_empty_graph():
    """Operations on empty graph return sensible defaults."""
    kg = KnowledgeGraph()
    assert kg.get_stats() == {"nodes": 0, "edges": 0, "density": 0, "components": 0}
    assert kg.get_influential_papers() == []
    assert kg.get_clusters() == []
    assert kg.get_bridge_papers() == []


# -- Temporal analytics tests --


@pytest.fixture
def temporal_store():
    """GraphStore with papers having publication_date for temporal tests.

    Papers span 2022-01 to 2024-06, with citations:
        B(2023-03) -> A(2022-01)
        C(2023-08) -> A(2022-01)
        D(2024-01) -> A(2022-01)
        D(2024-01) -> B(2023-03)
        E(2024-06) -> A(2022-01)

    A has 4 citations. B has 1.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = GraphStore(path)

    papers = [
        ("10.1/a", "A", "Paper A", 2022, "2022-01"),
        ("10.1/b", "B", "Paper B", 2023, "2023-03"),
        ("10.1/c", "C", "Paper C", 2023, "2023-08"),
        ("10.1/d", "D", "Paper D", 2024, "2024-01"),
        ("10.1/e", "E", "Paper E", 2024, "2024-06"),
    ]
    for doi, key, title, year, pub_date in papers:
        store.upsert_paper(
            doi=doi,
            zotero_key=key,
            title=title,
            year=year,
            authors="Author",
            openalex_id=f"W{key}",
            publication_date=pub_date,
        )

    # Topics: A, B = Gastroenterology; C, D, E = Oncology
    for doi in ["10.1/a", "10.1/b"]:
        store.upsert_topic(
            doi=doi,
            topic_id="T1",
            topic_name="GI Cancer",
            subfield="Gastroenterology",
            field="Medicine",
            domain="Health Sciences",
            score=0.9,
        )
    for doi in ["10.1/c", "10.1/d", "10.1/e"]:
        store.upsert_topic(
            doi=doi,
            topic_id="T2",
            topic_name="Tumor Biology",
            subfield="Oncology",
            field="Medicine",
            domain="Health Sciences",
            score=0.85,
        )

    # Citations: B, C, D, E all cite A; D also cites B
    store.upsert_citation("10.1/b", "10.1/a")
    store.upsert_citation("10.1/c", "10.1/a")
    store.upsert_citation("10.1/d", "10.1/a")
    store.upsert_citation("10.1/d", "10.1/b")
    store.upsert_citation("10.1/e", "10.1/a")

    yield store
    os.unlink(path)


def test_get_timeline(temporal_store):
    """Timeline returns papers per month sorted chronologically."""
    kg = KnowledgeGraph()
    kg.build_from_store(temporal_store)
    timeline = kg.get_timeline()
    assert len(timeline) == 5
    assert timeline[0] == {"month": "2022-01", "count": 1}
    assert timeline[-1] == {"month": "2024-06", "count": 1}


def test_get_timeline_with_topic_filter(temporal_store):
    """Timeline filtered by topic only includes matching papers."""
    kg = KnowledgeGraph()
    kg.build_from_store(temporal_store)
    timeline = kg.get_timeline(topic="Gastroenterology")
    assert len(timeline) == 2
    months = {t["month"] for t in timeline}
    assert months == {"2022-01", "2023-03"}


def test_get_timeline_with_year_range(temporal_store):
    """Timeline respects start_year and end_year filters."""
    kg = KnowledgeGraph()
    kg.build_from_store(temporal_store)
    timeline = kg.get_timeline(start_year=2023, end_year=2023)
    assert len(timeline) == 2
    months = {t["month"] for t in timeline}
    assert months == {"2023-03", "2023-08"}


def test_get_topic_evolution(temporal_store):
    """Topic evolution returns per-subfield monthly counts."""
    kg = KnowledgeGraph()
    kg.build_from_store(temporal_store)
    result = kg.get_topic_evolution()
    assert "Gastroenterology" in result
    assert "Oncology" in result
    # Gastroenterology: 2022-01 and 2023-03
    gi_months = {e["month"] for e in result["Gastroenterology"]}
    assert gi_months == {"2022-01", "2023-03"}
    # Oncology: 2023-08, 2024-01, 2024-06
    onc_months = {e["month"] for e in result["Oncology"]}
    assert onc_months == {"2023-08", "2024-01", "2024-06"}


def test_get_citation_velocity(temporal_store):
    """Citation velocity for paper A shows monthly citation pattern."""
    kg = KnowledgeGraph()
    kg.build_from_store(temporal_store)
    velocity = kg.get_citation_velocity("10.1/a")
    # A is cited by B(2023-03), C(2023-08), D(2024-01), E(2024-06)
    assert len(velocity) == 4
    months = [v["month"] for v in velocity]
    assert months == ["2023-03", "2023-08", "2024-01", "2024-06"]
    assert all(v["citations"] == 1 for v in velocity)


def test_get_citation_velocity_nonexistent(temporal_store):
    """Citation velocity for unknown DOI returns empty list."""
    kg = KnowledgeGraph()
    kg.build_from_store(temporal_store)
    assert kg.get_citation_velocity("10.999/none") == []


def test_get_trending(temporal_store):
    """Trending returns papers with recent citation acceleration."""
    kg = KnowledgeGraph()
    kg.build_from_store(temporal_store)
    # Use a large window to capture all citations
    trending = kg.get_trending(top_n=5, years=5)
    # Paper A has 4 citations, B has 1 (but needs >=2)
    # So only A should appear
    assert len(trending) >= 1
    assert trending[0]["doi"] == "10.1/a"
    assert trending[0]["total_citations"] == 4
    assert "velocity_ratio" in trending[0]
    assert "recent_citations" in trending[0]


def test_temporal_methods_on_empty_graph():
    """Temporal methods return empty results on empty graph."""
    kg = KnowledgeGraph()
    assert kg.get_timeline() == []
    assert kg.get_topic_evolution() == {}
    assert kg.get_citation_velocity("10.1/a") == []
    assert kg.get_trending() == []
