# Retraction Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `check_retractions` tool that batch-checks items for retractions, corrections, and errata using CrossRef (authoritative) and OpenAlex (broader context + cited_by_count).

**Architecture:** New `OpenAlexClient` class (reused by Feature 5 citation graph). CrossRef retraction check added to existing WebClient. Server tool orchestrates both in parallel.

**Tech Stack:** httpx (existing), no new dependencies.

---

### Task 1: Create OpenAlexClient

**Files:**
- Create: `src/zotero_mcp/openalex_client.py`
- Test: `tests/test_openalex.py` (new)

- [ ] **Step 1: Write failing tests for OpenAlexClient.get_work**

Create `tests/test_openalex.py`:

```python
"""Tests for OpenAlexClient — OpenAlex API wrapper."""

import httpx
import pytest
import respx

from zotero_mcp.openalex_client import OpenAlexClient

OPENALEX_BASE = "https://api.openalex.org"


@respx.mock
def test_get_work_returns_metadata():
    """get_work returns work metadata for a valid DOI."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/test").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W12345",
                "doi": "https://doi.org/10.1234/test",
                "title": "Test Paper",
                "is_retracted": False,
                "cited_by_count": 42,
                "cited_by_api_url": f"{OPENALEX_BASE}/works?filter=cites:W12345",
                "referenced_works": ["https://openalex.org/W99999"],
            },
        )
    )
    client = OpenAlexClient()
    result = client.get_work("10.1234/test")
    assert result is not None
    assert result["title"] == "Test Paper"
    assert result["is_retracted"] is False
    assert result["cited_by_count"] == 42


@respx.mock
def test_get_work_returns_none_for_404():
    """get_work returns None when DOI not found."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/nonexistent").mock(
        return_value=httpx.Response(404)
    )
    client = OpenAlexClient()
    result = client.get_work("10.1234/nonexistent")
    assert result is None


@respx.mock
def test_get_work_returns_retracted_flag():
    """get_work correctly reports retracted papers."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/retracted").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W11111",
                "doi": "https://doi.org/10.1234/retracted",
                "title": "Retracted Paper",
                "is_retracted": True,
                "cited_by_count": 5,
                "cited_by_api_url": "",
                "referenced_works": [],
            },
        )
    )
    client = OpenAlexClient()
    result = client.get_work("10.1234/retracted")
    assert result["is_retracted"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_openalex.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zotero_mcp.openalex_client'`

- [ ] **Step 3: Implement OpenAlexClient**

Create `src/zotero_mcp/openalex_client.py`:

```python
"""OpenAlex API client — citation graph and retraction data."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

OPENALEX_BASE = "https://api.openalex.org"
TIMEOUT = 10.0


class OpenAlexClient:
    """Wrapper for the OpenAlex API.

    Used for retraction checks (Feature 3) and citation graph (Feature 5).
    OpenAlex is free and requires no API key. Polite pool access uses
    an email in the User-Agent header.
    """

    def __init__(self, email: str = "zotero-mcp@example.com") -> None:
        self._client = httpx.Client(
            base_url=OPENALEX_BASE,
            headers={"User-Agent": f"zotero-mcp/1.0 (mailto:{email})"},
            timeout=TIMEOUT,
        )

    def get_work(self, doi: str) -> dict | None:
        """Get work metadata by DOI.

        Args:
            doi: DOI string (e.g. "10.1234/test", without https://doi.org/ prefix).

        Returns:
            OpenAlex work dict, or None if not found.
        """
        doi = doi.strip()
        if doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/"):]
        if doi.startswith("http://doi.org/"):
            doi = doi[len("http://doi.org/"):]

        try:
            resp = self._client.get(f"/works/doi:{doi}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.warning("OpenAlex lookup failed for DOI %s", doi)
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_openalex.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/zotero_mcp/openalex_client.py tests/test_openalex.py
git commit -m "feat: add OpenAlexClient for retraction checks and citation graph"
```

---

### Task 2: Add CrossRef retraction check to WebClient

**Files:**
- Modify: `src/zotero_mcp/web_client.py`
- Test: `tests/test_retractions.py` (new)

- [ ] **Step 1: Write failing tests for check_crossref_updates**

Create `tests/test_retractions.py`:

```python
"""Tests for retraction and correction checks."""

import httpx
import respx

from zotero_mcp.web_client import WebClient, WEB_BASE

USER_ID = "12345"
API_KEY = "testapikey"


def _make_client() -> WebClient:
    return WebClient(api_key=API_KEY, user_id=USER_ID)


@respx.mock
def test_check_crossref_updates_finds_retraction():
    """CrossRef check detects retraction in update-to field."""
    respx.get("https://api.crossref.org/works/10.1234/retracted").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "message": {
                    "DOI": "10.1234/retracted",
                    "title": ["Retracted Paper"],
                    "update-to": [
                        {
                            "type": "retraction",
                            "DOI": "10.1234/retraction-notice",
                            "updated": {"date-parts": [[2025, 3, 15]]},
                            "label": "Retraction",
                        }
                    ],
                },
            },
        )
    )
    client = _make_client()
    result = client.check_crossref_updates("10.1234/retracted")
    assert result["has_retraction"] is True
    assert result["retraction_doi"] == "10.1234/retraction-notice"


@respx.mock
def test_check_crossref_updates_finds_correction():
    """CrossRef check detects erratum in update-to field."""
    respx.get("https://api.crossref.org/works/10.1234/corrected").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "message": {
                    "DOI": "10.1234/corrected",
                    "title": ["Corrected Paper"],
                    "update-to": [
                        {
                            "type": "erratum",
                            "DOI": "10.1234/erratum-notice",
                            "updated": {"date-parts": [[2025, 1, 10]]},
                            "label": "Erratum",
                        }
                    ],
                },
            },
        )
    )
    client = _make_client()
    result = client.check_crossref_updates("10.1234/corrected")
    assert result["has_retraction"] is False
    assert len(result["corrections"]) == 1
    assert result["corrections"][0]["type"] == "erratum"


@respx.mock
def test_check_crossref_updates_clean_paper():
    """CrossRef check returns clean result for paper with no updates."""
    respx.get("https://api.crossref.org/works/10.1234/clean").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "message": {
                    "DOI": "10.1234/clean",
                    "title": ["Clean Paper"],
                },
            },
        )
    )
    client = _make_client()
    result = client.check_crossref_updates("10.1234/clean")
    assert result["has_retraction"] is False
    assert result["corrections"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_retractions.py -v`
Expected: FAIL with `AttributeError: 'WebClient' object has no attribute 'check_crossref_updates'`

- [ ] **Step 3: Implement `check_crossref_updates`**

Add to `src/zotero_mcp/web_client.py` in the `WebClient` class, after `_check_duplicate_title` (or after `_check_duplicate_doi` if Feature 4 hasn't been built yet):

```python
def check_crossref_updates(self, doi: str) -> dict:
    """Check CrossRef for retractions, corrections, and errata.

    Args:
        doi: DOI to check.

    Returns:
        Dict with has_retraction, retraction_doi, retraction_date,
        and corrections list.
    """
    result: dict = {
        "has_retraction": False,
        "retraction_doi": "",
        "retraction_date": "",
        "corrections": [],
    }

    try:
        resp = httpx.get(
            f"https://api.crossref.org/works/{doi}",
            headers={
                "User-Agent": "zotero-mcp/1.0 (mailto:zotero-mcp@example.com)"
            },
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return result
        work = resp.json().get("message", {})
    except Exception:
        return result

    updates = work.get("update-to", [])
    for update in updates:
        update_type = update.get("type", "").lower()
        update_doi = update.get("DOI", "")
        date_parts = update.get("updated", {}).get("date-parts", [[]])
        date_str = "-".join(str(p) for p in date_parts[0]) if date_parts[0] else ""

        if update_type == "retraction":
            result["has_retraction"] = True
            result["retraction_doi"] = update_doi
            result["retraction_date"] = date_str
        else:
            result["corrections"].append({
                "type": update_type,
                "doi": update_doi,
                "date": date_str,
            })

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_retractions.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/zotero_mcp/web_client.py tests/test_retractions.py
git commit -m "feat: add check_crossref_updates for retraction and correction detection"
```

---

### Task 3: Add `check_retractions` tool to server.py

**Files:**
- Modify: `src/zotero_mcp/server.py`
- Modify: `src/zotero_mcp/capabilities.py`
- Test: `tests/test_retractions.py`

- [ ] **Step 1: Write failing test for the server tool**

Add to `tests/test_retractions.py`:

```python
import json
from unittest.mock import MagicMock, patch


def test_check_retractions_tool_merges_crossref_and_openalex():
    """check_retractions tool merges CrossRef and OpenAlex results."""
    mock_web = MagicMock()
    mock_web.get_item.return_value = {
        "key": "ABC123",
        "title": "Test Paper",
        "DOI": "10.1234/test",
    }
    mock_web.check_crossref_updates.return_value = {
        "has_retraction": False,
        "retraction_doi": "",
        "retraction_date": "",
        "corrections": [],
    }

    mock_openalex = MagicMock()
    mock_openalex.get_work.return_value = {
        "is_retracted": False,
        "cited_by_count": 42,
    }

    import zotero_mcp.server as srv
    with patch.object(srv, "_get_web", return_value=mock_web), \
         patch("zotero_mcp.server.OpenAlexClient", return_value=mock_openalex):
        result = json.loads(srv.check_retractions("ABC123"))

    assert result["checked"] == 1
    assert result["retracted_count"] == 0
    assert result["results"][0]["cited_by_count"] == 42


def test_check_retractions_tool_detects_retraction():
    """check_retractions flags retracted papers from CrossRef."""
    mock_web = MagicMock()
    mock_web.get_item.return_value = {
        "key": "DEF456",
        "title": "Bad Paper",
        "DOI": "10.1234/retracted",
    }
    mock_web.check_crossref_updates.return_value = {
        "has_retraction": True,
        "retraction_doi": "10.1234/retraction-notice",
        "retraction_date": "2025-3-15",
        "corrections": [],
    }

    mock_openalex = MagicMock()
    mock_openalex.get_work.return_value = {
        "is_retracted": True,
        "cited_by_count": 5,
    }

    import zotero_mcp.server as srv
    with patch.object(srv, "_get_web", return_value=mock_web), \
         patch("zotero_mcp.server.OpenAlexClient", return_value=mock_openalex):
        result = json.loads(srv.check_retractions("DEF456"))

    assert result["retracted_count"] == 1
    assert result["results"][0]["retracted"] is True
    assert result["results"][0]["retraction_doi"] == "10.1234/retraction-notice"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_retractions.py::test_check_retractions_tool_merges_crossref_and_openalex -v`
Expected: FAIL with `AttributeError: module 'zotero_mcp.server' has no attribute 'check_retractions'`

- [ ] **Step 3: Implement `check_retractions` tool**

Add to `src/zotero_mcp/server.py`, after the read tools section:

```python
@mcp.tool(
    description=(
        "Check Zotero items for retractions, corrections, and errata. "
        "Uses CrossRef (authoritative for retractions) and OpenAlex "
        "(corrections, citation counts). Accepts one or more item keys. "
        "Use this to audit references before submitting manuscripts or grants."
    )
)
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

        entry: dict = {
            "key": key,
            "doi": doi,
            "title": title,
            "retracted": False,
            "retraction_doi": "",
            "retraction_date": "",
            "corrections": [],
            "cited_by_count": 0,
        }

        if not doi:
            entry["warning"] = "No DOI — cannot check retraction status"
            return entry

        # CrossRef (authoritative for retractions)
        crossref = web.check_crossref_updates(doi)
        if crossref["has_retraction"]:
            entry["retracted"] = True
            entry["retraction_doi"] = crossref["retraction_doi"]
            entry["retraction_date"] = crossref["retraction_date"]
        entry["corrections"] = crossref["corrections"]

        # OpenAlex (broader context + citation count)
        oa_work = openalex.get_work(doi)
        if oa_work:
            entry["cited_by_count"] = oa_work.get("cited_by_count", 0)
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

    return json.dumps({
        "results": results,
        "checked": len(results),
        "retracted_count": retracted_count,
        "corrected_count": corrected_count,
    }, ensure_ascii=False)
```

- [ ] **Step 4: Update capabilities.py TOOL_MODES**

Add to `TOOL_MODES` dict in `src/zotero_mcp/capabilities.py`:

```python
"check_retractions": ["cloud_crud"],
```

- [ ] **Step 5: Run all retraction tests**

Run: `python -m pytest tests/test_retractions.py tests/test_openalex.py -v`
Expected: All tests PASS

- [ ] **Step 6: Update test_server.py**

Add `"check_retractions"` to the `expected` set and update the tool count.

- [ ] **Step 7: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/zotero_mcp/server.py src/zotero_mcp/capabilities.py tests/test_retractions.py
git commit -m "feat: add check_retractions tool — CrossRef + OpenAlex retraction/correction checks"
```
