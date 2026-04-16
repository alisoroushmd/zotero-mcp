"""Tests for entity extraction tools in server.py."""

import json
import os
import tempfile

import pytest

from zotero_mcp.graph_store import GraphStore


@pytest.fixture
def tmp_db(monkeypatch):
    """Create a temporary database and point ZOTERO_MCP_GRAPH_DB at it."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("ZOTERO_MCP_GRAPH_DB", path)
    yield path
    os.unlink(path)


@pytest.fixture
def populated_store(tmp_db):
    """A GraphStore with papers and entities pre-populated."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1/a", zotero_key="A", title="Paper about gastric cancer",
        year=2024, authors="Smith J", openalex_id="W1",
        abstract="This paper studies CDX2 in gastric cancer.",
    )
    store.upsert_paper(
        doi="10.1/b", zotero_key="B", title="Paper about CDX2 biomarker",
        year=2023, authors="Lee A", openalex_id="W2",
        abstract="CDX2 expression in intestinal metaplasia.",
    )
    store.upsert_paper(
        doi="10.1/c", zotero_key="C", title="Paper with no entities yet",
        year=2022, authors="Kim B", openalex_id="W3",
        abstract="A study on H. pylori eradication.",
    )
    # Pre-extract entities for papers a and b
    eid1 = store.upsert_entity("gastric cancer", "condition")
    eid2 = store.upsert_entity("cdx2", "biomarker")
    eid3 = store.upsert_entity("intestinal metaplasia", "condition")
    store.upsert_paper_entity("10.1/a", eid1)
    store.upsert_paper_entity("10.1/a", eid2)
    store.upsert_paper_entity("10.1/b", eid2)
    store.upsert_paper_entity("10.1/b", eid3)
    store.close()
    return tmp_db


class TestStoreEntities:
    """Test store_entities tool JSON parsing and normalization."""

    def test_store_entities_from_json_string(self, tmp_db):
        """store_entities accepts a JSON string and normalizes entity names."""
        from zotero_mcp.server import store_entities

        store = GraphStore(tmp_db)
        store.upsert_paper(
            doi="10.1/x", zotero_key="X", title="Test",
            year=2024, authors="X", openalex_id="W1",
        )
        store.close()

        payload = json.dumps([{
            "doi": "10.1/x",
            "entities": [
                {"name": "CDX2", "type": "biomarker"},
                {"name": "Gastric Cancer", "type": "condition"},
            ],
        }])
        result = json.loads(store_entities(payload))
        assert result["stored"] == 1
        assert result["entities_created"] == 2
        assert result["entities_reused"] == 0

        # Verify normalization: names should be lowercase
        store2 = GraphStore(tmp_db)
        entities = store2.get_entities_for_doi("10.1/x")
        names = {e["name"] for e in entities}
        assert names == {"cdx2", "gastric cancer"}

    def test_store_entities_from_list(self, tmp_db):
        """store_entities accepts a parsed list directly."""
        from zotero_mcp.server import store_entities

        store = GraphStore(tmp_db)
        store.upsert_paper(
            doi="10.1/y", zotero_key="Y", title="Test2",
            year=2024, authors="Y", openalex_id="W2",
        )
        store.close()

        payload = [{
            "doi": "10.1/y",
            "entities": [
                {"name": "metformin", "type": "drug"},
            ],
        }]
        result = json.loads(store_entities(payload))
        assert result["stored"] == 1
        assert result["entities_created"] == 1

    def test_store_entities_reuse_existing(self, tmp_db):
        """Storing the same entity twice counts as reused, not created."""
        from zotero_mcp.server import store_entities

        store = GraphStore(tmp_db)
        store.upsert_paper(
            doi="10.1/a", zotero_key="A", title="Paper A",
            year=2024, authors="X", openalex_id="W1",
        )
        store.upsert_paper(
            doi="10.1/b", zotero_key="B", title="Paper B",
            year=2024, authors="Y", openalex_id="W2",
        )
        store.close()

        # First call creates the entity
        result1 = json.loads(store_entities(json.dumps([{
            "doi": "10.1/a",
            "entities": [{"name": "CDX2", "type": "biomarker"}],
        }])))
        assert result1["entities_created"] == 1
        assert result1["entities_reused"] == 0

        # Second call reuses it
        result2 = json.loads(store_entities(json.dumps([{
            "doi": "10.1/b",
            "entities": [{"name": "cdx2", "type": "biomarker"}],
        }])))
        assert result2["entities_created"] == 0
        assert result2["entities_reused"] == 1

    def test_store_entities_skips_empty(self, tmp_db):
        """Empty DOIs and empty entity names are skipped."""
        from zotero_mcp.server import store_entities

        payload = json.dumps([
            {"doi": "", "entities": [{"name": "test", "type": "condition"}]},
            {"doi": "10.1/z", "entities": [{"name": "", "type": "condition"}]},
            {"doi": "10.1/z", "entities": []},
        ])
        result = json.loads(store_entities(payload))
        assert result["stored"] == 0

    def test_store_entities_invalid_input(self, tmp_db):
        """Non-list input raises ValueError."""
        from zotero_mcp.server import store_entities

        result = json.loads(store_entities('{"not": "a list"}'))
        assert "error" in result


class TestSearchEntities:
    """Test search_entities query types with a populated GraphStore."""

    def test_by_name(self, populated_store):
        """by_name returns matching entities with their papers."""
        from zotero_mcp.server import search_entities

        result = json.loads(search_entities(
            query_type="by_name", entity_name="cdx2",
        ))
        assert result["query"] == "by_name"
        assert len(result["results"]) == 1
        assert result["results"][0]["name"] == "cdx2"
        assert result["results"][0]["paper_count"] == 2

    def test_by_type_specific(self, populated_store):
        """by_type with entity_type returns entities of that type."""
        from zotero_mcp.server import search_entities

        result = json.loads(search_entities(
            query_type="by_type", entity_type="condition",
        ))
        assert result["query"] == "by_type"
        assert result["entity_type"] == "condition"
        assert len(result["results"]) == 2  # gastric cancer + intestinal metaplasia

    def test_by_type_summary(self, populated_store):
        """by_type without entity_type returns a type summary."""
        from zotero_mcp.server import search_entities

        result = json.loads(search_entities(query_type="by_type"))
        assert result["query"] == "by_type"
        assert "entity_types" in result
        type_map = {t["entity_type"]: t["count"] for t in result["entity_types"]}
        assert type_map["condition"] == 2
        assert type_map["biomarker"] == 1

    def test_co_occurrence(self, populated_store):
        """co_occurrence returns entities that share papers with the given entity."""
        from zotero_mcp.server import search_entities

        result = json.loads(search_entities(
            query_type="co_occurrence", entity_name="cdx2",
        ))
        assert result["query"] == "co_occurrence"
        co_names = {e["name"] for e in result["co_occurring"]}
        assert "gastric cancer" in co_names
        assert "intestinal metaplasia" in co_names

    def test_shared_entities(self, populated_store):
        """shared_entities returns entities common to two papers."""
        from zotero_mcp.server import search_entities

        result = json.loads(search_entities(
            query_type="shared_entities", doi_a="10.1/a", doi_b="10.1/b",
        ))
        assert result["query"] == "shared_entities"
        shared_names = {e["name"] for e in result["shared"]}
        assert "cdx2" in shared_names
        assert "gastric cancer" not in shared_names

    def test_paper_entities(self, populated_store):
        """paper_entities returns all entities for a specific paper."""
        from zotero_mcp.server import search_entities

        result = json.loads(search_entities(
            query_type="paper_entities", doi="10.1/a",
        ))
        assert result["query"] == "paper_entities"
        assert result["doi"] == "10.1/a"
        entity_names = {e["name"] for e in result["entities"]}
        assert entity_names == {"gastric cancer", "cdx2"}

    def test_co_occurrence_not_found(self, populated_store):
        """co_occurrence returns error when entity is not found."""
        from zotero_mcp.server import search_entities

        result = json.loads(search_entities(
            query_type="co_occurrence", entity_name="nonexistent_entity_xyz",
        ))
        assert "error" in result

    def test_invalid_query_type(self, populated_store):
        """Invalid query_type raises ValueError."""
        from zotero_mcp.server import search_entities

        result = json.loads(search_entities(query_type="invalid_type"))
        assert "error" in result

    def test_missing_required_params(self, populated_store):
        """Missing required params raise ValueError."""
        from zotero_mcp.server import search_entities

        # by_name without entity_name
        result = json.loads(search_entities(query_type="by_name"))
        assert "error" in result

        # paper_entities without doi
        result = json.loads(search_entities(query_type="paper_entities"))
        assert "error" in result

        # shared_entities without both dois
        result = json.loads(search_entities(
            query_type="shared_entities", doi_a="10.1/a",
        ))
        assert "error" in result


class TestGetUnextractedAbstracts:
    """Test get_unextracted_abstracts tool."""

    def test_returns_unextracted_only(self, populated_store):
        """Only papers with abstracts but no entities are returned."""
        from zotero_mcp.server import get_unextracted_abstracts

        result = json.loads(get_unextracted_abstracts())
        assert result["remaining"] == 0
        assert len(result["papers"]) == 1
        assert result["papers"][0]["doi"] == "10.1/c"

    def test_limit_parameter(self, tmp_db):
        """Limit caps the returned batch size."""
        from zotero_mcp.server import get_unextracted_abstracts

        store = GraphStore(tmp_db)
        for i in range(5):
            store.upsert_paper(
                doi=f"10.1/{i}", zotero_key=f"K{i}", title=f"Paper {i}",
                year=2024, authors="X", openalex_id=f"W{i}",
                abstract=f"Abstract {i}",
            )
        store.close()

        result = json.loads(get_unextracted_abstracts(limit=2))
        assert len(result["papers"]) == 2
        assert result["remaining"] == 3
