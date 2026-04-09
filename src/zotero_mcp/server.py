"""MCP server exposing Zotero tools via FastMCP."""

import atexit
import functools
import json
import logging
import os
import re
import tempfile
import threading
import time
import httpx
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
_local_failed_at: float | None = None  # time.monotonic() when probe last failed
_LOCAL_RETRY_INTERVAL = 300.0  # retry local API probe every 5 minutes
_web: WebClient | None = None
_init_lock = threading.Lock()

# Temp file tracking for cleanup at exit.
_temp_files: list[str] = []
_temp_lock = threading.Lock()


def _register_temp_file(path: str) -> None:
    """Register a temporary file for cleanup at process exit."""
    with _temp_lock:
        _temp_files.append(path)


def _cleanup_temp_files() -> None:
    """Remove all registered temporary files."""
    with _temp_lock:
        for path in _temp_files:
            try:
                os.unlink(path)
            except OSError:
                pass
        _temp_files.clear()


atexit.register(_cleanup_temp_files)

_ZOTERO_KEY_RE = re.compile(r"^[A-Za-z0-9]+$")

# Directories allowed for file read/write operations.
_ALLOWED_PATH_ROOTS: list[str] = []


def _get_allowed_path_roots() -> list[str]:
    """Return the set of directories allowed for file I/O.

    Includes the current working directory, user home, and system temp dir.
    Computed lazily and cached.
    """
    if not _ALLOWED_PATH_ROOTS:
        import pathlib
        candidates = [
            pathlib.Path.cwd(),
            pathlib.Path.home(),
            pathlib.Path(tempfile.gettempdir()),
        ]
        for c in candidates:
            try:
                _ALLOWED_PATH_ROOTS.append(str(c.resolve()))
            except OSError:
                pass
    return _ALLOWED_PATH_ROOTS


def _validate_path(file_path: str, name: str = "path") -> str:
    """Validate that a file path is within allowed directories.

    Resolves the path (following symlinks) and checks it falls under
    the current working directory, user home, or system temp directory.

    Returns the resolved absolute path string.
    """
    import pathlib
    resolved = str(pathlib.Path(file_path).resolve())
    allowed = _get_allowed_path_roots()
    if not any(resolved.startswith(root + os.sep) or resolved == root for root in allowed):
        raise ValueError(
            f"{name} must be within the working directory, home directory, "
            f"or temp directory. Got: {file_path!r}"
        )
    return resolved


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
    """Lazy-initialize the local client with TTL-based failure caching.

    Retries the probe every _LOCAL_RETRY_INTERVAL seconds so that starting
    Zotero desktop mid-session is automatically picked up.
    """
    global _local, _local_failed_at
    now = time.monotonic()
    if (
        _local_failed_at is not None
        and (now - _local_failed_at) < _LOCAL_RETRY_INTERVAL
    ):
        raise RuntimeError("Local API unavailable (cached)")
    if _local is None:
        with _init_lock:
            now = time.monotonic()
            if (
                _local_failed_at is not None
                and (now - _local_failed_at) < _LOCAL_RETRY_INTERVAL
            ):
                raise RuntimeError("Local API unavailable (cached)")
            if _local is None:
                try:
                    _local = LocalClient()
                    _local_failed_at = None  # Reset on successful probe
                    logger.info("Local Zotero API connected")
                except RuntimeError:
                    _local_failed_at = time.monotonic()
                    logger.info(
                        "Local Zotero API unavailable — will retry in %.0fs",
                        _LOCAL_RETRY_INTERVAL,
                    )
                    raise
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


def _error_response(code: str, message: str, **extra) -> dict:
    """Build a structured error dict for MCP tool return values.

    Args:
        code: Short machine-readable error code (e.g. "invalid_key").
        message: Human-readable explanation.
        **extra: Additional fields to include in the response.

    Returns:
        Dict with "error", "message", and any extra keys.
    """
    return {"error": code, "message": message, **extra}


def _handle_tool_errors(fn):
    """Decorator that converts common exceptions to structured JSON error responses.

    Catches ValueError (bad input), RuntimeError (unavailable/config), and
    httpx.HTTPStatusError (API errors), and returns a JSON-encoded error dict
    instead of raising so the LLM receives a readable error message.
    """

    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            return json.dumps(_error_response("invalid_input", str(exc)))
        except httpx.HTTPStatusError as exc:
            return json.dumps(
                _error_response(
                    "api_error",
                    f"Zotero API returned {exc.response.status_code}",
                    status_code=exc.response.status_code,
                )
            )
        except RuntimeError as exc:
            return json.dumps(_error_response("unavailable", str(exc)))

    return _wrapper


# -- Status tool --


@mcp.tool(
    description="Check available operating modes and fix instructions",
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
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
        try:
            return getattr(_get_web(), local_method)(*args, **kwargs)
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"Zotero Web API timed out ({exc.__class__.__name__}). "
                "Try a more specific query, reduce the limit, or start "
                "Zotero desktop for faster local searches."
            ) from exc


@mcp.tool(
    description=(
        "Search items in Zotero library by keyword. "
        "Optionally filter by item_type (e.g. 'journalArticle', 'book') "
        "or tag (exact tag name)."
    ),
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def search_items(
    query: str,
    limit: str | int = 25,
    item_type: str = "",
    tag: str = "",
) -> str:
    """Search for items by keyword. Excludes attachments and notes."""
    results = _read_local_or_web(
        "search_items",
        query,
        _clamp_limit(limit),
        item_type=item_type or None,
        tag=tag or None,
    )
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(
    description="Get detailed metadata for a single Zotero item",
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def get_item(item_key: str, format: str = "json") -> str:
    """Get full metadata or BibTeX for one item by its key."""
    _validate_key(item_key, "item_key")
    result = _read_local_or_web("get_item", item_key.strip(), fmt=format)
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description="List all collections in the Zotero library",
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def get_collections() -> str:
    """Returns flat list of collections with key, name, parent, and item count."""
    results = _read_local_or_web("get_collections")
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(
    description="Get child notes attached to a Zotero item",
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def get_notes(parent_key: str) -> str:
    """Get all notes attached to a parent item."""
    _validate_key(parent_key, "parent_key")
    results = _read_local_or_web("get_notes", parent_key.strip())
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(
    description="List attachments on a Zotero item with availability status",
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
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
                "filename": att.get("filename", ""),
                "contentType": att.get("contentType", ""),
                "availability": link_mode_map.get(link_mode, "metadata_only"),
            }
        )
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(
    description="Route to best full-text source (PMCID, local PDF, DOI)",
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def get_pdf_content(item_key: str) -> str:
    """Route to the best available content source for a Zotero item.

    Args:
        item_key: Zotero item key.

    Returns:
        JSON with content_source and the relevant identifier or path.
    """
    _validate_key(item_key, "item_key")
    item_key = item_key.strip()

    # Read item metadata
    item = _read_local_or_web("get_item", item_key)
    if isinstance(item, str):
        return json.dumps(
            {
                "item_key": item_key,
                "content_source": "error",
                "message": "Could not read item metadata.",
            }
        )

    doi = item.get("DOI", "")
    extra = item.get("extra", "")
    url = item.get("url", "")

    # Step 1: Check for PMCID via PMID
    pmid_match = re.search(r"PMID:\s*(\d+)", extra)
    if pmid_match:
        pmid = pmid_match.group(1)
        try:
            pmcid = _get_web().resolve_pmid_to_pmcid(pmid)
            if pmcid:
                return json.dumps(
                    {
                        "item_key": item_key,
                        "content_source": "pmc",
                        "pmcid": pmcid,
                        "pmid": pmid,
                        "message": "Use PubMed MCP get_full_text_article(pmcid)",
                    }
                )
        except Exception as exc:
            logger.warning(
                "PMCID lookup failed for item %s PMID %s: %s", item_key, pmid, exc
            )

    # Step 2: Check for PDF attachments
    try:
        children = _read_local_or_web("get_children", item_key, item_type="attachment")
    except Exception as exc:
        logger.warning("Failed to list attachments for %s: %s", item_key, exc)
        children = []

    pdf_attachments = [c for c in children if c.get("contentType") == "application/pdf"]

    if pdf_attachments:
        att = pdf_attachments[0]
        att_key = att.get("key", "")

        # Step 3: Try local file path (fastest)
        try:
            local = _get_local()
            local_path = local.get_attachment_path(att_key)
            if local_path:
                return json.dumps(
                    {
                        "item_key": item_key,
                        "content_source": "local_pdf",
                        "pdf_path": local_path,
                        "attachment_key": att_key,
                        "message": "Read this PDF path",
                    }
                )
        except Exception as exc:
            logger.warning(
                "Local attachment path lookup failed for %s: %s", att_key, exc
            )

        # Step 4: Download from web API
        try:
            web = _get_web()
            pdf_bytes = web.download_attachment(att_key)
            tmp = tempfile.NamedTemporaryFile(
                prefix="zotero_mcp_", suffix=".pdf", delete=False
            )
            try:
                tmp.write(pdf_bytes)
                tmp.close()
            except Exception:
                tmp.close()
                os.unlink(tmp.name)
                raise
            _register_temp_file(tmp.name)
            return json.dumps(
                {
                    "item_key": item_key,
                    "content_source": "web_pdf",
                    "pdf_path": tmp.name,
                    "attachment_key": att_key,
                    "message": "Read this PDF path",
                }
            )
        except Exception as exc:
            logger.warning(
                "Web PDF download failed for attachment %s: %s", att_key, exc
            )

    # Step 5: No stored PDF — try free PDF via DOI (Unpaywall / PMC / bioRxiv)
    if doi:
        try:
            pdf_bytes, _, source = _get_web()._download_free_pdf(doi)
            if pdf_bytes:
                tmp = tempfile.NamedTemporaryFile(
                    prefix="zotero_mcp_", suffix=".pdf", delete=False
                )
                try:
                    tmp.write(pdf_bytes)
                    tmp.close()
                except Exception:
                    tmp.close()
                    os.unlink(tmp.name)
                    raise
                _register_temp_file(tmp.name)
                return json.dumps(
                    {
                        "item_key": item_key,
                        "content_source": f"free_pdf_{source}",
                        "pdf_path": tmp.name,
                        "doi": doi,
                        "message": "Read this PDF path",
                    }
                )
        except Exception as exc:
            logger.warning("Free PDF download failed for DOI %s: %s", doi, exc)

    # Step 6: No PDF found anywhere, return DOI/URL fallback
    result: dict = {
        "item_key": item_key,
        "content_source": "not_found",
        "message": "No PDF attached or available open-access. Try DOI or ask user for the file.",
    }
    if doi:
        result["doi"] = doi
    if url:
        result["url"] = url
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description="Check items for retractions/corrections via CrossRef + OpenAlex",
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def check_retractions(item_keys: str | list[str]) -> str:
    """Batch check items for retractions and corrections.

    Args:
        item_keys: Single key or list of Zotero item keys.

    Returns:
        JSON with per-item retraction/correction status and summary counts.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from zotero_mcp.openalex_client import OpenAlexClient

    keys = _parse_list_param(item_keys) or []
    if not keys:
        raise ValueError("item_keys must not be empty")
    for k in keys:
        _validate_key(k, "item_key")

    web = _get_web()
    openalex = OpenAlexClient()

    results = []
    retracted_count = 0
    corrected_count = 0

    def _check_one(key: str) -> dict:
        item = web.get_item(key.strip())
        if isinstance(item, str):
            return {"key": key, "error": "Could not read item"}

        doi = item.get("DOI", "")
        title = item.get("title", "")

        entry: dict = {"key": key, "title": title, "retracted": False}

        if not doi:
            entry["warning"] = "No DOI — cannot check retraction status"
            return entry

        entry["doi"] = doi

        # CrossRef (authoritative for retractions)
        crossref = web.check_crossref_updates(doi)
        if crossref["has_retraction"]:
            entry["retracted"] = True
            entry["retraction_doi"] = crossref["retraction_doi"]
            entry["retraction_date"] = crossref["retraction_date"]
        if crossref["corrections"]:
            entry["corrections"] = crossref["corrections"]

        # OpenAlex (broader context + citation count)
        oa_work = openalex.get_work(doi)
        if oa_work:
            cited_by = oa_work.get("cited_by_count", 0)
            if cited_by:
                entry["cited_by_count"] = cited_by
            # OpenAlex retraction flag as backup
            if oa_work.get("is_retracted") and not entry["retracted"]:
                entry["retracted"] = True
                entry["retraction_source"] = "openalex"

        return entry

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_check_one, k): k for k in keys}
        for future in as_completed(futures):
            entry = future.result()
            results.append(entry)
            if entry.get("retracted"):
                retracted_count += 1
            if entry.get("corrections"):
                corrected_count += 1

    return json.dumps(
        {
            "results": results,
            "checked": len(results),
            "retracted_count": retracted_count,
            "corrected_count": corrected_count,
        },
        ensure_ascii=False,
    )


@mcp.tool(
    description="Get citing/referenced works for an item via OpenAlex",
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def get_citation_graph(
    item_key: str, direction: str = "both", limit: str | int = 20
) -> str:
    """Get citing and/or referenced works for a Zotero item.

    Args:
        item_key: Zotero item key.
        direction: "cited_by", "references", or "both".
        limit: Max results per direction.

    Returns:
        JSON with cited_by and/or references lists, each with in_library flag.
    """
    from zotero_mcp.openalex_client import OpenAlexClient

    _validate_key(item_key, "item_key")
    limit_int = _clamp_limit(limit, lo=1, hi=50)

    web = _get_web()
    item = web.get_item(item_key.strip())
    if isinstance(item, str):
        return json.dumps({"error": "Could not read item metadata"})

    doi = item.get("DOI", "")
    if not doi:
        return json.dumps(
            {
                "item_key": item_key,
                "error": "No DOI on this item — cannot query citation graph",
            }
        )

    openalex = OpenAlexClient()

    def _add_library_flags(works: list[dict]) -> list[dict]:
        """Batch-check library membership using ThreadPoolExecutor."""
        from concurrent.futures import ThreadPoolExecutor

        dois = [w.get("doi", "") for w in works]

        def _check_doi(d: str) -> dict | None:
            return web._check_duplicate_doi(d) if d else None

        with ThreadPoolExecutor(max_workers=5) as pool:
            existing_items = list(pool.map(_check_doi, dois))

        for work, existing in zip(works, existing_items):
            if existing:
                work["in_library"] = True
                work["zotero_key"] = existing["key"]
            else:
                work["in_library"] = False
        return works

    result: dict = {
        "item_key": item_key,
        "doi": doi,
        "title": item.get("title", ""),
    }

    if direction in ("cited_by", "both"):
        cited_by = openalex.get_citing_works(doi, limit_int)
        result["cited_by"] = _add_library_flags(cited_by)
        result["cited_by_count"] = len(cited_by)

    if direction in ("references", "both"):
        references = openalex.get_references(doi)
        result["references"] = _add_library_flags(references)

    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description="List items in a specific Zotero collection",
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def get_collection_items(collection_key: str, limit: str | int = 100) -> str:
    """Get items within a collection by its key."""
    _validate_key(collection_key, "collection_key")
    results = _read_local_or_web(
        "get_collection_items", collection_key.strip(), _clamp_limit(limit)
    )
    return json.dumps(results, ensure_ascii=False)


# -- Write tools (web API) --


@mcp.tool(description="Create a Zotero item from a PMID, DOI, or PubMed URL")
@_handle_tool_errors
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


@mcp.tool(description="Create a Zotero item from a URL (webpage, preprint, etc.)")
@_handle_tool_errors
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


@mcp.tool(description="Create item with manually provided metadata")
@_handle_tool_errors
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


@mcp.tool(description="Create a note attached to a Zotero item (HTML or plain text)")
@_handle_tool_errors
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


@mcp.tool(description="Add tags and/or collection to multiple items at once")
@_handle_tool_errors
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
    description="Scan library for duplicate items (DOI match + title similarity)",
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def find_duplicates(collection_key: str | None = None, limit: str | int = 100) -> str:
    """Find duplicate items in the library or a collection."""
    limit_int = _clamp_limit(limit)
    if collection_key:
        _validate_key(collection_key, "collection_key")
        collection_key = collection_key.strip()
    result = _get_web().find_duplicates(collection_key, limit_int)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description="Create a new collection (folder), optionally nested under a parent"
)
@_handle_tool_errors
def create_collection(name: str, parent_key: str | None = None) -> str:
    """Create a collection. Returns the new collection key."""
    result = _get_web().create_collection(name, parent_key)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(description="Add a Zotero item to a collection")
@_handle_tool_errors
def add_to_collection(item_key: str, collection_key: str) -> str:
    """Add an existing item to a collection."""
    _validate_key(item_key, "item_key")
    _validate_key(collection_key, "collection_key")
    result = _get_web().add_to_collection(item_key.strip(), collection_key.strip())
    return json.dumps(result, ensure_ascii=False)


_ALLOWED_UPDATE_FIELDS = {
    "title", "creators", "date", "DOI", "url", "abstractNote",
    "publicationTitle", "volume", "issue", "pages", "publisher",
    "ISBN", "ISSN", "extra", "tags", "collections", "itemType",
    "bookTitle", "proceedingsTitle", "series", "seriesTitle",
    "language", "rights", "shortTitle", "accessDate", "archive",
    "archiveLocation", "callNumber", "libraryCatalog", "place",
    "numPages", "edition", "numberOfVolumes",
}


@mcp.tool(description="Update metadata fields on an existing Zotero item")
@_handle_tool_errors
def update_item(item_key: str, fields: dict) -> str:
    """Update item fields. Uses optimistic locking with version check."""
    _validate_key(item_key, "item_key")
    disallowed = set(fields.keys()) - _ALLOWED_UPDATE_FIELDS
    if disallowed:
        raise ValueError(
            f"Fields not allowed for update: {', '.join(sorted(disallowed))}. "
            f"Allowed fields: {', '.join(sorted(_ALLOWED_UPDATE_FIELDS))}"
        )
    result = _get_web().update_item(item_key.strip(), fields)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(description="Move items to trash (reversible). Accepts one or more keys.")
@_handle_tool_errors
def trash_items(item_keys: str | list[str]) -> str:
    """Move items to Zotero trash."""
    keys = _parse_list_param(item_keys) or []
    if not keys:
        raise ValueError("item_keys must not be empty")
    for k in keys:
        _validate_key(k, "item_key")
    result = _get_web().trash_items([k.strip() for k in keys])
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description="Permanently delete ALL trashed items (IRREVERSIBLE)",
    annotations={"destructiveHint": True},
)
@_handle_tool_errors
def empty_trash() -> str:
    """Permanently delete all trashed items."""
    result = _get_web().empty_trash()
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description="List all tags in the library, optionally filtered by prefix",
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def get_tags(prefix: str = "") -> str:
    """Return all tags in the library."""
    result = _get_web().get_tags(prefix=prefix or "")
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description="Remove a tag from every item in the library",
    annotations={"destructiveHint": True},
)
@_handle_tool_errors
def remove_tag(tag: str) -> str:
    """Remove a tag from the entire library."""
    if not tag.strip():
        raise ValueError("tag must not be empty")
    result = _get_web().remove_tag(tag.strip())
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(description="Rename a tag across every item in the library")
@_handle_tool_errors
def rename_tag(old_tag: str, new_tag: str) -> str:
    """Rename a tag library-wide."""
    if not old_tag.strip() or not new_tag.strip():
        raise ValueError("old_tag and new_tag must not be empty")
    result = _get_web().rename_tag(old_tag.strip(), new_tag.strip())
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description=(
        "Check if preprints in the library have been formally published in a peer-reviewed journal. "
        "Uses CrossRef (authoritative) and OpenAlex. Reports published DOI, journal name, "
        "and whether the published version is already in the library."
    ),
    annotations={"readOnlyHint": True},
)
@_handle_tool_errors
def check_published_versions(item_keys: str | list[str]) -> str:
    """Check for published journal versions of preprints.

    Args:
        item_keys: Single key or list of Zotero item keys (typically preprints).

    Returns:
        JSON with per-item results and a summary count of items with published versions.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from zotero_mcp.openalex_client import OpenAlexClient

    keys = _parse_list_param(item_keys) or []
    if not keys:
        raise ValueError("item_keys must not be empty")
    for k in keys:
        _validate_key(k, "item_key")

    web = _get_web()
    openalex = OpenAlexClient()

    published_count = 0
    results: list[dict] = []

    def _check_one(key: str) -> dict:
        item = web.get_item(key.strip())
        if isinstance(item, str):
            return {"key": key, "error": "Could not read item"}

        doi = item.get("DOI", "")
        title = item.get("title", "")
        entry: dict = {"key": key, "title": title, "has_published_version": False}

        if not doi:
            entry["warning"] = "No DOI — cannot check for published version"
            return entry

        entry["doi"] = doi

        # CrossRef is authoritative for preprint→article links
        crossref = web.check_crossref_published(doi)
        published_doi = crossref.get("published_doi")

        # OpenAlex for confirmation and journal name
        oa = openalex.check_published_version(doi)
        entry["is_preprint"] = oa.get("is_preprint", doi.startswith("10.1101/"))

        # Use CrossRef DOI if available; fall back to OpenAlex
        if not published_doi and oa.get("published_doi"):
            published_doi = oa["published_doi"]

        if published_doi:
            entry["has_published_version"] = True
            entry["published_doi"] = published_doi
            if oa.get("journal"):
                entry["journal"] = oa["journal"]
            existing = web._check_duplicate_doi(published_doi)
            if existing:
                entry["in_library"] = True
                entry["zotero_key"] = existing.get("key")
            else:
                entry["in_library"] = False

        return entry

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_check_one, k): k for k in keys}
        for future in as_completed(futures):
            entry = future.result()
            results.append(entry)
            if entry.get("has_published_version"):
                published_count += 1

    return json.dumps(
        {
            "results": results,
            "checked": len(results),
            "published_count": published_count,
        },
        ensure_ascii=False,
    )


@mcp.tool(description="Attach a PDF to an item (auto-downloads or accepts local path)")
@_handle_tool_errors
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
    if pdf_path:
        if not pdf_path.lower().endswith(".pdf"):
            raise ValueError("pdf_path must be a .pdf file")
        pdf_path = _validate_path(pdf_path, "pdf_path")
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


@mcp.tool(description="Insert live Zotero citations into an existing .docx")
@_handle_tool_errors
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
    document_path = _validate_path(document_path, "document_path")
    if output_path:
        output_path = _validate_path(output_path, "output_path")

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
    description="Write a .docx with live Zotero citations from markdown + [@KEY] markers"
)
@_handle_tool_errors
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
    output_path = _validate_path(output_path, "output_path")

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
