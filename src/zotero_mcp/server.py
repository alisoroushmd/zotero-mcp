"""MCP server exposing Zotero tools via FastMCP."""

import json
import logging
import os
import re
import threading

from fastmcp import FastMCP

from zotero_mcp.capabilities import check_capabilities, format_status
from zotero_mcp.local_client import LocalClient
from zotero_mcp.web_client import WebClient

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "zotero",
    instructions=(
        "Zotero MCP server. All tools work with just API credentials "
        "(ZOTERO_API_KEY + ZOTERO_USER_ID). If Zotero desktop is also "
        "running, reads are faster via the local API. "
        "Call server_status to check available modes."
    ),
)

_local: LocalClient | None = None
_web: WebClient | None = None
_init_lock = threading.Lock()

_ZOTERO_KEY_RE = re.compile(r"^[A-Za-z0-9]+$")


def _validate_key(value: str, name: str = "key") -> None:
    """Validate a Zotero item/collection key."""
    if not value or not value.strip():
        raise ValueError(f"{name} must not be empty")
    if not _ZOTERO_KEY_RE.match(value.strip()):
        raise ValueError(f"{name} must be alphanumeric, got: {value!r}")


def _clamp_limit(value: str | int, lo: int = 1, hi: int = 100) -> int:
    """Clamp a limit parameter to a safe range."""
    return max(lo, min(hi, int(value)))


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
            missing = []
            if not api_key:
                missing.append("ZOTERO_API_KEY")
            if not user_id:
                missing.append("ZOTERO_USER_ID")
            raise RuntimeError(
                f"Cloud CRUD mode requires {', '.join(missing)}. "
                f"Get your API key at https://www.zotero.org/settings/keys"
            )
        # Try to attach local client for faster reads, but don't fail without it
        local = None
        try:
            local = _get_local()
        except RuntimeError:
            pass  # Zotero desktop not running — web client will use web reads
        _web = WebClient(api_key=api_key, user_id=user_id, local_client=local)
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


# -- Status tool --


@mcp.tool(
    description=(
        "Check which operating modes are available. Call this first to "
        "understand what tools will work. Reports Local Read, Cloud CRUD, "
        "and Live Citation mode status with fix instructions."
    )
)
def server_status() -> str:
    """Probe Zotero services and report available modes and tools."""
    caps = check_capabilities()
    return json.dumps(format_status(caps), ensure_ascii=False)


# -- Read tools (web API primary, local API fallback) --


def _read_local_or_web(local_method: str, *args, **kwargs):
    """Try local API first (faster), fall back to web API for reads."""
    try:
        local = _get_local()
        return getattr(local, local_method)(*args, **kwargs)
    except RuntimeError:
        return getattr(_get_web(), local_method)(*args, **kwargs)


@mcp.tool(description="Search items in Zotero library by keyword")
def search_items(query: str, limit: str | int = 25) -> str:
    """Search for items by keyword. Excludes attachments and notes."""
    results = _read_local_or_web("search_items", query, _clamp_limit(limit))
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(description="Get detailed metadata for a single Zotero item")
def get_item(item_key: str, format: str = "json") -> str:
    """Get full metadata or BibTeX for one item by its key."""
    _validate_key(item_key, "item_key")
    result = _read_local_or_web("get_item", item_key.strip(), fmt=format)
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(description="List all collections in the Zotero library")
def get_collections() -> str:
    """Returns flat list of collections with key, name, parent, and item count."""
    results = _read_local_or_web("get_collections")
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(
    description=(
        "Get child notes attached to a Zotero item. Returns note content "
        "(HTML), tags, and modification date for each note."
    )
)
def get_notes(parent_key: str) -> str:
    """Get all notes attached to a parent item."""
    _validate_key(parent_key, "parent_key")
    results = _read_local_or_web("get_notes", parent_key.strip())
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(
    description=(
        "List attachments on a Zotero item with availability status. "
        "Returns filename, content type, link mode, and whether the "
        "file is available locally, in cloud storage, or metadata-only."
    )
)
def get_item_attachments(parent_key: str) -> str:
    """Get attachments for a parent item with availability classification."""
    _validate_key(parent_key, "parent_key")
    attachments = _read_local_or_web(
        "get_children", parent_key.strip(), item_type="attachment"
    )

    link_mode_map = {
        "imported_url": "stored_remote_available",
        "imported_file": "stored_local_available",
        "linked_file": "linked_local_available",
        "linked_url": "linked_local_available",
    }

    results = []
    for att in attachments:
        link_mode = att.get("linkMode", "")
        results.append(
            {
                "key": att.get("key", ""),
                "title": att.get("title", ""),
                "filename": att.get("filename", ""),
                "contentType": att.get("contentType", ""),
                "linkMode": link_mode,
                "availability": link_mode_map.get(link_mode, "metadata_only"),
                "path": att.get("path", ""),
            }
        )
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(description="List items in a specific Zotero collection")
def get_collection_items(collection_key: str, limit: str | int = 100) -> str:
    """Get items within a collection by its key."""
    _validate_key(collection_key, "collection_key")
    results = _read_local_or_web(
        "get_collection_items", collection_key.strip(), _clamp_limit(limit)
    )
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
    if not identifier or not identifier.strip():
        raise ValueError("identifier must not be empty")
    collection_keys = _parse_list_param(collection_keys)
    tags = _parse_list_param(tags)
    result = _get_web().create_item_from_identifier(
        identifier.strip(), collection_keys, tags
    )
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
    collection_keys = _parse_list_param(collection_keys)
    tags = _parse_list_param(tags)
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
    collection_keys = _parse_list_param(collection_keys)
    tags = _parse_list_param(tags)
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
        "Create a note attached to a Zotero item. Use for annotations, "
        "quality assessments, 'What's New' summaries, or any structured "
        "commentary on a reference. Content can be HTML or plain text."
    )
)
def create_note(
    parent_key: str,
    content: str,
    tags: str | list[str] | None = None,
) -> str:
    """Create a child note on a Zotero item."""
    _validate_key(parent_key, "parent_key")
    tags = _parse_list_param(tags)
    result = _get_web().create_note(parent_key.strip(), content, tags)
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
    _validate_key(item_key, "item_key")
    _validate_key(collection_key, "collection_key")
    result = _get_web().add_to_collection(item_key.strip(), collection_key.strip())
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(description="Update metadata fields on an existing Zotero item")
def update_item(item_key: str, fields: dict) -> str:
    """Update item fields. Uses optimistic locking with version check."""
    _validate_key(item_key, "item_key")
    result = _get_web().update_item(item_key.strip(), fields)
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
    _validate_key(parent_key, "parent_key")
    if pdf_path and not pdf_path.lower().endswith(".pdf"):
        raise ValueError("pdf_path must be a .pdf file")
    result = _get_web().attach_pdf(parent_key.strip(), pdf_path, doi)
    return json.dumps(result, ensure_ascii=False)


# -- Document tools --


def _fetch_item_metadata(item_keys: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Fetch metadata for multiple items in parallel (local fast path, web fallback).

    Returns:
        Tuple of (item_data dict, missing_keys list).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _get_one(key: str) -> dict | str:
        return _read_local_or_web("get_item", key)

    item_data: dict[str, dict] = {}
    missing_keys: list[str] = []

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_get_one, key): key for key in item_keys}
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

    return item_data, missing_keys


@mcp.tool(
    description=(
        "Insert live Zotero citations into an existing Word document (.docx). "
        "Scans paragraphs and tables for [@ITEM_KEY] markers, replaces them "
        "with Zotero field codes, and appends a bibliography. Preserves all "
        "existing document formatting (styles, headers, images, page layout). "
        "Use this instead of write_cited_document when you need to add "
        "citations to a document that already has formatting you want to keep."
    )
)
def insert_citations(document_path: str, output_path: str | None = None) -> str:
    """Insert Zotero citation field codes into an existing Word document.

    Args:
        document_path: Path to existing .docx with [@ITEM_KEY] markers.
        output_path: Where to save. If omitted, overwrites the original.

    Returns:
        JSON with output_path and citation_count.
    """
    if not document_path.lower().endswith(".docx"):
        raise ValueError("document_path must be a .docx file")
    if output_path and not output_path.lower().endswith(".docx"):
        raise ValueError("output_path must be a .docx file")

    from zotero_mcp.citation_writer import insert_citations as _insert_citations
    from zotero_mcp.citation_writer import parse_citations

    # Read the document to find all citation keys
    from docx import Document as _Document

    doc = _Document(document_path)
    all_text: list[str] = []
    for para in doc.paragraphs:
        t = "".join(run.text for run in para.runs)
        if t:
            all_text.append(t)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    t = "".join(run.text for run in para.runs)
                    if t:
                        all_text.append(t)

    combined = "\n\n".join(all_text)
    _, key_to_number = parse_citations(combined)
    item_keys = list(key_to_number.keys())

    if not item_keys:
        return json.dumps(
            {
                "output_path": document_path,
                "citation_count": 0,
                "message": "No [@KEY] citation markers found in the document.",
            }
        )

    # Fetch metadata for each item (local fast path, web fallback)
    item_data, missing_keys = _fetch_item_metadata(item_keys)

    user_id = os.environ.get("ZOTERO_USER_ID", "0")

    result_path, citation_count = _insert_citations(
        document_path, item_data, user_id, output_path
    )

    result = {
        "output_path": result_path,
        "citation_count": citation_count,
    }
    if missing_keys:
        result["missing_keys"] = missing_keys
        result["warning"] = (
            f"Could not fetch metadata for {len(missing_keys)} item(s): "
            f"{', '.join(missing_keys)}. These citations were skipped."
        )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description=(
        "Write a Word document (.docx) with live Zotero citations. "
        "Use [@ITEM_KEY] markers in the content to insert citations. "
        "Supports grouped citations like [@KEY1, @KEY2]. "
        "Produces Vancouver-style superscript numbers and a bibliography. "
        "The Zotero Word plugin will recognize these as live citations."
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
    if not output_path.lower().endswith(".docx"):
        raise ValueError("output_path must be a .docx file")

    from zotero_mcp.citation_writer import build_document, parse_citations

    # Extract all unique item keys from the content
    _, key_to_number = parse_citations(content)
    item_keys = list(key_to_number.keys())

    if not item_keys:
        # No citations — just build a plain document
        result_path = build_document(content, {}, "", output_path)
        return json.dumps({"output_path": result_path, "citation_count": 0})

    # Fetch metadata (local fast path, web fallback)
    item_data, missing_keys = _fetch_item_metadata(item_keys)

    user_id = os.environ.get("ZOTERO_USER_ID", "0")
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
