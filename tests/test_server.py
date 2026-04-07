"""Tests for MCP server tool registration and timeout handling."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest


def test_server_has_all_tools():
    """Server exposes all 28 tools."""
    from zotero_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    expected = {
        "search_items",
        "get_item",
        "get_collections",
        "get_collection_items",
        "get_notes",
        "get_item_attachments",
        "create_item_from_identifier",
        "create_item_from_url",
        "create_item_manual",
        "create_note",
        "batch_organize",
        "find_duplicates",
        "create_collection",
        "add_to_collection",
        "update_item",
        "trash_items",
        "empty_trash",
        "get_tags",
        "remove_tag",
        "rename_tag",
        "attach_pdf",
        "insert_citations",
        "write_cited_document",
        "server_status",
        "get_pdf_content",
        "check_retractions",
        "get_citation_graph",
        "check_published_versions",
    }
    actual = {t.name for t in tools}
    missing = expected - actual
    assert not missing, f"Missing tools: {missing}"
    assert len(tools) == 28


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
        result = srv._get_local()
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
