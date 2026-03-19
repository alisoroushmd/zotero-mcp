"""Tests for MCP server tool registration."""

import asyncio


def test_server_has_all_tools():
    """Server exposes all 10 tools."""
    from zotero_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    expected = {
        "search_items",
        "get_item",
        "get_collections",
        "get_collection_items",
        "create_item_from_identifier",
        "add_to_collection",
        "update_item",
        "write_cited_document",
        "create_item_from_url",
        "create_item_manual",
    }
    actual = {t.name for t in tools}
    missing = expected - actual
    assert not missing, f"Missing tools: {missing}"
    assert len(tools) == 10
