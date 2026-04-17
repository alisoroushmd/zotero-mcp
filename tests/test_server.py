"""Tests for MCP server tool registration and timeout handling."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest


def test_server_has_all_tools():
    """Server exposes all 36 tools."""
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
    assert len(tools) == 36


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
