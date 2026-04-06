# Trash Management + Citation Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `trash_items` and `empty_trash` tools for library cleanup, and `get_citation_graph` tool for discovering citing/referenced works via OpenAlex (with `in_library` flag).

**Architecture:** Trash uses Zotero Web API DELETE endpoints. Citation graph extends the OpenAlexClient (created in Feature 3) with `get_citing_works` and `get_references`. The `in_library` flag reuses existing `_check_duplicate_doi`.

**Tech Stack:** httpx (existing), OpenAlexClient (from Feature 3), no new dependencies.

**Prerequisite:** Feature 3 must be built first (creates `openalex_client.py`).

---

### Task 1: Add trash methods to WebClient

**Files:**
- Modify: `src/zotero_mcp/web_client.py`
- Test: `tests/test_trash.py` (new)

- [ ] **Step 1: Write failing tests for trash_items**

Create `tests/test_trash.py`:

```python
"""Tests for trash management — trash_items and empty_trash."""

import httpx
import pytest
import respx

from zotero_mcp.web_client import WEB_BASE, WebClient

USER_ID = "12345"
API_KEY = "testapikey"
BASE = f"{WEB_BASE}/users/{USER_ID}"


def _make_client() -> WebClient:
    return WebClient(api_key=API_KEY, user_id=USER_ID)


@respx.mock
def test_trash_items_single():
    """trash_items moves a single item to trash."""
    # Need to read version first
    respx.get(f"{BASE}/items/ABC123").mock(
        return_value=httpx.Response(
            200,
            json={"key": "ABC123", "data": {"key": "ABC123", "version": 10}},
            headers={"Last-Modified-Version": "10"},
        )
    )
    respx.delete(f"{BASE}/items").mock(
        return_value=httpx.Response(204, headers={"Last-Modified-Version": "11"})
    )
    client = _make_client()
    result = client.trash_items(["ABC123"])
    assert "ABC123" in result["trashed"]
    assert result["failed"] == []


@respx.mock
def test_trash_items_batch_chunking():
    """trash_items chunks >50 keys into multiple requests."""
    keys = [f"KEY{i:04d}" for i in range(55)]
    # Mock version read for all items
    respx.get(url__regex=rf"{BASE}/items\?.*").mock(
        return_value=httpx.Response(
            200,
            headers={"Last-Modified-Version": "100"},
        )
    )
    # Mock delete (called twice: 50 + 5)
    delete_route = respx.delete(f"{BASE}/items").mock(
        return_value=httpx.Response(204, headers={"Last-Modified-Version": "101"})
    )
    client = _make_client()
    result = client.trash_items(keys)
    assert len(result["trashed"]) == 55
    assert delete_route.call_count == 2


@respx.mock
def test_empty_trash():
    """empty_trash permanently deletes all trashed items."""
    # Need library version
    respx.get(f"{BASE}/items").mock(
        return_value=httpx.Response(
            200,
            json=[],
            headers={"Last-Modified-Version": "50"},
        )
    )
    respx.delete(f"{BASE}/items/trash").mock(
        return_value=httpx.Response(204)
    )
    client = _make_client()
    result = client.empty_trash()
    assert result["status"] == "emptied"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trash.py -v`
Expected: FAIL with `AttributeError: 'WebClient' object has no attribute 'trash_items'`

- [ ] **Step 3: Implement `trash_items` and `empty_trash`**

Add to `src/zotero_mcp/web_client.py` in the `WebClient` class, after `update_item`:

```python
def trash_items(self, item_keys: list[str]) -> dict:
    """Move items to Zotero trash (reversible).

    Uses DELETE /items?itemKey=KEY1,KEY2 with version header.
    Chunks into batches of 50 (Zotero API limit).

    Args:
        item_keys: List of Zotero item keys to trash.

    Returns:
        Dict with trashed and failed key lists.
    """
    trashed: list[str] = []
    failed: list[str] = []

    # Get current library version
    resp = self._web_client.get("/items", params={"limit": 0})
    version = resp.headers.get("Last-Modified-Version", "0")

    # Chunk into batches of 50
    for i in range(0, len(item_keys), 50):
        batch = item_keys[i:i + 50]
        key_param = ",".join(k.strip() for k in batch)
        try:
            resp = self._web_client.delete(
                "/items",
                params={"itemKey": key_param},
                headers={"If-Unmodified-Since-Version": str(version)},
            )
            resp.raise_for_status()
            version = resp.headers.get("Last-Modified-Version", version)
            trashed.extend(batch)
        except Exception:
            failed.extend(batch)

    return {"trashed": trashed, "failed": failed}

def empty_trash(self) -> dict:
    """Permanently delete all items in Zotero trash.

    This is irreversible. The calling tool should confirm with the user
    before invoking this method.

    Returns:
        Dict with status.
    """
    # Get current library version
    resp = self._web_client.get("/items", params={"limit": 0})
    version = resp.headers.get("Last-Modified-Version", "0")

    resp = self._web_client.delete(
        "/items/trash",
        headers={"If-Unmodified-Since-Version": str(version)},
    )
    resp.raise_for_status()
    return {"status": "emptied"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_trash.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/zotero_mcp/web_client.py tests/test_trash.py
git commit -m "feat: add trash_items and empty_trash to WebClient"
```

---

### Task 2: Add trash tools to server.py

**Files:**
- Modify: `src/zotero_mcp/server.py`
- Modify: `src/zotero_mcp/capabilities.py`

- [ ] **Step 1: Add `trash_items` and `empty_trash` tools**

Add to `src/zotero_mcp/server.py`, after the `update_item` tool:

```python
@mcp.tool(
    description=(
        "Move Zotero items to the trash (reversible). Items can be "
        "recovered in Zotero desktop until the trash is emptied. "
        "Accepts one or more item keys."
    )
)
def trash_items(item_keys: str | list[str]) -> str:
    """Move items to Zotero trash.

    Args:
        item_keys: Single key or list of Zotero item keys to trash.

    Returns:
        JSON with trashed and failed key lists.
    """
    keys = _parse_list_param(item_keys) or []
    if not keys:
        raise ValueError("item_keys must not be empty")
    for k in keys:
        _validate_key(k, "item_key")
    result = _get_web().trash_items([k.strip() for k in keys])
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description=(
        "Permanently delete ALL items in the Zotero trash. This is "
        "IRREVERSIBLE. Always confirm with the user before calling this tool. "
        "Use trash_items first to move items to trash, review them, then "
        "empty the trash only when confirmed."
    )
)
def empty_trash() -> str:
    """Permanently delete all trashed items."""
    result = _get_web().empty_trash()
    return json.dumps(result, ensure_ascii=False)
```

- [ ] **Step 2: Update capabilities.py TOOL_MODES**

Add to `TOOL_MODES` dict in `src/zotero_mcp/capabilities.py`:

```python
"trash_items": ["cloud_crud"],
"empty_trash": ["cloud_crud"],
```

- [ ] **Step 3: Update test_server.py**

Add `"trash_items"` and `"empty_trash"` to the `expected` set and update the tool count.

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/zotero_mcp/server.py src/zotero_mcp/capabilities.py tests/test_server.py
git commit -m "feat: add trash_items and empty_trash tools to server"
```

---

### Task 3: Add citation graph methods to OpenAlexClient

**Files:**
- Modify: `src/zotero_mcp/openalex_client.py`
- Test: `tests/test_citation_graph.py` (new)

- [ ] **Step 1: Write failing tests for get_citing_works and get_references**

Create `tests/test_citation_graph.py`:

```python
"""Tests for citation graph — citing works and references via OpenAlex."""

import httpx
import respx

from zotero_mcp.openalex_client import OPENALEX_BASE, OpenAlexClient


@respx.mock
def test_get_citing_works_returns_list():
    """get_citing_works returns recent citing papers."""
    # First, get the work to find cited_by_api_url
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/test").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W12345",
                "doi": "https://doi.org/10.1234/test",
                "title": "Original Paper",
                "is_retracted": False,
                "cited_by_count": 2,
                "cited_by_api_url": f"{OPENALEX_BASE}/works?filter=cites:W12345",
                "referenced_works": [],
            },
        )
    )
    # Then, fetch citing works
    respx.get(
        f"{OPENALEX_BASE}/works",
        params__contains={"filter": "cites:W12345"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W99999",
                        "doi": "https://doi.org/10.5678/citing",
                        "title": "Citing Paper",
                        "publication_year": 2025,
                        "authorships": [
                            {
                                "author": {
                                    "display_name": "Smith J",
                                },
                            }
                        ],
                    }
                ],
            },
        )
    )
    client = OpenAlexClient()
    results = client.get_citing_works("10.1234/test", limit=10)
    assert len(results) == 1
    assert results[0]["title"] == "Citing Paper"
    assert results[0]["doi"] == "10.5678/citing"
    assert results[0]["year"] == 2025


@respx.mock
def test_get_references_returns_list():
    """get_references returns papers cited by the target."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/test").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W12345",
                "doi": "https://doi.org/10.1234/test",
                "title": "Original Paper",
                "is_retracted": False,
                "cited_by_count": 0,
                "cited_by_api_url": "",
                "referenced_works": [
                    "https://openalex.org/W88888",
                ],
            },
        )
    )
    # Fetch referenced work details
    respx.get(f"{OPENALEX_BASE}/works/W88888").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W88888",
                "doi": "https://doi.org/10.9999/referenced",
                "title": "Referenced Paper",
                "publication_year": 2020,
                "authorships": [
                    {"author": {"display_name": "Lee A"}},
                ],
            },
        )
    )
    client = OpenAlexClient()
    results = client.get_references("10.1234/test")
    assert len(results) == 1
    assert results[0]["title"] == "Referenced Paper"
    assert results[0]["doi"] == "10.9999/referenced"


@respx.mock
def test_get_citing_works_returns_empty_for_unknown_doi():
    """get_citing_works returns empty list when DOI not found."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/unknown").mock(
        return_value=httpx.Response(404)
    )
    client = OpenAlexClient()
    results = client.get_citing_works("10.1234/unknown", limit=10)
    assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_citation_graph.py -v`
Expected: FAIL with `AttributeError: 'OpenAlexClient' object has no attribute 'get_citing_works'`

- [ ] **Step 3: Implement `get_citing_works` and `get_references`**

Add to `src/zotero_mcp/openalex_client.py` in the `OpenAlexClient` class, after `get_work`:

```python
def _format_work_summary(self, work: dict) -> dict:
    """Extract key fields from an OpenAlex work for display.

    Args:
        work: Raw OpenAlex work dict.

    Returns:
        Compact summary with title, doi, year, authors.
    """
    doi = (work.get("doi") or "").replace("https://doi.org/", "")
    authorships = work.get("authorships", [])
    authors = "; ".join(
        a.get("author", {}).get("display_name", "")
        for a in authorships[:3]
    )
    if len(authorships) > 3:
        authors += " et al."
    return {
        "openalex_id": work.get("id", ""),
        "title": work.get("title", ""),
        "doi": doi,
        "year": work.get("publication_year"),
        "authors": authors,
    }

def get_citing_works(self, doi: str, limit: int = 20) -> list[dict]:
    """Get works that cite the given DOI.

    Args:
        doi: DOI of the target paper.
        limit: Max number of citing works to return.

    Returns:
        List of work summary dicts, sorted by recency.
    """
    work = self.get_work(doi)
    if not work:
        return []

    cited_by_url = work.get("cited_by_api_url", "")
    if not cited_by_url:
        return []

    try:
        # Extract the filter from the URL and use it
        openalex_id = work.get("id", "").split("/")[-1]
        resp = self._client.get(
            "/works",
            params={
                "filter": f"cites:{openalex_id}",
                "sort": "publication_year:desc",
                "per_page": min(limit, 50),
            },
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [self._format_work_summary(w) for w in results]
    except Exception:
        logger.warning("Failed to fetch citing works for DOI %s", doi)
        return []

def get_references(self, doi: str) -> list[dict]:
    """Get works referenced by the given DOI.

    Resolves each referenced_works OpenAlex ID individually.
    Limited to first 20 references to avoid excessive API calls.

    Args:
        doi: DOI of the target paper.

    Returns:
        List of work summary dicts.
    """
    work = self.get_work(doi)
    if not work:
        return []

    ref_ids = work.get("referenced_works", [])[:20]
    results: list[dict] = []

    for ref_url in ref_ids:
        ref_id = ref_url.split("/")[-1]  # e.g. "W88888"
        try:
            resp = self._client.get(f"/works/{ref_id}")
            if resp.status_code == 200:
                results.append(self._format_work_summary(resp.json()))
        except Exception:
            continue

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_citation_graph.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/zotero_mcp/openalex_client.py tests/test_citation_graph.py
git commit -m "feat: add get_citing_works and get_references to OpenAlexClient"
```

---

### Task 4: Add `get_citation_graph` tool to server.py

**Files:**
- Modify: `src/zotero_mcp/server.py`
- Modify: `src/zotero_mcp/capabilities.py`
- Test: `tests/test_citation_graph.py`

- [ ] **Step 1: Write failing test for the server tool**

Add to `tests/test_citation_graph.py`:

```python
import json
from unittest.mock import MagicMock, patch


def test_get_citation_graph_tool_with_in_library_flag():
    """get_citation_graph flags which citing papers are in library."""
    mock_web = MagicMock()
    mock_web.get_item.return_value = {
        "key": "ABC123",
        "title": "My Paper",
        "DOI": "10.1234/mine",
    }
    # First DOI is in library, second is not
    mock_web._check_duplicate_doi.side_effect = [
        {"key": "XYZ789", "title": "Already Have This"},  # in library
        None,  # not in library
    ]

    mock_openalex = MagicMock()
    mock_openalex.get_citing_works.return_value = [
        {"openalex_id": "W1", "title": "In Library Paper", "doi": "10.5678/inlib", "year": 2025, "authors": "A B"},
        {"openalex_id": "W2", "title": "New Paper", "doi": "10.5678/new", "year": 2025, "authors": "C D"},
    ]
    mock_openalex.get_references.return_value = []

    import zotero_mcp.server as srv
    with patch.object(srv, "_get_web", return_value=mock_web), \
         patch("zotero_mcp.server.OpenAlexClient", return_value=mock_openalex):
        result = json.loads(srv.get_citation_graph("ABC123"))

    assert result["cited_by_count"] == 2
    assert result["cited_by"][0]["in_library"] is True
    assert result["cited_by"][0]["zotero_key"] == "XYZ789"
    assert result["cited_by"][1]["in_library"] is False
    assert "zotero_key" not in result["cited_by"][1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_citation_graph.py::test_get_citation_graph_tool_with_in_library_flag -v`
Expected: FAIL with `AttributeError: module 'zotero_mcp.server' has no attribute 'get_citation_graph'`

- [ ] **Step 3: Implement `get_citation_graph` tool**

Add to `src/zotero_mcp/server.py`, after `check_retractions`:

```python
@mcp.tool(
    description=(
        "Get the citation graph for a Zotero item — who cites it and what "
        "it cites. Uses OpenAlex. Each result is flagged with in_library "
        "(true/false) showing whether it already exists in your Zotero. "
        "Use direction='cited_by' to find papers citing your reference, "
        "'references' for papers it cites, or 'both' for the full graph."
    )
)
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
        return json.dumps({
            "item_key": item_key,
            "error": "No DOI on this item — cannot query citation graph",
        })

    openalex = OpenAlexClient()

    def _add_library_flag(works: list[dict]) -> list[dict]:
        for work in works:
            work_doi = work.get("doi", "")
            if work_doi:
                existing = web._check_duplicate_doi(work_doi)
                if existing:
                    work["in_library"] = True
                    work["zotero_key"] = existing["key"]
                else:
                    work["in_library"] = False
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
        result["cited_by"] = _add_library_flag(cited_by)
        result["cited_by_count"] = len(cited_by)

    if direction in ("references", "both"):
        references = openalex.get_references(doi)
        result["references"] = _add_library_flag(references)

    return json.dumps(result, ensure_ascii=False)
```

- [ ] **Step 4: Update capabilities.py TOOL_MODES**

Add to `TOOL_MODES` dict in `src/zotero_mcp/capabilities.py`:

```python
"get_citation_graph": ["cloud_crud"],
```

- [ ] **Step 5: Run all citation graph and trash tests**

Run: `python -m pytest tests/test_citation_graph.py tests/test_trash.py -v`
Expected: All tests PASS

- [ ] **Step 6: Update test_server.py**

Add `"trash_items"`, `"empty_trash"`, and `"get_citation_graph"` to the `expected` set and update the tool count.

- [ ] **Step 7: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/zotero_mcp/server.py src/zotero_mcp/capabilities.py tests/test_citation_graph.py tests/test_server.py
git commit -m "feat: add get_citation_graph tool with in_library flags"
```
