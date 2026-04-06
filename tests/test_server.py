"""Tests for MCP server tool registration and timeout handling."""

import asyncio
from unittest.mock import patch

import pytest


def test_server_has_all_tools():
    """Server exposes all 24 tools."""
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
        "attach_pdf",
        "insert_citations",
        "write_cited_document",
        "server_status",
        "get_pdf_content",
        "check_retractions",
        "get_citation_graph",
    }
    actual = {t.name for t in tools}
    missing = expected - actual
    assert not missing, f"Missing tools: {missing}"
    assert len(tools) == 24


def test_read_local_or_web_httpx_timeout():
    """Web fallback converts httpx.TimeoutException to RuntimeError."""
    import httpx

    import zotero_mcp.server as srv

    def _timeout_method(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    with (
        patch.object(srv, "_local_failed", True),
        patch.object(srv, "_get_web") as mock_web,
    ):
        mock_web.return_value.search_items = _timeout_method

        with pytest.raises(RuntimeError, match="timed out.*ReadTimeout"):
            srv._read_local_or_web("search_items", "test", 10)
