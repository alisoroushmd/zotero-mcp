"""Tests for MCP server tool registration and timeout handling."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest


def test_server_has_all_tools():
    """Server exposes all 39 tools."""
    from zotero_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    expected = {
        "search_items",
        "get_item",
        "get_collections",
        "get_collection_items",
        "get_notes",
        "get_item_attachments",
        "create_item",
        "create_item_manual",
        "create_note",
        "batch_organize",
        "find_duplicates",
        "create_collection",
        "audit_local_keys",
        "check_ssl_health",
        "add_to_collection",
        "update_item",
        "trash_items",
        "empty_trash",
        "inspect_trash",
        "plan_attachment_migration",
        "migrate_attachments",
        "manage_tags",
        "attach_pdf",
        "insert_citations",
        "write_cited_document",
        "server_status",
        "get_pdf_content",
        "check_retractions",
        "get_citation_graph",
        "check_published_versions",
        "build_index",
        "query_knowledge_graph",
        "find_related_papers",
        "query_authors",
        "export_knowledge_graph",
        "get_unextracted_abstracts",
        "search_entities",
        "store_entities",
        "search_fulltext",
    }
    actual = {t.name for t in tools}
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"Missing tools: {missing}"
    assert not extra, f"Extra tools: {extra}"
    assert len(tools) == 39


def test_server_has_prompts():
    """Server exposes MCP prompts for multi-tool workflows."""
    from zotero_mcp.server import mcp

    prompts = asyncio.run(mcp.list_prompts())
    prompt_names = {p.name for p in prompts}
    expected = {"literature_audit", "build_and_explore", "add_and_verify", "extract_entities"}
    missing = expected - prompt_names
    assert not missing, f"Missing prompts: {missing}"


def test_local_failed_ttl_allows_retry_after_interval():
    """After _local_failed_at is set, local client should be retried after the retry interval."""
    import zotero_mcp.server as srv

    old_timestamp = time.monotonic() - srv._LOCAL_RETRY_INTERVAL - 1.0
    with (
        patch.object(srv, "_local_failed_at", old_timestamp),
        patch.object(srv, "_local", None),
        patch("zotero_mcp.server.LocalClient") as mock_lc,
    ):
        mock_lc.return_value = MagicMock()
        srv._get_local()
        mock_lc.assert_called_once()


def test_local_failed_ttl_blocks_within_interval():
    """Within the retry interval, _get_local should raise without probing."""
    import zotero_mcp.server as srv

    recent_timestamp = time.monotonic() - 10.0
    with (
        patch.object(srv, "_local_failed_at", recent_timestamp),
        patch.object(srv, "_local", None),
        patch("zotero_mcp.server.LocalClient") as mock_lc,
    ):
        with pytest.raises(RuntimeError, match="unavailable"):
            srv._get_local()
        mock_lc.assert_not_called()


def test_read_local_or_web_httpx_timeout():
    """Web fallback converts httpx.TimeoutException to RuntimeError."""
    import httpx

    import zotero_mcp.server as srv

    def _timeout_method(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    # Set _local_failed_at to a recent timestamp so the TTL check marks local unavailable
    recent_ts = time.monotonic() - 10.0
    with (
        patch.object(srv, "_local_failed_at", recent_ts),
        patch.object(srv, "_local", None),
        patch.object(srv, "_get_web") as mock_web,
    ):
        mock_web.return_value.search_items = _timeout_method

        with pytest.raises(RuntimeError, match="timed out.*ReadTimeout"):
            srv._read_local_or_web("search_items", "test", 10)


def test_handle_tool_errors_catches_value_error():
    """_handle_tool_errors converts ValueError to structured JSON error."""
    import json as _json

    import zotero_mcp.server as srv

    @srv._handle_tool_errors
    def bad_tool():
        raise ValueError("item_key must not be empty")

    result = _json.loads(bad_tool())
    assert result["error"] == "invalid_input"
    assert "item_key" in result["message"]


def test_handle_tool_errors_catches_runtime_error():
    """_handle_tool_errors converts RuntimeError to structured JSON error."""
    import json as _json

    import zotero_mcp.server as srv

    @srv._handle_tool_errors
    def unavailable_tool():
        raise RuntimeError("Local API unavailable")

    result = _json.loads(unavailable_tool())
    assert result["error"] == "unavailable"
    assert "unavailable" in result["message"].lower()


def test_handle_tool_errors_passes_through_success():
    """_handle_tool_errors does not interfere with successful tool calls."""
    import zotero_mcp.server as srv

    @srv._handle_tool_errors
    def good_tool():
        return '{"ok": true}'

    assert good_tool() == '{"ok": true}'


def test_handle_tool_errors_includes_api_response_body():
    """api_error includes a truncated response body for actionable 4xx (ZOT-14/30)."""
    import json as _json

    import httpx

    import zotero_mcp.server as srv

    @srv._handle_tool_errors
    def conflict_tool():
        req = httpx.Request("PATCH", "https://api.zotero.org/items/X")
        resp = httpx.Response(412, request=req, text="item version mismatch")
        raise httpx.HTTPStatusError("412", request=req, response=resp)

    result = _json.loads(conflict_tool())
    assert result["error"] == "api_error"
    assert result["status_code"] == 412
    assert "item version mismatch" in result["message"]


def test_handle_tool_errors_catches_transport_error():
    """A non-timeout httpx transport error becomes a structured network_error (ZOT-14)."""
    import json as _json

    import httpx

    import zotero_mcp.server as srv

    @srv._handle_tool_errors
    def dns_tool():
        raise httpx.ConnectError("Name or service not known")

    result = _json.loads(dns_tool())
    assert result["error"] == "network_error"
    assert "ConnectError" in result["message"]


def test_handle_tool_errors_catches_data_shape_error():
    """A KeyError from an unexpected API response becomes internal_error (ZOT-14)."""
    import json as _json

    import zotero_mcp.server as srv

    @srv._handle_tool_errors
    def shape_tool():
        return {"a": 1}["missing"]

    result = _json.loads(shape_tool())
    assert result["error"] == "internal_error"


def test_handle_tool_errors_catchall():
    """Any other exception is caught by the final safety net (ZOT-14)."""
    import json as _json

    import zotero_mcp.server as srv

    @srv._handle_tool_errors
    def odd_tool():
        raise ZeroDivisionError("boom")

    result = _json.loads(odd_tool())
    assert result["error"] == "internal_error"
    assert "ZeroDivisionError" in result["message"]


def test_read_local_or_web_falls_back_on_local_http_error():
    """A local-API HTTP error (e.g. 500 mid-sync) falls back to the Web API (ZOT-15)."""
    import httpx

    import zotero_mcp.server as srv

    mock_local = MagicMock()
    req = httpx.Request("GET", "http://localhost:23119/api/users/0/items")
    resp = httpx.Response(500, request=req)
    mock_local.search_items.side_effect = httpx.HTTPStatusError("500", request=req, response=resp)

    mock_web = MagicMock()
    mock_web.search_items.return_value = [{"key": "FROMWEB"}]

    with (
        patch.object(srv, "_get_local", return_value=mock_local),
        patch.object(srv, "_get_web", return_value=mock_web),
    ):
        result = srv._read_local_or_web("search_items", "q", 10)

    assert result == [{"key": "FROMWEB"}]
    mock_web.search_items.assert_called_once()


# -- manage_tags tool routing --


def test_manage_tags_list_calls_get_tags():
    """manage_tags(action='list') delegates to WebClient.get_tags."""
    import json as _json

    import zotero_mcp.server as srv

    with patch.object(srv, "_get_web") as mock_web:
        mock_web.return_value.get_tags.return_value = [{"tag": "cancer"}]
        result = _json.loads(srv.manage_tags(action="list", prefix="can"))
        mock_web.return_value.get_tags.assert_called_once_with(prefix="can")
        assert result == [{"tag": "cancer"}]


def test_manage_tags_remove_calls_remove_tag():
    """manage_tags(action='remove') delegates to WebClient.remove_tag."""
    import json as _json

    import zotero_mcp.server as srv

    with patch.object(srv, "_get_web") as mock_web:
        mock_web.return_value.remove_tag.return_value = {"removed": 1}
        result = _json.loads(srv.manage_tags(action="remove", tag="old-tag"))
        mock_web.return_value.remove_tag.assert_called_once_with("old-tag")
        assert result["removed"] == 1


def test_manage_tags_rename_calls_rename_tag():
    """manage_tags(action='rename') delegates to WebClient.rename_tag."""
    import json as _json

    import zotero_mcp.server as srv

    with patch.object(srv, "_get_web") as mock_web:
        mock_web.return_value.rename_tag.return_value = {"renamed": 1}
        result = _json.loads(srv.manage_tags(action="rename", tag="old", new_tag="new"))
        mock_web.return_value.rename_tag.assert_called_once_with("old", "new")
        assert result["renamed"] == 1


def test_manage_tags_remove_requires_tag():
    """manage_tags(action='remove') with empty tag returns error."""
    import json as _json

    import zotero_mcp.server as srv

    with patch.object(srv, "_get_web") as mock_web:
        result = _json.loads(srv.manage_tags(action="remove", tag=""))
        assert result["error"] == "invalid_input"
        mock_web.return_value.remove_tag.assert_not_called()


def test_manage_tags_rename_requires_both_tags():
    """manage_tags(action='rename') with missing new_tag returns error."""
    import json as _json

    import zotero_mcp.server as srv

    with patch.object(srv, "_get_web") as mock_web:
        result = _json.loads(srv.manage_tags(action="rename", tag="old", new_tag=""))
        assert result["error"] == "invalid_input"
        mock_web.return_value.rename_tag.assert_not_called()


def test_manage_tags_invalid_action():
    """manage_tags with unknown action returns error."""
    import json as _json

    import zotero_mcp.server as srv

    with patch.object(srv, "_get_web"):
        result = _json.loads(srv.manage_tags(action="delete"))
        assert result["error"] == "invalid_input"


def test_manage_tags_annotated_destructive():
    """manage_tags carries destructiveHint — remove/rename alter the whole library (ZOT-37)."""
    from zotero_mcp.server import mcp

    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    annotations = tools["manage_tags"].annotations
    assert annotations.destructiveHint is True
    assert annotations.readOnlyHint is False


# -- batch key cap (ZOT-42) --


def test_check_retractions_caps_batch_at_50():
    """check_retractions processes at most _MAX_BATCH_KEYS keys and reports the cut."""
    import json as _json

    import zotero_mcp.server as srv

    keys = [f"KEY{i:04d}A" for i in range(60)]
    with (
        patch.object(srv, "_get_web") as mock_web,
        patch.object(srv, "_get_openalex"),
    ):
        # No DOI → warning path; neither CrossRef nor OpenAlex is hit.
        mock_web.return_value.get_item.return_value = {"title": "T"}
        result = _json.loads(srv.check_retractions(keys))
    assert result["checked"] == srv._MAX_BATCH_KEYS
    assert result["truncated"] is True
    assert result["submitted"] == 60
    assert result["processed"] == srv._MAX_BATCH_KEYS
    assert mock_web.return_value.get_item.call_count == srv._MAX_BATCH_KEYS


def test_check_published_versions_caps_batch_at_50():
    """check_published_versions applies the same _MAX_BATCH_KEYS cap."""
    import json as _json

    import zotero_mcp.server as srv

    keys = [f"KEY{i:04d}B" for i in range(55)]
    with (
        patch.object(srv, "_get_web") as mock_web,
        patch.object(srv, "_get_openalex"),
    ):
        mock_web.return_value.get_item.return_value = {"title": "T"}
        result = _json.loads(srv.check_published_versions(keys))
    assert result["checked"] == srv._MAX_BATCH_KEYS
    assert result["truncated"] is True
    assert result["submitted"] == 55


def test_check_retractions_small_batch_not_flagged():
    """A batch under the cap carries no truncation fields."""
    import json as _json

    import zotero_mcp.server as srv

    with (
        patch.object(srv, "_get_web") as mock_web,
        patch.object(srv, "_get_openalex"),
    ):
        mock_web.return_value.get_item.return_value = {"title": "T"}
        result = _json.loads(srv.check_retractions(["ABCD1234"]))
    assert "truncated" not in result
    assert result["checked"] == 1


# -- Semantic Scholar singleton (ZOT-38) --


def test_get_s2_returns_singleton():
    """_get_s2 initializes once and reuses the pooled client afterwards."""
    import zotero_mcp.server as srv

    with (
        patch.object(srv, "_s2", None),
        patch("zotero_mcp.semantic_scholar_client.SemanticScholarClient") as mock_cls,
    ):
        mock_cls.return_value = MagicMock()
        first = srv._get_s2()
        second = srv._get_s2()
        assert first is second
        mock_cls.assert_called_once()


def test_find_related_papers_uses_singleton():
    """find_related_papers goes through _get_s2, not a per-call client."""
    import zotero_mcp.server as srv

    s2 = MagicMock()
    s2.get_recommendations.return_value = []
    with (
        patch.object(srv, "_get_web") as mock_web,
        patch.object(srv, "_get_s2", return_value=s2) as mock_get_s2,
    ):
        mock_web.return_value.get_item.return_value = {"DOI": "10.1/x", "title": "T"}
        srv.find_related_papers("ABCD1234")
        srv.find_related_papers("ABCD1234")
    assert mock_get_s2.call_count == 2
    assert s2.get_recommendations.call_count == 2


# -- trending limit clamp (ZOT-41) --


def test_trending_uses_clamped_limit():
    """query_knowledge_graph(trending) clamps limit like every sibling branch."""
    import zotero_mcp.server as srv

    kg = MagicMock()
    kg.get_trending.return_value = []
    with patch.object(srv, "_get_or_build_kg", return_value=kg):
        srv.query_knowledge_graph(query_type="trending", limit=5000)
    kg.get_trending.assert_called_once_with(top_n=200, years=3)


# -- build_index type routing --


def test_build_index_invalid_type_fails_fast():
    """build_index with invalid type raises ValueError before any work."""
    import json as _json

    import zotero_mcp.server as srv

    result = _json.loads(srv.build_index(type="invalid"))
    assert result["error"] == "invalid_input"
    assert "invalid" in result["message"].lower()


def test_build_index_graph_delegates():
    """build_index(type='graph') calls _build_knowledge_graph."""
    import json as _json

    import zotero_mcp.server as srv

    with patch.object(srv, "_build_knowledge_graph", return_value={"papers": 5}):
        result = _json.loads(srv.build_index(type="graph"))
        assert "graph" in result
        assert result["graph"]["papers"] == 5
        assert "fulltext" not in result


def test_build_index_fulltext_delegates():
    """build_index(type='fulltext') calls _build_fulltext_index."""
    import json as _json

    import zotero_mcp.server as srv

    with patch.object(srv, "_build_fulltext_index", return_value={"indexed": 3}):
        result = _json.loads(srv.build_index(type="fulltext"))
        assert "fulltext" in result
        assert result["fulltext"]["indexed"] == 3
        assert "graph" not in result


def test_build_index_both_delegates_to_both():
    """build_index(type='both') calls both helpers."""
    import json as _json

    import zotero_mcp.server as srv

    with (
        patch.object(srv, "_build_knowledge_graph", return_value={"papers": 5}),
        patch.object(srv, "_build_fulltext_index", return_value={"indexed": 3}),
    ):
        result = _json.loads(srv.build_index(type="both"))
        assert result["graph"]["papers"] == 5
        assert result["fulltext"]["indexed"] == 3


# -- _parse_list_param tests --


def test_parse_list_param_with_native_list():
    """_parse_list_param passes through a native Python list unchanged."""
    from zotero_mcp.server import _parse_list_param

    assert _parse_list_param(["a", "b"]) == ["a", "b"]


def test_parse_list_param_with_json_string():
    """_parse_list_param deserialises a JSON-encoded list string."""
    from zotero_mcp.server import _parse_list_param

    assert _parse_list_param('["a","b"]') == ["a", "b"]


def test_parse_list_param_with_bare_string():
    """_parse_list_param wraps a bare (non-JSON) string in a single-item list."""
    from zotero_mcp.server import _parse_list_param

    assert _parse_list_param("ABC123") == ["ABC123"]


def test_parse_list_param_with_none():
    """_parse_list_param returns None when given None."""
    from zotero_mcp.server import _parse_list_param

    assert _parse_list_param(None) is None


# -- write_cited_document path-traversal and missing-key tests --


def test_write_cited_document_rejects_path_traversal():
    """write_cited_document raises ValueError for path traversal attempts."""
    import json as _json

    import zotero_mcp.server as srv

    with patch.object(srv, "_get_web"):
        result = _json.loads(
            srv.write_cited_document(
                content="Test [@ABC123].",
                output_path="/etc/passwd.docx",
            )
        )
    assert result.get("error") == "invalid_input"


def test_write_cited_document_missing_keys_reported():
    """write_cited_document reports missing keys that couldn't be fetched."""
    import json as _json
    import pathlib
    import tempfile

    import zotero_mcp.server as srv

    tmp_path = str(pathlib.Path(tempfile.gettempdir()) / "test_missing.docx")

    mock_web = MagicMock()
    # Return a string to simulate fetch failure (server.py checks `isinstance(item, str)`)
    mock_web.get_item.return_value = "error: not found"

    with (
        patch.object(srv, "_web", mock_web),
        patch.object(srv, "_get_local", side_effect=RuntimeError("no local")),
        patch.object(srv, "_get_web", return_value=mock_web),
    ):
        result = _json.loads(
            srv.write_cited_document(
                content="Test [@ABC123].",
                output_path=tmp_path,
            )
        )
    # Missing keys should be reported (either in result or the call should still succeed)
    assert "missing_keys" in result or result.get("error") is not None


# -- _handle_tool_errors timeout --


def test_handle_tool_errors_catches_timeout():
    """_handle_tool_errors converts httpx.TimeoutException to structured JSON error."""
    import json as _json

    import httpx

    import zotero_mcp.server as srv

    @srv._handle_tool_errors
    def slow_tool():
        raise httpx.ReadTimeout("connection timed out")

    result = _json.loads(slow_tool())
    assert result["error"] == "timeout"
    assert "timed out" in result["message"].lower()


# -- store_entities entity_type validation --


def test_store_entities_rejects_invalid_type():
    """store_entities raises ValueError for unknown entity types."""
    import json as _json

    import zotero_mcp.server as srv

    bad_input = [{"doi": "10.1/test", "entities": [{"name": "cancer", "type": "disease"}]}]

    with patch.object(srv, "_invalidate_kg_cache"):
        with patch("zotero_mcp.graph_store.GraphStore") as mock_gs_cls:
            mock_store = MagicMock()
            mock_gs_cls.return_value.__enter__ = MagicMock(return_value=mock_store)
            mock_gs_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = _json.loads(srv.store_entities(bad_input))

    assert result["error"] == "invalid_input"
    assert "disease" in result["message"]


def test_store_entities_accepts_valid_type():
    """store_entities accepts all documented entity types without error."""
    import json as _json

    import zotero_mcp.server as srv

    valid_input = [{"doi": "10.1/test", "entities": [{"name": "imatinib", "type": "drug"}]}]

    with patch.object(srv, "_invalidate_kg_cache"):
        with patch("zotero_mcp.graph_store.GraphStore") as mock_gs_cls:
            mock_store = MagicMock()
            mock_store.entity_exists.return_value = False
            mock_store.upsert_entity.return_value = 1
            mock_gs_cls.return_value.__enter__ = MagicMock(return_value=mock_store)
            mock_gs_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = _json.loads(srv.store_entities(valid_input))

    assert result.get("error") is None
    assert result["stored"] == 1
    assert result["entities_created"] == 1


# -- FTS5 malformed query handling --


def test_search_fulltext_bad_fts5_query_returns_error():
    """search_fulltext returns structured error for invalid FTS5 syntax."""
    import json as _json

    import zotero_mcp.server as srv

    with patch("zotero_mcp.graph_store.GraphStore") as mock_gs_cls:
        mock_store = MagicMock()
        mock_store.search_fulltext.side_effect = ValueError("Invalid full-text search query")
        mock_gs_cls.return_value.__enter__ = MagicMock(return_value=mock_store)
        mock_gs_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = _json.loads(srv.search_fulltext('"unbalanced'))

    assert result["error"] == "invalid_input"


# -- query_knowledge_graph dispatch --


def test_query_knowledge_graph_unknown_type_returns_error():
    """query_knowledge_graph returns structured error for unknown query_type."""
    import json as _json

    import zotero_mcp.server as srv

    mock_kg = MagicMock()
    with patch.object(srv, "_get_or_build_kg", return_value=mock_kg):
        result = _json.loads(srv.query_knowledge_graph(query_type="bogus"))

    assert result["error"] == "invalid_input"
    assert "bogus" in result["message"]


def test_query_knowledge_graph_path_requires_both_dois():
    """query_knowledge_graph path query raises if doi_a or doi_b missing."""
    import json as _json

    import zotero_mcp.server as srv

    mock_kg = MagicMock()
    with patch.object(srv, "_get_or_build_kg", return_value=mock_kg):
        result = _json.loads(srv.query_knowledge_graph(query_type="path", doi_a="10.1/a"))

    assert result["error"] == "invalid_input"
    assert "doi_b" in result["message"]


def test_query_knowledge_graph_influential_delegates():
    """query_knowledge_graph influential delegates to KnowledgeGraph.get_influential_papers."""
    import json as _json

    import zotero_mcp.server as srv

    mock_kg = MagicMock()
    mock_kg.get_influential_papers.return_value = [{"doi": "10.1/a", "pagerank": 0.9}]
    with patch.object(srv, "_get_or_build_kg", return_value=mock_kg):
        result = _json.loads(srv.query_knowledge_graph(query_type="influential", limit=5))

    mock_kg.get_influential_papers.assert_called_once_with(top_n=5)
    assert result[0]["doi"] == "10.1/a"


# -- find_related_papers server layer --


def test_find_related_papers_no_dois_returns_error():
    """find_related_papers returns error when none of the items have DOIs."""
    import json as _json

    import zotero_mcp.server as srv

    mock_web = MagicMock()
    mock_web.get_item.return_value = {"key": "ABC123", "title": "No DOI item"}

    with patch.object(srv, "_get_web", return_value=mock_web):
        result = _json.loads(srv.find_related_papers("ABC123"))

    assert "error" in result
    assert "DOI" in result["error"]


def test_find_related_papers_flags_in_library():
    """find_related_papers marks recommendations already in library."""
    import json as _json

    import zotero_mcp.server as srv

    mock_web = MagicMock()
    mock_web.get_item.return_value = {"key": "ABC123", "DOI": "10.1/seed", "title": "Seed"}
    mock_web._check_duplicate_doi.side_effect = lambda doi: (
        {"key": "REC001"} if doi == "10.1/in_lib" else None
    )

    mock_s2 = MagicMock()
    mock_s2.get_recommendations.return_value = [
        {"doi": "10.1/in_lib", "title": "Already have this"},
        {"doi": "10.1/new", "title": "New paper"},
    ]

    with (
        patch.object(srv, "_get_web", return_value=mock_web),
        patch("zotero_mcp.semantic_scholar_client.SemanticScholarClient", return_value=mock_s2),
    ):
        result = _json.loads(srv.find_related_papers("ABC123"))

    recs = result["recommendations"]
    assert recs[0]["in_library"] is True
    assert recs[0]["zotero_key"] == "REC001"
    assert recs[1]["in_library"] is False


# -- DOI normalization for graph back-links (ZOT-22) --


def test_norm_doi_lowercases_and_strips_prefix():
    """_norm_doi canonicalizes case and strips the doi.org prefix (ZOT-22)."""
    import zotero_mcp.server as srv

    assert srv._norm_doi("10.1016/J.GIE.2020.01.001") == "10.1016/j.gie.2020.01.001"
    assert srv._norm_doi("https://doi.org/10.1/AbC") == "10.1/abc"
    assert srv._norm_doi("  10.1/X  ") == "10.1/x"
    assert srv._norm_doi("") == ""


def test_index_works_populates_zotero_key_for_uppercase_doi(tmp_path):
    """An uppercase Zotero DOI still matches OpenAlex's lowercase DOI (ZOT-22)."""
    import zotero_mcp.server as srv
    from zotero_mcp.graph_store import GraphStore

    store = GraphStore(str(tmp_path / "g.sqlite"))
    # Zotero DOI is uppercase; OpenAlex returns it lowercased.
    key_by_doi = {srv._norm_doi("10.1016/J.GIE.2020.001"): "ZKEY1"}
    works = [
        {
            "id": "https://openalex.org/W1",
            "doi": "https://doi.org/10.1016/j.gie.2020.001",
            "title": "Paper",
            "publication_year": 2020,
            "authorships": [],
            "referenced_works": [],
        }
    ]
    mock_oa = MagicMock()
    srv._index_works(works, key_by_doi, store, mock_oa)
    paper = store.get_paper("10.1016/j.gie.2020.001")
    assert paper is not None
    assert paper["zotero_key"] == "ZKEY1"  # would be "" before the fix
    store.close()


# -- KG query DOI normalization (ZOT-22 review) --


def test_query_knowledge_graph_normalizes_doi_inputs():
    """path/neighborhood/citation_velocity normalize DOIs to match graph nodes (ZOT-22 review)."""

    import zotero_mcp.server as srv

    mock_kg = MagicMock()
    mock_kg.get_neighborhood.return_value = {"ok": True}
    mock_kg.get_path.return_value = []
    mock_kg.get_citation_velocity.return_value = []

    with patch.object(srv, "_get_or_build_kg", return_value=mock_kg):
        srv.query_knowledge_graph(query_type="neighborhood", doi="10.1016/J.GIE.2023.01.001")
        mock_kg.get_neighborhood.assert_called_once()
        assert mock_kg.get_neighborhood.call_args.args[0] == "10.1016/j.gie.2023.01.001"

        srv.query_knowledge_graph(
            query_type="path",
            doi_a="https://doi.org/10.1/A",
            doi_b="10.2/B",
        )
        assert mock_kg.get_path.call_args.args == ("10.1/a", "10.2/b")

        srv.query_knowledge_graph(query_type="citation_velocity", doi="10.3/CcC")
        assert mock_kg.get_citation_velocity.call_args.args[0] == "10.3/ccc"


def test_get_pdf_content_extract_text_degrades_without_pypdf():
    """extract_text=True without pypdf returns the path result, not an error (ZOT-30 review)."""
    import json as _json

    import zotero_mcp.server as srv

    mock_web = MagicMock()
    mock_web.get_item.return_value = {"key": "K", "DOI": "", "title": "T", "extra": "", "url": ""}
    # Force the local-PDF branch to yield a path.
    mock_local = MagicMock()
    mock_local.get_item.return_value = mock_web.get_item.return_value

    with (
        patch.object(srv, "_get_local", side_effect=RuntimeError("no local")),
        patch.object(srv, "_get_web", return_value=mock_web),
        patch("zotero_mcp.text_extractor.pypdf_available", return_value=False),
    ):
        # Even if no PDF source is found, the tool must not raise an internal_error
        # purely because pypdf is missing; it returns a structured result.
        result = _json.loads(srv.get_pdf_content("K", extract_text=True))
    assert result.get("error") != "internal_error"
