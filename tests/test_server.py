"""Tests for MCP server tool registration."""

import asyncio


def test_server_has_all_tools():
    """Server exposes all 20 tools."""
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
        "attach_pdf",
        "insert_citations",
        "write_cited_document",
        "server_status",
        "get_pdf_content",
    }
    actual = {t.name for t in tools}
    missing = expected - actual
    assert not missing, f"Missing tools: {missing}"
    assert len(tools) == 20
