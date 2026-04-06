# PDF Content Locator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `get_pdf_content` tool that routes to the best available content source (PMC structured text, local PDF path, web-downloaded PDF, or DOI fallback).

**Architecture:** Smart content router in server.py delegates to local_client (file path lookup) and web_client (attachment download). Returns metadata the LLM uses with its existing tools (PubMed MCP, Read tool) rather than parsing PDFs itself.

**Tech Stack:** httpx (existing), tempfile (stdlib), no new dependencies.

---

### Task 1: Add `get_attachment_path` to LocalClient

**Files:**
- Modify: `src/zotero_mcp/local_client.py`
- Test: `tests/test_local_client.py`

- [ ] **Step 1: Write the failing test for local attachment path lookup**

Add to `tests/test_local_client.py`:

```python
@respx.mock
def test_get_attachment_path_returns_path():
    """get_attachment_path returns local file path for a stored PDF."""
    respx.get(f"{LOCAL_BASE}/users/0/items/ATT001").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ATT001",
                "data": {
                    "key": "ATT001",
                    "itemType": "attachment",
                    "linkMode": "imported_file",
                    "path": "storage/ATT001/paper.pdf",
                    "contentType": "application/pdf",
                },
            },
        )
    )
    client = LocalClient()
    path = client.get_attachment_path("ATT001")
    assert path == "storage/ATT001/paper.pdf"


@respx.mock
def test_get_attachment_path_returns_none_for_linked_url():
    """get_attachment_path returns None for linked_url attachments (no local file)."""
    respx.get(f"{LOCAL_BASE}/users/0/items/ATT002").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ATT002",
                "data": {
                    "key": "ATT002",
                    "itemType": "attachment",
                    "linkMode": "linked_url",
                    "url": "https://example.com/paper.pdf",
                    "contentType": "application/pdf",
                },
            },
        )
    )
    client = LocalClient()
    path = client.get_attachment_path("ATT002")
    assert path is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_local_client.py::test_get_attachment_path_returns_path tests/test_local_client.py::test_get_attachment_path_returns_none_for_linked_url -v`
Expected: FAIL with `AttributeError: 'LocalClient' object has no attribute 'get_attachment_path'`

- [ ] **Step 3: Implement `get_attachment_path`**

Add to `src/zotero_mcp/local_client.py` in the `LocalClient` class, after `get_notes`:

```python
def get_attachment_path(self, attachment_key: str) -> str | None:
    """Get local file path for an attachment.

    Args:
        attachment_key: Zotero key of the attachment item.

    Returns:
        Local file path string, or None if the attachment has no local file
        (e.g. linked_url attachments).
    """
    resp = self._get(f"/users/0/items/{attachment_key}")
    data = resp.json().get("data", resp.json())
    link_mode = data.get("linkMode", "")
    if link_mode in ("imported_file", "imported_url", "linked_file"):
        return data.get("path", None)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_local_client.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/zotero_mcp/local_client.py tests/test_local_client.py
git commit -m "feat: add get_attachment_path to LocalClient for PDF content locator"
```

---

### Task 2: Add `download_attachment` to WebClient

**Files:**
- Modify: `src/zotero_mcp/web_client.py`
- Test: `tests/test_web_client_pdf.py` (new)

- [ ] **Step 1: Write the failing test for web attachment download**

Create `tests/test_web_client_pdf.py`:

```python
"""Tests for WebClient PDF attachment download."""

import httpx
import respx

from zotero_mcp.web_client import WEB_BASE, WebClient

USER_ID = "12345"
API_KEY = "testapikey"
BASE = f"{WEB_BASE}/users/{USER_ID}"


def _make_client() -> WebClient:
    return WebClient(api_key=API_KEY, user_id=USER_ID)


@respx.mock
def test_download_attachment_returns_bytes():
    """download_attachment returns PDF bytes from Web API."""
    pdf_bytes = b"%PDF-1.4 fake pdf content here"
    respx.get(f"{BASE}/items/ATT001/file").mock(
        return_value=httpx.Response(
            200,
            content=pdf_bytes,
            headers={"Content-Type": "application/pdf"},
        )
    )
    client = _make_client()
    result = client.download_attachment("ATT001")
    assert result == pdf_bytes


@respx.mock
def test_download_attachment_raises_on_404():
    """download_attachment raises RuntimeError when attachment not found."""
    respx.get(f"{BASE}/items/ATT001/file").mock(
        return_value=httpx.Response(404)
    )
    client = _make_client()
    import pytest
    with pytest.raises(httpx.HTTPStatusError):
        client.download_attachment("ATT001")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_client_pdf.py -v`
Expected: FAIL with `AttributeError: 'WebClient' object has no attribute 'download_attachment'`

- [ ] **Step 3: Implement `download_attachment`**

Add to `src/zotero_mcp/web_client.py` in the `WebClient` class, after the `get_notes` method:

```python
def download_attachment(self, attachment_key: str) -> bytes:
    """Download an attachment file from Zotero cloud storage.

    Args:
        attachment_key: Zotero key of the attachment item.

    Returns:
        Raw file bytes.

    Raises:
        httpx.HTTPStatusError: If the download fails (404, 403, etc.).
    """
    resp = self._web_client.get(f"/items/{attachment_key}/file")
    resp.raise_for_status()
    return resp.content
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_client_pdf.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/zotero_mcp/web_client.py tests/test_web_client_pdf.py
git commit -m "feat: add download_attachment to WebClient for PDF content locator"
```

---

### Task 3: Add `get_pdf_content` tool to server.py

**Files:**
- Modify: `src/zotero_mcp/server.py`
- Modify: `src/zotero_mcp/capabilities.py`
- Test: `tests/test_pdf_content.py` (new)

- [ ] **Step 1: Write the failing test for PMC path (best case)**

Create `tests/test_pdf_content.py`:

```python
"""Tests for get_pdf_content tool — content routing logic."""

import json
from unittest.mock import MagicMock, patch

import httpx
import respx


def _mock_web_client(item_data: dict, children: list | None = None):
    """Create a mock WebClient that returns given item data."""
    mock = MagicMock()
    mock.get_item.return_value = item_data
    mock.get_children.return_value = children or []
    mock.download_attachment.return_value = b"%PDF-1.4 fake"
    return mock


def _mock_local_client(attachment_path: str | None = None):
    """Create a mock LocalClient."""
    mock = MagicMock()
    mock.get_item.return_value = {
        "key": "ABC123",
        "title": "Test",
        "DOI": "10.1234/test",
        "extra": "PMID: 12345678",
    }
    mock.get_children.return_value = []
    mock.get_attachment_path.return_value = attachment_path
    return mock


@respx.mock
def test_get_pdf_content_returns_pmcid_when_available():
    """If item has a PMID that maps to a PMCID, return PMC source."""
    # Mock PubMed ID converter
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(
            200,
            json={
                "esearchresult": {
                    "idlist": ["9046468"],
                }
            },
        )
    )

    item_data = {
        "key": "ABC123",
        "title": "Test Paper",
        "DOI": "10.1234/test",
        "extra": "PMID: 12345678",
    }

    mock_web = _mock_web_client(item_data)
    mock_local = _mock_local_client()

    import zotero_mcp.server as srv
    with patch.object(srv, "_get_web", return_value=mock_web), \
         patch.object(srv, "_get_local", return_value=mock_local):
        result = json.loads(srv.get_pdf_content("ABC123"))

    assert result["content_source"] == "pmc"
    assert result["pmcid"] == "PMC9046468"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pdf_content.py::test_get_pdf_content_returns_pmcid_when_available -v`
Expected: FAIL with `AttributeError: module 'zotero_mcp.server' has no attribute 'get_pdf_content'`

- [ ] **Step 3: Write test for local PDF path (second priority)**

Add to `tests/test_pdf_content.py`:

```python
def test_get_pdf_content_returns_local_path():
    """If no PMCID but local PDF exists, return local file path."""
    item_data = {
        "key": "ABC123",
        "title": "Test Paper",
        "DOI": "10.1234/test",
        "extra": "",  # no PMID
    }
    children = [
        {
            "key": "ATT001",
            "itemType": "attachment",
            "contentType": "application/pdf",
            "linkMode": "imported_file",
            "path": "storage/ATT001/paper.pdf",
        }
    ]

    mock_web = _mock_web_client(item_data, children)
    mock_local = _mock_local_client(attachment_path="/Users/test/Zotero/storage/ATT001/paper.pdf")
    mock_local.get_children.return_value = children

    import zotero_mcp.server as srv
    with patch.object(srv, "_get_web", return_value=mock_web), \
         patch.object(srv, "_get_local", return_value=mock_local):
        result = json.loads(srv.get_pdf_content("ABC123"))

    assert result["content_source"] == "local_pdf"
    assert "ATT001" in result["pdf_path"]
```

- [ ] **Step 4: Write test for web download fallback**

Add to `tests/test_pdf_content.py`:

```python
def test_get_pdf_content_downloads_from_web():
    """If no local path, download from web and return temp file path."""
    item_data = {
        "key": "ABC123",
        "title": "Test Paper",
        "DOI": "10.1234/test",
        "extra": "",
    }
    children = [
        {
            "key": "ATT001",
            "itemType": "attachment",
            "contentType": "application/pdf",
            "linkMode": "imported_url",
        }
    ]

    mock_web = _mock_web_client(item_data, children)
    mock_local = MagicMock()
    mock_local.get_item.return_value = item_data
    mock_local.get_children.return_value = children
    mock_local.get_attachment_path.return_value = None

    import zotero_mcp.server as srv
    with patch.object(srv, "_get_web", return_value=mock_web), \
         patch.object(srv, "_get_local", return_value=mock_local):
        result = json.loads(srv.get_pdf_content("ABC123"))

    assert result["content_source"] == "web_pdf"
    assert result["pdf_path"].endswith(".pdf")
```

- [ ] **Step 5: Write test for not-found fallback**

Add to `tests/test_pdf_content.py`:

```python
def test_get_pdf_content_returns_not_found():
    """If no PDF attached and no PMCID, return DOI/URL for manual lookup."""
    item_data = {
        "key": "ABC123",
        "title": "Test Paper",
        "DOI": "10.1234/test",
        "url": "https://example.com/paper",
        "extra": "",
    }

    mock_web = _mock_web_client(item_data, children=[])
    mock_local = _mock_local_client()

    import zotero_mcp.server as srv
    with patch.object(srv, "_get_web", return_value=mock_web), \
         patch.object(srv, "_get_local", return_value=mock_local):
        result = json.loads(srv.get_pdf_content("ABC123"))

    assert result["content_source"] == "not_found"
    assert result["doi"] == "10.1234/test"
```

- [ ] **Step 6: Implement `get_pdf_content` tool**

Add to `src/zotero_mcp/server.py`, after the `get_item_attachments` tool:

```python
@mcp.tool(
    description=(
        "Find the best way to access a Zotero item's full-text content. "
        "Returns a PMCID (for PubMed MCP full-text retrieval), a local PDF "
        "file path (for Claude's Read tool), or a DOI/URL fallback. "
        "Call this before trying to read a paper's content."
    )
)
def get_pdf_content(item_key: str) -> str:
    """Route to the best available content source for a Zotero item.

    Args:
        item_key: Zotero item key.

    Returns:
        JSON with content_source and the relevant identifier or path.
    """
    import re
    import tempfile

    _validate_key(item_key, "item_key")
    item_key = item_key.strip()

    # Read item metadata
    item = _read_local_or_web("get_item", item_key)
    if isinstance(item, str):
        return json.dumps({"item_key": item_key, "content_source": "error",
                           "message": "Could not read item metadata."})

    doi = item.get("DOI", "")
    extra = item.get("extra", "")
    url = item.get("url", "")

    # Step 1: Check for PMCID via PMID
    pmid_match = re.search(r"PMID:\s*(\d+)", extra)
    if pmid_match:
        pmid = pmid_match.group(1)
        try:
            import httpx as _httpx
            resp = _httpx.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={"db": "pmc", "term": f"{pmid}[pmid]", "retmode": "json"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                ids = resp.json().get("esearchresult", {}).get("idlist", [])
                if ids:
                    pmcid = f"PMC{ids[0]}"
                    return json.dumps({
                        "item_key": item_key,
                        "content_source": "pmc",
                        "pmcid": pmcid,
                        "pmid": pmid,
                        "message": (
                            "Use PubMed MCP get_full_text_article with this "
                            "PMCID for structured full text."
                        ),
                    })
        except Exception:
            pass  # Fall through to PDF paths

    # Step 2: Check for PDF attachments
    try:
        children = _read_local_or_web("get_children", item_key, item_type="attachment")
    except Exception:
        children = []

    pdf_attachments = [
        c for c in children if c.get("contentType") == "application/pdf"
    ]

    if pdf_attachments:
        att = pdf_attachments[0]
        att_key = att.get("key", "")

        # Step 3: Try local file path (fastest)
        try:
            local = _get_local()
            local_path = local.get_attachment_path(att_key)
            if local_path:
                return json.dumps({
                    "item_key": item_key,
                    "content_source": "local_pdf",
                    "pdf_path": local_path,
                    "attachment_key": att_key,
                    "message": (
                        "PDF available locally. Read this file path for "
                        "full content."
                    ),
                })
        except RuntimeError:
            pass  # Local API not available

        # Step 4: Download from web API
        try:
            web = _get_web()
            pdf_bytes = web.download_attachment(att_key)
            tmp = tempfile.NamedTemporaryFile(
                prefix="zotero_mcp_", suffix=".pdf", delete=False
            )
            tmp.write(pdf_bytes)
            tmp.close()
            return json.dumps({
                "item_key": item_key,
                "content_source": "web_pdf",
                "pdf_path": tmp.name,
                "attachment_key": att_key,
                "message": (
                    "PDF downloaded from Zotero cloud storage. Read this "
                    "file path for full content."
                ),
            })
        except Exception:
            pass  # Fall through to not_found

    # Step 5: No PDF, return DOI/URL fallback
    result = {
        "item_key": item_key,
        "content_source": "not_found",
        "message": (
            "No PDF attached. Try accessing via DOI or ask the user "
            "for the file."
        ),
    }
    if doi:
        result["doi"] = doi
    if url:
        result["url"] = url
    return json.dumps(result, ensure_ascii=False)
```

- [ ] **Step 7: Update capabilities.py TOOL_MODES**

Add to `TOOL_MODES` dict in `src/zotero_mcp/capabilities.py`:

```python
"get_pdf_content": ["any_read"],
```

- [ ] **Step 8: Run all tests**

Run: `python -m pytest tests/test_pdf_content.py tests/test_local_client.py tests/test_web_client_pdf.py -v`
Expected: All tests PASS

- [ ] **Step 9: Update test_server.py tool count**

In `tests/test_server.py`, add `"get_pdf_content"` to the `expected` set and update the count:

```python
assert len(tools) == 19
```

- [ ] **Step 10: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 11: Commit**

```bash
git add src/zotero_mcp/server.py src/zotero_mcp/capabilities.py src/zotero_mcp/local_client.py src/zotero_mcp/web_client.py tests/
git commit -m "feat: add get_pdf_content tool — smart content router for PDF access"
```
