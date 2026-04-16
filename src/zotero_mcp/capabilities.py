"""Capability detection for Zotero MCP operating modes."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ServerCapabilities:
    """Reports which operating modes are available."""

    local_api: bool
    web_api: bool
    local_api_error: str
    web_api_error: str

    @property
    def cloud_crud(self) -> bool:
        """Web API available — all read/write/organize operations work."""
        return self.web_api

    @property
    def local_read(self) -> bool:
        """Local Zotero desktop API available for faster reads."""
        return self.local_api

    @property
    def any_read(self) -> bool:
        """At least one read path available (web or local)."""
        return self.web_api or self.local_api


# Tool-to-mode mapping: what each tool requires.
# "cloud_crud" = web API credentials (primary path for everything)
# "local_read" = optional speedup, NOT required
TOOL_MODES: dict[str, list[str]] = {
    "server_status": [],
    "search_items": ["any_read"],
    "get_item": ["any_read"],
    "get_collections": ["any_read"],
    "get_collection_items": ["any_read"],
    "get_notes": ["any_read"],
    "get_item_attachments": ["any_read"],
    "get_pdf_content": ["any_read"],
    "create_item": ["cloud_crud"],
    "create_item_manual": ["cloud_crud"],
    "create_note": ["cloud_crud"],
    "create_collection": ["cloud_crud"],
    "batch_organize": ["cloud_crud"],
    "find_duplicates": ["cloud_crud"],
    "add_to_collection": ["cloud_crud"],
    "update_item": ["cloud_crud"],
    "trash_items": ["cloud_crud"],
    "empty_trash": ["cloud_crud"],
    "attach_pdf": ["cloud_crud"],
    "write_cited_document": ["cloud_crud"],
    "insert_citations": ["cloud_crud"],
    "check_retractions": ["cloud_crud"],
    "get_citation_graph": ["cloud_crud"],
    "get_tags": ["any_read"],
    "remove_tag": ["cloud_crud"],
    "rename_tag": ["cloud_crud"],
    "check_published_versions": ["cloud_crud"],
    "build_knowledge_graph": ["cloud_crud"],
    "query_knowledge_graph": ["any_read"],
    "find_related_papers": ["cloud_crud"],
    "query_authors": ["any_read"],
    "export_knowledge_graph": ["any_read"],
    "build_fulltext_index": ["cloud_crud"],
    "search_fulltext": ["any_read"],
    "get_unextracted_abstracts": ["any_read"],
    "store_entities": ["cloud_crud"],
    "search_entities": ["any_read"],
}


def check_capabilities() -> ServerCapabilities:
    """Probe available Zotero services and return capability report.

    Checks:
    - Local API: pings localhost:23119 with a 2-second timeout
    - Web API: checks for ZOTERO_API_KEY and ZOTERO_USER_ID env vars
    """
    local_ok = False
    local_error = ""
    try:
        resp = httpx.get(
            "http://localhost:23119/api/users/0/items",
            params={"limit": 1},
            timeout=2.0,
        )
        resp.raise_for_status()
        local_ok = True
    except httpx.ConnectError:
        local_error = (
            "Zotero desktop not running. Reads will use the Web API (slower). "
            "For faster reads, start Zotero and enable: Settings > Advanced > "
            "General > 'Allow other applications on this computer to communicate "
            "with Zotero'"
        )
    except Exception as e:
        local_error = f"Zotero local API error: {e}"

    web_ok = False
    web_error = ""
    api_key = os.environ.get("ZOTERO_API_KEY", "")
    user_id = os.environ.get("ZOTERO_USER_ID", "")
    if api_key and user_id:
        web_ok = True
    else:
        missing = []
        if not api_key:
            missing.append("ZOTERO_API_KEY")
        if not user_id:
            missing.append("ZOTERO_USER_ID")
        web_error = (
            f"Missing environment variable(s): {', '.join(missing)}. "
            f"Get your API key at https://www.zotero.org/settings/keys"
        )

    openalex_key = os.environ.get("OPENALEX_API_KEY", "")
    if not openalex_key:
        logger.warning(
            "OPENALEX_API_KEY not set — knowledge graph, citation graph, "
            "and retraction checks may fail"
        )

    return ServerCapabilities(
        local_api=local_ok,
        web_api=web_ok,
        local_api_error=local_error,
        web_api_error=web_error,
    )


def format_status(caps: ServerCapabilities) -> dict:
    """Format capabilities as a structured status report for the LLM."""
    modes = {
        "cloud_crud": {
            "available": caps.cloud_crud,
            "description": (
                "Primary mode — all tools work via Zotero Web API "
                "(reads, writes, citations, attachments)"
            ),
            "requires": "ZOTERO_API_KEY + ZOTERO_USER_ID environment variables",
        },
        "local_read": {
            "available": caps.local_read,
            "description": (
                "Optional speedup — reads use local Zotero desktop API "
                "(faster, no rate limits)"
            ),
            "requires": "Zotero 7 desktop running with local API enabled",
        },
    }

    if not caps.cloud_crud:
        modes["cloud_crud"]["fix"] = caps.web_api_error
    if not caps.local_read:
        modes["local_read"]["fix"] = caps.local_api_error

    available_tools = []
    unavailable_tools = []
    for tool_name, required_modes in TOOL_MODES.items():
        tool_available = all(getattr(caps, mode, False) for mode in required_modes)
        entry = {"name": tool_name, "modes": required_modes}
        if tool_available:
            available_tools.append(entry)
        else:
            unavailable_tools.append(entry)

    return {
        "modes": modes,
        "available_tools": available_tools,
        "unavailable_tools": unavailable_tools,
    }
