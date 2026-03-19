"""MCP server exposing Zotero tools via FastMCP."""

import json
import logging
import os
import threading

from fastmcp import FastMCP

from zotero_mcp.local_client import LocalClient
from zotero_mcp.web_client import WebClient

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "zotero",
    instructions=(
        "Zotero MCP server. Read operations use the local Zotero desktop API "
        "(Zotero must be running). Write operations use the Zotero Web API "
        "(requires ZOTERO_API_KEY and ZOTERO_USER_ID env vars)."
    ),
)

_local: LocalClient | None = None
_web: WebClient | None = None
_init_lock = threading.Lock()


def _get_local() -> LocalClient:
    """Lazy-initialize the local client (thread-safe)."""
    global _local
    if _local is None:
        with _init_lock:
            if _local is None:
                _local = LocalClient()
    return _local


def _get_web() -> WebClient:
    """Lazy-initialize the web client (thread-safe)."""
    global _web
    if _web is not None:
        return _web

    with _init_lock:
        if _web is not None:
            return _web
        api_key = os.environ.get("ZOTERO_API_KEY", "")
        user_id = os.environ.get("ZOTERO_USER_ID", "")
        if not api_key or not user_id:
            raise RuntimeError(
                "ZOTERO_API_KEY and ZOTERO_USER_ID are required for write operations. "
                "Get your API key at https://www.zotero.org/settings/keys"
            )
        _web = WebClient(api_key=api_key, user_id=user_id, local_client=_get_local())
        return _web


def _parse_list_param(value: str | list | None) -> list | None:
    """Parse a parameter that may be a JSON string, list, or None."""
    if value is None:
        return None
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [value]
    except (json.JSONDecodeError, TypeError):
        return [value]  # Treat bare string as single-item list


# -- Read tools (local API) --


@mcp.tool(description="Search items in Zotero library by keyword")
def search_items(query: str, limit: int = 25) -> str:
    """Search for items by keyword. Excludes attachments and notes."""
    results = _get_local().search_items(query, limit)
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(description="Get detailed metadata for a single Zotero item")
def get_item(item_key: str, format: str = "json") -> str:
    """Get full metadata or BibTeX for one item by its key."""
    result = _get_local().get_item(item_key, fmt=format)
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(description="List all collections in the Zotero library")
def get_collections() -> str:
    """Returns flat list of collections with key, name, parent, and item count."""
    results = _get_local().get_collections()
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(description="List items in a specific Zotero collection")
def get_collection_items(collection_key: str, limit: int = 100) -> str:
    """Get items within a collection by its key."""
    results = _get_local().get_collection_items(collection_key, limit)
    return json.dumps(results, ensure_ascii=False)


# -- Write tools (web API) --


@mcp.tool(
    description=(
        "Create a Zotero item from a PMID, DOI, or PubMed URL. "
        "Resolves metadata automatically via Zotero's translation server."
    )
)
def create_item_from_identifier(
    identifier: str,
    collection_keys: str | list[str] | None = None,
    tags: str | list[str] | None = None,
) -> str:
    """Look up identifier, create item in Zotero. Returns {key, title}."""
    collection_keys = _parse_list_param(collection_keys)
    tags = _parse_list_param(tags)
    result = _get_web().create_item_from_identifier(identifier, collection_keys, tags)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description=(
        "Create a Zotero item from a URL (web page, FDA document, preprint, "
        "dataset documentation, etc.). Scrapes metadata when possible, "
        "falls back to a basic webpage item with the URL."
    )
)
def create_item_from_url(
    url: str,
    title: str | None = None,
    collection_keys: str | list[str] | None = None,
    tags: str | list[str] | None = None,
) -> str:
    """Create a Zotero item from any URL."""
    if isinstance(collection_keys, str):
        collection_keys = json.loads(collection_keys)
    if isinstance(tags, str):
        tags = json.loads(tags)
    result = _get_web().create_item_from_url(url, title, collection_keys, tags)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description=(
        "Create a Zotero item with manually provided metadata. "
        "Use when no DOI/PMID/URL can resolve the item. Claude populates "
        "fields from context (web search results, user input, etc.). "
        "Supports all Zotero item types: journalArticle, report, webpage, "
        "document, statute, hearing, book, bookSection, etc."
    )
)
def create_item_manual(
    item_type: str,
    title: str,
    creators: str | list[dict] | None = None,
    date: str = "",
    url: str = "",
    doi: str = "",
    publication_title: str = "",
    volume: str = "",
    issue: str = "",
    pages: str = "",
    publisher: str = "",
    abstract: str = "",
    extra: str = "",
    collection_keys: str | list[str] | None = None,
    tags: str | list[str] | None = None,
) -> str:
    """Create a Zotero item with manual metadata."""
    creators = _parse_list_param(creators)
    if isinstance(collection_keys, str):
        collection_keys = json.loads(collection_keys)
    if isinstance(tags, str):
        tags = json.loads(tags)
    result = _get_web().create_item_manual(
        item_type=item_type,
        title=title,
        creators=creators,
        date=date,
        url=url,
        doi=doi,
        publication_title=publication_title,
        volume=volume,
        issue=issue,
        pages=pages,
        publisher=publisher,
        abstract=abstract,
        extra=extra,
        collection_keys=collection_keys,
        tags=tags,
    )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description=(
        "Add tags and/or a collection to multiple Zotero items at once. "
        "Before calling this, use get_item on each item to read the abstract "
        "and title, then suggest appropriate tags based on the content. "
        "Ask the user to approve the suggested tags before applying."
    )
)
def batch_organize(
    item_keys: str | list[str],
    tags: str | list[str] | None = None,
    collection_key: str | None = None,
) -> str:
    """Bulk-add tags and/or collection to multiple items."""
    item_keys = _parse_list_param(item_keys) or []
    tags = _parse_list_param(tags)
    result = _get_web().batch_organize(item_keys, tags, collection_key)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description="Create a new collection (folder) in Zotero. Optionally nest it under a parent collection."
)
def create_collection(name: str, parent_key: str | None = None) -> str:
    """Create a collection. Returns the new collection key."""
    result = _get_web().create_collection(name, parent_key)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(description="Add a Zotero item to a collection")
def add_to_collection(item_key: str, collection_key: str) -> str:
    """Add an existing item to a collection."""
    result = _get_web().add_to_collection(item_key, collection_key)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(description="Update metadata fields on an existing Zotero item")
def update_item(item_key: str, fields: dict) -> str:
    """Update item fields. Uses optimistic locking with version check."""
    result = _get_web().update_item(item_key, fields)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description=(
        "Attach a PDF to a Zotero item. Automatically finds free PDFs via "
        "Unpaywall, PubMed Central, or bioRxiv/medRxiv using the item's DOI. "
        "If no free PDF is found, returns a message asking the user to provide "
        "the file path. Use pdf_path to attach a user-provided local PDF."
    )
)
def attach_pdf(
    parent_key: str,
    pdf_path: str | None = None,
    doi: str | None = None,
) -> str:
    """Attach a PDF to a Zotero item.

    Args:
        parent_key: Zotero item key to attach the PDF to.
        pdf_path: Local file path to a PDF. If None, tries auto-download.
        doi: DOI to search for free PDF. If None, reads from the item.

    Returns:
        JSON with status, attachment_key, filename, source.
    """
    result = _get_web().attach_pdf(parent_key, pdf_path, doi)
    return json.dumps(result, ensure_ascii=False)


# -- Document tools --


@mcp.tool(
    description=(
        "Write a Word document (.docx) with live Zotero citations. "
        "Use [@ITEM_KEY] markers in the content to insert citations. "
        "Supports grouped citations like [@KEY1, @KEY2]. "
        "Produces Vancouver-style superscript numbers and a bibliography. "
        "The Zotero Word plugin will recognize these as live citations. "
        "Zotero desktop must be running to fetch item metadata."
    )
)
def write_cited_document(content: str, output_path: str) -> str:
    """Write a Word document with live Zotero field codes.

    Args:
        content: Markdown text with [@ITEM_KEY] citation markers.
        output_path: Where to save the .docx file.

    Returns:
        JSON with output_path and citation_count.
    """
    from zotero_mcp.citation_writer import build_document, parse_citations

    # Extract all unique item keys from the content
    _, key_to_number = parse_citations(content)
    item_keys = list(key_to_number.keys())

    if not item_keys:
        # No citations — just build a plain document
        result_path = build_document(content, {}, "", output_path)
        return json.dumps({"output_path": result_path, "citation_count": 0})

    # Fetch metadata for each item from local Zotero (parallel)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    local = _get_local()
    item_data: dict[str, dict] = {}
    missing_keys: list[str] = []

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(local.get_item, key): key for key in item_keys}
        for future in as_completed(futures):
            key = futures[future]
            try:
                data = future.result()
                if isinstance(data, dict):
                    item_data[key] = data
                else:
                    missing_keys.append(key)
            except Exception:
                missing_keys.append(key)

    # Get user ID for URI construction
    user_id = os.environ.get("ZOTERO_USER_ID", "0")

    # Build the document
    result_path = build_document(content, item_data, user_id, output_path)

    result = {
        "output_path": result_path,
        "citation_count": len(item_data),
    }
    if missing_keys:
        result["missing_keys"] = missing_keys
        result["warning"] = (
            f"Could not fetch metadata for {len(missing_keys)} item(s): "
            f"{', '.join(missing_keys)}. These citations were skipped."
        )
    return json.dumps(result, ensure_ascii=False)
