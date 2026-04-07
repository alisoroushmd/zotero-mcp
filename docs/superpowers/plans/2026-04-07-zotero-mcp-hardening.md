# Zotero MCP Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the Zotero MCP server against silent failures, resource leaks, rate limiting, and missing functionality identified in the v0.3.0 code review.

**Architecture:** Ten independent tasks, each self-contained and committable on its own. Tasks 1–5 are reliability/correctness fixes; Tasks 6–7 add new API functionality; Tasks 8–10 improve routing, detection, and developer experience. Tests are added or extended for every changed behavior.

**Tech Stack:** Python 3.11+, httpx, FastMCP, pytest + respx (mock HTTP), python-docx

---

## Task 1: Logging for All Silent Exception Blocks

**Files:**
- Modify: `src/zotero_mcp/web_client.py` (lines 174, 261, 579, 753, 894, 1408, 1553, 1571, 1581)
- Modify: `src/zotero_mcp/server.py` (lines 267, 295, 321)
- Modify: `src/zotero_mcp/openalex_client.py` (line 141)
- Test: `tests/test_logging.py` (new file)

- [ ] **Step 1: Write failing tests that assert warnings are emitted**

Create `tests/test_logging.py`:

```python
"""Tests that silent exception blocks emit log warnings."""
import logging
import pytest
import respx
import httpx
from unittest.mock import MagicMock, patch
from zotero_mcp.web_client import WebClient
from zotero_mcp.openalex_client import OpenAlexClient

WEB_BASE = "https://api.zotero.org"
PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CROSSREF_BASE = "https://api.crossref.org"


def make_client():
    return WebClient(api_key="testkey", user_id="123456")


@respx.mock
def test_resolve_pmid_to_pmcid_logs_on_failure(caplog):
    """PMCID lookup failure should log a warning, not swallow silently."""
    respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(
        side_effect=httpx.ConnectError("network down")
    )
    client = make_client()
    with caplog.at_level(logging.WARNING, logger="zotero_mcp.web_client"):
        result = client.resolve_pmid_to_pmcid("12345678")
    assert result is None
    assert any("PMCID" in r.message or "pmcid" in r.message.lower() for r in caplog.records)


@respx.mock
def test_check_crossref_updates_logs_on_network_failure(caplog):
    """CrossRef failure should log a warning."""
    respx.get(f"{CROSSREF_BASE}/works/10.1234/test").mock(
        side_effect=httpx.ConnectError("network down")
    )
    client = make_client()
    with caplog.at_level(logging.WARNING, logger="zotero_mcp.web_client"):
        result = client.check_crossref_updates("10.1234/test")
    assert result["has_retraction"] is False
    assert any("crossref" in r.message.lower() for r in caplog.records)


@respx.mock
def test_check_duplicate_doi_logs_on_failure(caplog):
    """Duplicate DOI check failure should log a warning."""
    respx.get(f"{WEB_BASE}/users/123456/items/top").mock(
        side_effect=httpx.ConnectError("network down")
    )
    client = make_client()
    with caplog.at_level(logging.WARNING, logger="zotero_mcp.web_client"):
        result = client._check_duplicate_doi("10.1234/test")
    assert result is None
    assert any("duplicate" in r.message.lower() for r in caplog.records)


@respx.mock
def test_download_free_pdf_logs_on_each_failure(caplog):
    """Each PDF source failure should log a warning."""
    respx.get("https://api.unpaywall.org/v2/10.1234/test").mock(
        side_effect=httpx.ConnectError("down")
    )
    respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}}))
    client = make_client()
    with caplog.at_level(logging.WARNING, logger="zotero_mcp.web_client"):
        pdf, name, src = client._download_free_pdf("10.1234/test")
    assert pdf is None
    assert any("unpaywall" in r.message.lower() for r in caplog.records)


def test_openalex_fetch_one_logs_on_failure(caplog):
    """OpenAlex reference fetch failure should log."""
    client = OpenAlexClient()
    with patch.object(client._client, "get", side_effect=httpx.ConnectError("down")):
        with caplog.at_level(logging.WARNING, logger="zotero_mcp.openalex_client"):
            result = client.get_references("10.1234/test")
    # get_work itself would return None, so result is []
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/ali.soroush/Library/CloudStorage/OneDrive-Personal/Desktop/experiment/zotero-mcp
python -m pytest tests/test_logging.py -v 2>&1 | head -40
```

Expected: `FAILED` — tests assert log messages that don't exist yet.

- [ ] **Step 3: Add logging to `web_client.py` silent exception blocks**

In `src/zotero_mcp/web_client.py`, update these blocks:

**`resolve_pmid_to_pmcid` (line ~174):**
```python
        except Exception as exc:
            logger.warning("PMID→PMCID lookup failed for %s: %s", pmid, exc)
        return None
```

**`check_crossref_updates` (line ~205, in the except block):**
```python
        except Exception as exc:
            logger.warning("CrossRef update check failed for %s: %s", doi, exc)
            return result
```

**`_check_duplicate_doi` (line ~261, in the except block):**
```python
        except Exception as exc:
            logger.warning("Duplicate DOI check failed for %s: %s", doi, exc)
        return None
```

**`_resolve_via_pubmed` (line ~579, in the final except):**
```python
        except Exception as exc:
            logger.warning("PubMed efetch failed for %s: %s", pmid, exc)
            return None
```

**`_resolve_via_crossref` (line ~752, in the except):**
```python
        except Exception as exc:
            logger.warning("CrossRef resolve failed for %s: %s", identifier, exc)
            return None
```

**`create_item_from_url` (line ~894, in the except for translation server):**
```python
        except Exception as exc:
            logger.warning("Translation server /web failed for %s: %s", url, exc)
```

**`attach_pdf` (line ~1408, in the local DOI read except):**
```python
                except Exception as exc:
                    logger.warning("Local DOI read failed for %s: %s", parent_key, exc)
```

**`_download_free_pdf` — three except blocks (lines ~1553, ~1571, ~1581):**
```python
        # After Unpaywall try block:
        except Exception as exc:
            logger.warning("Unpaywall PDF download failed for %s: %s", doi, exc)

        # After PMC try block:
        except Exception as exc:
            logger.warning("PMC PDF download failed for %s: %s", doi, exc)

        # After bioRxiv try block:
        except Exception as exc:
            logger.warning("bioRxiv/medRxiv PDF download failed for %s: %s", doi, exc)
```

- [ ] **Step 4: Add logging to `server.py` silent exception blocks**

In `src/zotero_mcp/server.py`:

**`get_pdf_content` line ~267 (PMID→PMCID):**
```python
        except Exception as exc:
            logger.warning("PMCID lookup failed for item %s PMID %s: %s", item_key, pmid, exc)
```

**`get_pdf_content` line ~295 (get_children):**
```python
    except Exception as exc:
        logger.warning("Failed to list attachments for %s: %s", item_key, exc)
        children = []
```

**`get_pdf_content` line ~321 (web PDF download):**
```python
        except Exception as exc:
            logger.warning("Web PDF download failed for attachment %s: %s", att_key, exc)
```

- [ ] **Step 5: Add logging to `openalex_client.py` silent exception block**

In `src/zotero_mcp/openalex_client.py`, `_fetch_one` inner function (line ~141):
```python
            except Exception as exc:
                logger.warning("OpenAlex reference fetch failed for %s: %s", ref_id, exc)
            return None
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/test_logging.py -v
```

Expected: all 5 tests `PASSED`.

- [ ] **Step 7: Run full suite to confirm no regressions**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: same pass/fail count as before (all existing tests pass).

- [ ] **Step 8: Commit**

```bash
git add src/zotero_mcp/web_client.py src/zotero_mcp/server.py src/zotero_mcp/openalex_client.py tests/test_logging.py
git commit -m "fix: add logging to all silent exception blocks — replace pass with logger.warning"
```

---

## Task 2: `attach_pdf` Orphan Attachment Cleanup

**Files:**
- Modify: `src/zotero_mcp/web_client.py` (`attach_pdf` method, lines ~1446–1521)
- Test: `tests/test_web_client_pdf.py` (extend existing file)

- [ ] **Step 1: Read the existing PDF upload test file**

```bash
cat tests/test_web_client_pdf.py
```

- [ ] **Step 2: Write a failing test for orphan cleanup**

Add to `tests/test_web_client_pdf.py`:

```python
@respx.mock
def test_attach_pdf_cleans_up_orphan_on_s3_failure():
    """If S3 upload fails after attachment item is created, the attachment item should be deleted."""
    parent_key = "ABCD1234"
    attach_key = "ATTACH01"
    pdf_bytes = b"%PDF-1.4 fake pdf content for testing purposes padding"

    # Step 1: Create attachment item — succeeds
    respx.post(f"{WEB_BASE}/users/123456/items").mock(
        return_value=httpx.Response(200, json={
            "successful": {"0": {"key": attach_key, "data": {"key": attach_key, "version": 1}}}
        })
    )
    # Step 2: Get upload auth — succeeds
    respx.post(f"{WEB_BASE}/users/123456/items/{attach_key}/file").mock(
        return_value=httpx.Response(200, json={
            "url": "https://s3.amazonaws.com/upload",
            "prefix": "",
            "suffix": "",
            "contentType": "application/pdf",
            "uploadKey": "testkey123",
        })
    )
    # Step 3: S3 upload — FAILS
    respx.post("https://s3.amazonaws.com/upload").mock(
        side_effect=httpx.ConnectError("S3 unreachable")
    )
    # Cleanup DELETE — should be called
    delete_route = respx.delete(f"{WEB_BASE}/users/123456/items").mock(
        return_value=httpx.Response(204)
    )

    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp_path = f.name

    try:
        client = WebClient(api_key="testkey", user_id="123456")
        with pytest.raises(Exception):
            client.attach_pdf(parent_key, pdf_path=tmp_path)
        assert delete_route.called, "Expected orphan attachment DELETE to be called"
    finally:
        os.unlink(tmp_path)
```

- [ ] **Step 3: Run test to verify it fails**

```bash
python -m pytest tests/test_web_client_pdf.py::test_attach_pdf_cleans_up_orphan_on_s3_failure -v
```

Expected: `FAILED` — cleanup DELETE is not called yet.

- [ ] **Step 4: Implement orphan cleanup in `attach_pdf`**

In `src/zotero_mcp/web_client.py`, wrap steps 2–4 in a try/except that deletes the attachment item on failure. Replace the section after `attach_key = self._extract_created_key(resp.json())` (line ~1448) through the end of the method:

```python
        attach_key = self._extract_created_key(resp.json())

        try:
            # Step 2: Get upload authorization
            md5_hash = hashlib.md5(pdf_bytes).hexdigest()
            file_size = len(pdf_bytes)

            auth_resp = self._web_client.post(
                f"/items/{attach_key}/file",
                headers={
                    "If-None-Match": "*",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                content=urlencode(
                    {
                        "md5": md5_hash,
                        "filename": filename,
                        "filesize": file_size,
                        "mtime": int(time.time() * 1000),
                    }
                ),
            )
            auth_resp.raise_for_status()
            auth_data = auth_resp.json()

            if auth_data.get("exists"):
                return {
                    "status": "exists",
                    "attachment_key": attach_key,
                    "filename": filename,
                    "source": source,
                    "message": "File already exists in Zotero storage.",
                }

            # Step 3: Upload to Zotero storage
            upload_url = auth_data["url"]
            upload_prefix = auth_data.get("prefix", b"")
            upload_suffix = auth_data.get("suffix", b"")
            upload_content_type = auth_data.get("contentType", "application/pdf")

            if isinstance(upload_prefix, str):
                upload_prefix = upload_prefix.encode()
            if isinstance(upload_suffix, str):
                upload_suffix = upload_suffix.encode()

            upload_body = upload_prefix + pdf_bytes + upload_suffix

            upload_resp = httpx.post(
                upload_url,
                content=upload_body,
                headers={"Content-Type": upload_content_type},
                timeout=60.0,
            )
            upload_resp.raise_for_status()

            # Step 4: Register upload
            register_resp = self._web_client.post(
                f"/items/{attach_key}/file",
                headers={
                    "If-None-Match": "*",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                content=urlencode({"upload": auth_data["uploadKey"]}),
            )
            register_resp.raise_for_status()

        except Exception:
            # Clean up the orphaned attachment item so it doesn't pollute the library
            try:
                # Get version for the delete header
                ver_resp = self._web_client.get("/items", params={"limit": 0})
                version = ver_resp.headers.get("Last-Modified-Version", "0")
                self._web_client.delete(
                    "/items",
                    params={"itemKey": attach_key},
                    headers={"If-Unmodified-Since-Version": version},
                )
                logger.warning("Cleaned up orphan attachment %s after upload failure", attach_key)
            except Exception as cleanup_exc:
                logger.warning(
                    "Failed to clean up orphan attachment %s: %s", attach_key, cleanup_exc
                )
            raise

        logger.info("Attached PDF %s to item %s (%s)", filename, parent_key, source)
        return {
            "status": "attached",
            "attachment_key": attach_key,
            "parent_key": parent_key,
            "filename": filename,
            "source": source,
            "size_bytes": file_size,
        }
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_web_client_pdf.py -v
```

Expected: all tests in this file pass.

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 7: Commit**

```bash
git add src/zotero_mcp/web_client.py tests/test_web_client_pdf.py
git commit -m "fix: delete orphan attachment item if PDF upload to S3 fails"
```

---

## Task 3: PDF Magic Byte Validation

**Files:**
- Modify: `src/zotero_mcp/web_client.py` (`_download_free_pdf`, lines ~1550, ~1570, ~1580)
- Test: `tests/test_web_client_pdf.py` (extend)

- [ ] **Step 1: Write failing test**

Add to `tests/test_web_client_pdf.py`:

```python
@respx.mock
def test_download_free_pdf_rejects_non_pdf_bytes():
    """Content that doesn't start with %PDF- should be rejected even if large."""
    doi = "10.1234/test"
    not_a_pdf = b"<html>" + b"x" * 2000  # HTML page, not a PDF

    respx.get(f"https://api.unpaywall.org/v2/{doi}").mock(
        return_value=httpx.Response(200, json={
            "best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf"}
        })
    )
    respx.get("https://example.com/paper.pdf").mock(
        return_value=httpx.Response(200, content=not_a_pdf)
    )
    # PMC: no results
    respx.get(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )

    client = WebClient(api_key="testkey", user_id="123456")
    pdf, name, src = client._download_free_pdf(doi)
    assert pdf is None, "Should reject HTML page masquerading as PDF"


@respx.mock
def test_download_free_pdf_accepts_small_valid_pdf():
    """A small but valid PDF (>= 5 bytes starting with %PDF-) should be accepted."""
    doi = "10.1234/test"
    valid_pdf = b"%PDF-1.4 minimal content"  # Starts with magic bytes, small

    respx.get(f"https://api.unpaywall.org/v2/{doi}").mock(
        return_value=httpx.Response(200, json={
            "best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf"}
        })
    )
    respx.get("https://example.com/paper.pdf").mock(
        return_value=httpx.Response(200, content=valid_pdf)
    )

    client = WebClient(api_key="testkey", user_id="123456")
    pdf, name, src = client._download_free_pdf(doi)
    assert pdf == valid_pdf
    assert src == "unpaywall"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_web_client_pdf.py::test_download_free_pdf_rejects_non_pdf_bytes tests/test_web_client_pdf.py::test_download_free_pdf_accepts_small_valid_pdf -v
```

Expected: at least one `FAILED`.

- [ ] **Step 3: Add `_is_valid_pdf` helper and replace content-length checks**

In `src/zotero_mcp/web_client.py`, add a module-level helper just before the `WebClient` class:

```python
def _is_valid_pdf(content: bytes) -> bool:
    """Validate PDF by magic bytes rather than content length."""
    return len(content) >= 5 and content[:5] == b"%PDF-"
```

Then in `_download_free_pdf`, replace all three occurrences of `len(pdf_resp.content) > 1000` with `_is_valid_pdf(pdf_resp.content)`:

Line ~1550 (Unpaywall):
```python
                    if pdf_resp.status_code == 200 and _is_valid_pdf(pdf_resp.content):
```

Line ~1570 (PMC):
```python
                        if pdf_resp.status_code == 200 and _is_valid_pdf(pdf_resp.content):
```

Line ~1580 (bioRxiv):
```python
                if pdf_resp.status_code == 200 and _is_valid_pdf(pdf_resp.content):
```
And the medRxiv line below it:
```python
                if pdf_resp.status_code == 200 and _is_valid_pdf(pdf_resp.content):
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_web_client_pdf.py -v
```

Expected: all pass.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add src/zotero_mcp/web_client.py tests/test_web_client_pdf.py
git commit -m "fix: validate PDF magic bytes instead of content length in _download_free_pdf"
```

---

## Task 4: `_local_failed` TTL (Retry Local API Probe After 5 Minutes)

**Files:**
- Modify: `src/zotero_mcp/server.py` (lines ~28–64)
- Test: `tests/test_server.py` (extend)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_server.py`:

```python
def test_local_failed_ttl_allows_retry_after_interval():
    """After _local_failed_at is set, local client should be retried after the retry interval."""
    import time
    import zotero_mcp.server as srv

    # Simulate a previous failure that happened > TTL ago
    old_timestamp = time.monotonic() - srv._LOCAL_RETRY_INTERVAL - 1.0
    with (
        patch.object(srv, "_local_failed_at", old_timestamp),
        patch.object(srv, "_local", None),
        patch("zotero_mcp.server.LocalClient") as mock_lc,
    ):
        mock_lc.return_value = MagicMock()  # Probe succeeds this time
        result = srv._get_local()
        mock_lc.assert_called_once()  # LocalClient was instantiated (retry happened)


def test_local_failed_ttl_blocks_within_interval():
    """Within the retry interval, _get_local should raise immediately without probing."""
    import time
    import zotero_mcp.server as srv

    recent_timestamp = time.monotonic() - 10.0  # 10 seconds ago, within 5-minute TTL
    with (
        patch.object(srv, "_local_failed_at", recent_timestamp),
        patch.object(srv, "_local", None),
        patch("zotero_mcp.server.LocalClient") as mock_lc,
    ):
        with pytest.raises(RuntimeError, match="unavailable"):
            srv._get_local()
        mock_lc.assert_not_called()  # No probe attempted within TTL
```

Also add `from unittest.mock import MagicMock, patch` to imports if not already present.

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_server.py::test_local_failed_ttl_allows_retry_after_interval tests/test_server.py::test_local_failed_ttl_blocks_within_interval -v
```

Expected: `FAILED` — `_LOCAL_RETRY_INTERVAL` attribute doesn't exist yet.

- [ ] **Step 3: Implement TTL in `server.py`**

Replace the current global state and `_get_local` in `src/zotero_mcp/server.py`:

Replace lines 28–64 with:

```python
import time

_local: LocalClient | None = None
_local_failed_at: float | None = None  # time.monotonic() when probe last failed; None = no failure
_LOCAL_RETRY_INTERVAL = 300.0  # retry local API probe every 5 minutes
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
    """Lazy-initialize the local client (thread-safe) with TTL-based failure caching.

    If the probe previously failed, retries every _LOCAL_RETRY_INTERVAL seconds
    so that starting Zotero mid-session is picked up automatically.
    """
    global _local, _local_failed_at
    now = time.monotonic()
    if _local_failed_at is not None and (now - _local_failed_at) < _LOCAL_RETRY_INTERVAL:
        raise RuntimeError("Local API unavailable (cached)")
    if _local is None:
        with _init_lock:
            now = time.monotonic()
            if _local_failed_at is not None and (now - _local_failed_at) < _LOCAL_RETRY_INTERVAL:
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
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_server.py -v
```

Expected: all pass including the two new TTL tests.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add src/zotero_mcp/server.py tests/test_server.py
git commit -m "fix: replace sticky _local_failed bool with 5-minute TTL retry for local API probe"
```

---

## Task 5: Shared Retry-with-Backoff Utility + Apply to All Web Writes

**Files:**
- Modify: `src/zotero_mcp/web_client.py` (add helper, apply to PATCH/POST/DELETE operations)
- Test: `tests/test_retry.py` (new file)

- [ ] **Step 1: Write failing tests**

Create `tests/test_retry.py`:

```python
"""Tests for the retry-with-backoff helper and its application to write operations."""
import time
import httpx
import pytest
import respx
from unittest.mock import patch
from zotero_mcp.web_client import WebClient, _retry_request

WEB_BASE = "https://api.zotero.org"


def make_client():
    return WebClient(api_key="testkey", user_id="123456")


def test_retry_request_succeeds_after_429():
    """_retry_request retries on 429 and returns the successful response."""
    call_count = 0

    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    with patch("time.sleep"):  # Don't actually sleep in tests
        resp = _retry_request(flaky)
    assert resp.status_code == 200
    assert call_count == 2


def test_retry_request_raises_after_max_attempts():
    """_retry_request raises after max_attempts of 429."""
    def always_429():
        return httpx.Response(429, headers={"Retry-After": "0"})

    with patch("time.sleep"):
        with pytest.raises(httpx.HTTPStatusError):
            _retry_request(always_429, max_attempts=3)


def test_retry_request_caps_sleep_at_30s():
    """Retry sleep is capped at 30 seconds regardless of Retry-After header."""
    sleep_calls = []

    def always_429():
        return httpx.Response(429, headers={"Retry-After": "999"})

    with patch("time.sleep", side_effect=lambda d: sleep_calls.append(d)):
        with pytest.raises(httpx.HTTPStatusError):
            _retry_request(always_429, max_attempts=2)

    assert all(d <= 30.0 for d in sleep_calls)


@respx.mock
def test_update_item_retries_on_429():
    """update_item retries on 429 before succeeding."""
    item_key = "ABCD1234"
    call_count = 0

    respx.get(f"{WEB_BASE}/users/123456/items/{item_key}").mock(
        return_value=httpx.Response(200, json={"data": {"key": item_key, "version": 5, "title": "Test"}})
    )

    def patch_side_effect(request, route):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(204, headers={"Last-Modified-Version": "6"})

    respx.patch(f"{WEB_BASE}/users/123456/items/{item_key}").mock(side_effect=patch_side_effect)

    client = make_client()
    with patch("time.sleep"):
        result = client.update_item(item_key, {"title": "New Title"})
    assert result["version"] == 6
    assert call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_retry.py -v
```

Expected: `FAILED` — `_retry_request` doesn't exist yet.

- [ ] **Step 3: Add `_retry_request` module-level helper to `web_client.py`**

Add this function to `src/zotero_mcp/web_client.py` just before the `WebClient` class definition (after the `_is_valid_pdf` helper from Task 3):

```python
def _retry_request(
    fn: "Callable[[], httpx.Response]",
    max_attempts: int = 3,
    base_delay: float = 1.0,
) -> "httpx.Response":
    """Call fn() and retry on 429 with exponential backoff.

    Args:
        fn: Zero-argument callable that returns an httpx.Response.
        max_attempts: Maximum number of attempts (including first).
        base_delay: Base sleep duration in seconds; doubles each retry.

    Returns:
        The successful response.

    Raises:
        httpx.HTTPStatusError: If rate-limited on the final attempt.
    """
    from typing import Callable  # local import to avoid circular

    for attempt in range(max_attempts):
        resp = fn()
        if resp.status_code != 429:
            return resp
        if attempt == max_attempts - 1:
            resp.raise_for_status()  # Raises HTTPStatusError
        retry_after = float(resp.headers.get("Retry-After", base_delay * (2 ** attempt)))
        delay = min(retry_after, 30.0)
        logger.warning(
            "Rate limited (429), retrying in %.1fs (attempt %d/%d)",
            delay,
            attempt + 1,
            max_attempts,
        )
        time.sleep(delay)
    return resp  # unreachable but satisfies type checker
```

Also add `from typing import TYPE_CHECKING, Callable` at the top (the `TYPE_CHECKING` import is already there, just add `Callable` to it).

- [ ] **Step 4: Apply `_retry_request` to write operations in `WebClient`**

Update these methods in `web_client.py`:

**`update_item` (replaces the direct PATCH call):**
```python
        resp = _retry_request(
            lambda: self._web_client.patch(
                f"/items/{item_key}",
                headers={"If-Unmodified-Since-Version": str(version)},
                json=fields,
            )
        )
```

**`add_to_collection` (replaces the direct PATCH call):**
```python
        resp = _retry_request(
            lambda: self._web_client.patch(
                f"/items/{item_key}",
                headers={"If-Unmodified-Since-Version": str(version)},
                json={"collections": collections},
            )
        )
```

**`create_item_from_identifier` (replaces the POST call):**
```python
        resp = _retry_request(lambda: self._web_client.post("/items", json=[metadata]))
```

**`create_item_from_url` (replaces the POST call):**
```python
        resp = _retry_request(lambda: self._web_client.post("/items", json=[metadata]))
```

**`create_item_manual` (replaces the POST call):**
```python
        resp = _retry_request(lambda: self._web_client.post("/items", json=[metadata]))
```

**`create_note` (replaces the POST call):**
```python
        resp = _retry_request(lambda: self._web_client.post("/items", json=[note_data]))
```

**`create_collection` (replaces the POST call):**
```python
        resp = _retry_request(lambda: self._web_client.post("/collections", json=payload))
```

Note: `batch_organize` already has its own 429 handling — leave it as-is. `trash_items` batch loop failure handling can also stay.

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_retry.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 7: Commit**

```bash
git add src/zotero_mcp/web_client.py tests/test_retry.py
git commit -m "feat: add _retry_request backoff helper, apply 429 retry to all write operations"
```

---

## Task 6: `search_items` Filtering (item_type and tag)

**Files:**
- Modify: `src/zotero_mcp/server.py` (`search_items` tool, line ~147)
- Modify: `src/zotero_mcp/web_client.py` (`search_items` method, line ~66)
- Modify: `src/zotero_mcp/local_client.py` (`search_items` method, line ~46)
- Test: `tests/test_search_filters.py` (new file)

- [ ] **Step 1: Write failing tests**

Create `tests/test_search_filters.py`:

```python
"""Tests for item_type and tag filtering in search_items."""
import httpx
import pytest
import respx
from zotero_mcp.web_client import WebClient
from zotero_mcp.local_client import LocalClient

WEB_BASE = "https://api.zotero.org"
LOCAL_BASE = "http://localhost:23119/api"

SAMPLE_ITEM = {
    "data": {
        "key": "ITEM0001",
        "itemType": "journalArticle",
        "title": "Test Article",
        "creators": [],
        "date": "2024",
        "DOI": "10.1234/test",
        "collections": [],
        "tags": [{"tag": "ai"}],
        "version": 1,
    }
}


@respx.mock
def test_web_search_items_with_item_type():
    """item_type param is forwarded to the Zotero Web API."""
    route = respx.get(f"{WEB_BASE}/users/123456/items/top").mock(
        return_value=httpx.Response(200, json=[SAMPLE_ITEM])
    )
    client = WebClient(api_key="testkey", user_id="123456")
    results = client.search_items("test", limit=10, item_type="journalArticle")
    assert results[0]["item_type"] == "journalArticle"
    # The request should have itemType in params
    assert route.calls[0].request.url.params["itemType"] == "journalArticle"


@respx.mock
def test_web_search_items_with_tag():
    """tag param is forwarded to the Zotero Web API."""
    route = respx.get(f"{WEB_BASE}/users/123456/items/top").mock(
        return_value=httpx.Response(200, json=[SAMPLE_ITEM])
    )
    client = WebClient(api_key="testkey", user_id="123456")
    results = client.search_items("test", limit=10, tag="ai")
    assert route.calls[0].request.url.params["tag"] == "ai"


@respx.mock
def test_local_search_items_with_item_type(monkeypatch):
    """item_type overrides the default -attachment||-note filter on local client."""
    route = respx.get(f"{LOCAL_BASE}/users/0/items").mock(
        return_value=httpx.Response(200, json=[SAMPLE_ITEM])
    )
    # Skip probe
    monkeypatch.setattr("zotero_mcp.local_client.LocalClient.__init__",
                        lambda self, *a, **kw: setattr(self, "_client",
                            httpx.Client(base_url=LOCAL_BASE, timeout=5.0)) or None)
    client = LocalClient.__new__(LocalClient)
    client._base = LOCAL_BASE
    client._client = httpx.Client(base_url=LOCAL_BASE, timeout=5.0)

    results = client.search_items("test", limit=10, item_type="journalArticle")
    params = route.calls[0].request.url.params
    assert params["itemType"] == "journalArticle"
    # Should NOT contain the default exclusion filter when item_type is given
    assert params["itemType"] != "-attachment || -note"


@respx.mock
def test_local_search_items_default_excludes_attachments(monkeypatch):
    """Without item_type filter, local search should still exclude attachments/notes."""
    route = respx.get(f"{LOCAL_BASE}/users/0/items").mock(
        return_value=httpx.Response(200, json=[SAMPLE_ITEM])
    )
    client = LocalClient.__new__(LocalClient)
    client._base = LOCAL_BASE
    client._client = httpx.Client(base_url=LOCAL_BASE, timeout=5.0)

    client.search_items("test", limit=10)
    params = route.calls[0].request.url.params
    assert "-attachment" in params.get("itemType", "")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_search_filters.py -v
```

Expected: `FAILED` — methods don't accept `item_type` or `tag` params yet.

- [ ] **Step 3: Update `WebClient.search_items` in `web_client.py`**

Replace the current `search_items` method (lines ~66–76):

```python
    def search_items(
        self,
        query: str,
        limit: int = 25,
        item_type: str | None = None,
        tag: str | None = None,
    ) -> list[dict]:
        """Search items via Web API. Excludes attachments and notes by default.

        Args:
            query: Keyword search string.
            limit: Max results (1–100).
            item_type: Zotero item type filter (e.g. "journalArticle", "book").
                       Comma-separate multiple types. Prefix with "-" to exclude.
            tag: Tag filter. Comma-separate multiple tags. Prefix with "-" to exclude.
        """
        from zotero_mcp.local_client import _format_summary

        params: dict = {"q": query, "limit": limit}
        if item_type:
            params["itemType"] = item_type
        if tag:
            params["tag"] = tag

        resp = self._web_client.get(
            "/items/top",
            params=params,
            timeout=SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        return [_format_summary(item) for item in resp.json()]
```

- [ ] **Step 4: Update `LocalClient.search_items` in `local_client.py`**

Replace the current `search_items` method (lines ~46–56):

```python
    def search_items(
        self,
        query: str,
        limit: int = 25,
        item_type: str | None = None,
        tag: str | None = None,
    ) -> list[dict]:
        """Keyword search across the library.

        Args:
            query: Keyword search string.
            limit: Max results (1–100).
            item_type: Zotero item type filter. When provided, overrides the
                       default "-attachment || -note" exclusion filter.
            tag: Tag filter.
        """
        params: dict = {
            "q": query,
            "limit": limit,
            "itemType": item_type if item_type else "-attachment || -note",
        }
        if tag:
            params["tag"] = tag
        resp = self._get("/users/0/items", params=params)
        return [_format_summary(item) for item in resp.json()]
```

- [ ] **Step 5: Update `search_items` tool in `server.py`**

Replace the `search_items` tool definition (lines ~143–150):

```python
@mcp.tool(
    description=(
        "Search items in Zotero library by keyword. "
        "Optional filters: item_type (e.g. 'journalArticle', 'book'), "
        "tag (e.g. 'ai', '-reviewed' to exclude)."
    ),
    annotations={"readOnlyHint": True},
)
def search_items(
    query: str,
    limit: str | int = 25,
    item_type: str | None = None,
    tag: str | None = None,
) -> str:
    """Search for items by keyword, with optional type and tag filters."""
    results = _read_local_or_web(
        "search_items",
        query,
        _clamp_limit(limit),
        item_type=item_type,
        tag=tag,
    )
    return json.dumps(results, ensure_ascii=False)
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/test_search_filters.py -v
```

Expected: all pass.

- [ ] **Step 7: Run full suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 8: Commit**

```bash
git add src/zotero_mcp/server.py src/zotero_mcp/web_client.py src/zotero_mcp/local_client.py tests/test_search_filters.py
git commit -m "feat: add item_type and tag filter params to search_items tool"
```

---

## Task 7: Tag Management Tools (get_tags, rename_tag, remove_tag)

**Files:**
- Modify: `src/zotero_mcp/web_client.py` (add 3 new methods)
- Modify: `src/zotero_mcp/server.py` (add 3 new tools, update tool count test)
- Modify: `tests/test_server.py` (update tool count assertion)
- Test: `tests/test_tag_management.py` (new file)

- [ ] **Step 1: Write failing tests**

Create `tests/test_tag_management.py`:

```python
"""Tests for tag management operations: get_tags, rename_tag, remove_tag."""
import httpx
import pytest
import respx
from zotero_mcp.web_client import WebClient

WEB_BASE = "https://api.zotero.org"


def make_client():
    return WebClient(api_key="testkey", user_id="123456")


@respx.mock
def test_get_tags_returns_list():
    """get_tags returns list of {tag, count} dicts."""
    respx.get(f"{WEB_BASE}/users/123456/tags").mock(
        return_value=httpx.Response(200, json=[
            {"tag": "ai", "meta": {"numItems": 5}},
            {"tag": "reviewed", "meta": {"numItems": 3}},
        ])
    )
    client = make_client()
    tags = client.get_tags()
    assert len(tags) == 2
    assert tags[0] == {"tag": "ai", "count": 5}
    assert tags[1] == {"tag": "reviewed", "count": 3}


@respx.mock
def test_get_tags_for_collection():
    """get_tags with collection_key queries collection-scoped tags."""
    route = respx.get(f"{WEB_BASE}/users/123456/collections/COL00001/tags").mock(
        return_value=httpx.Response(200, json=[{"tag": "ai", "meta": {"numItems": 2}}])
    )
    client = make_client()
    tags = client.get_tags(collection_key="COL00001")
    assert route.called
    assert tags[0]["tag"] == "ai"


@respx.mock
def test_remove_tag_calls_delete():
    """remove_tag calls DELETE /tags/{encoded_tag} with version header."""
    # Get library version
    respx.get(f"{WEB_BASE}/users/123456/items").mock(
        return_value=httpx.Response(200, headers={"Last-Modified-Version": "42"})
    )
    delete_route = respx.delete(f"{WEB_BASE}/users/123456/tags/ai").mock(
        return_value=httpx.Response(204)
    )
    client = make_client()
    result = client.remove_tag("ai")
    assert delete_route.called
    assert delete_route.calls[0].request.headers["If-Unmodified-Since-Version"] == "42"
    assert result["removed_tag"] == "ai"


@respx.mock
def test_remove_tag_url_encodes_spaces():
    """remove_tag URL-encodes tag names with spaces."""
    respx.get(f"{WEB_BASE}/users/123456/items").mock(
        return_value=httpx.Response(200, headers={"Last-Modified-Version": "1"})
    )
    delete_route = respx.delete(f"{WEB_BASE}/users/123456/tags/needs%20review").mock(
        return_value=httpx.Response(204)
    )
    client = make_client()
    client.remove_tag("needs review")
    assert delete_route.called


@respx.mock
def test_rename_tag_patches_all_matching_items():
    """rename_tag reads items with old tag, removes old tag, adds new tag via PATCH."""
    old_tag = "draft"
    new_tag = "reviewed"
    item_key = "ABCD1234"

    # Search for items with old tag
    respx.get(f"{WEB_BASE}/users/123456/items/top").mock(
        return_value=httpx.Response(200, json=[{
            "data": {
                "key": item_key,
                "version": 3,
                "tags": [{"tag": "draft"}, {"tag": "ai"}],
                "collections": [],
                "itemType": "journalArticle",
                "title": "Test",
            }
        }])
    )
    # PATCH call
    patch_route = respx.patch(f"{WEB_BASE}/users/123456/items/{item_key}").mock(
        return_value=httpx.Response(204, headers={"Last-Modified-Version": "4"})
    )

    client = make_client()
    result = client.rename_tag(old_tag, new_tag)
    assert patch_route.called
    # The patch body should contain new_tag and not old_tag
    import json as _json
    patch_body = _json.loads(patch_route.calls[0].request.content)
    tag_names = [t["tag"] for t in patch_body["tags"]]
    assert new_tag in tag_names
    assert old_tag not in tag_names
    assert result["updated_count"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_tag_management.py -v
```

Expected: `FAILED` — methods don't exist yet.

- [ ] **Step 3: Add `get_tags`, `remove_tag`, `rename_tag` to `WebClient`**

Add these three methods to `src/zotero_mcp/web_client.py` after the `find_duplicates` method:

```python
    def get_tags(self, collection_key: str | None = None) -> list[dict]:
        """List all tags in the library or a collection.

        Args:
            collection_key: Optional collection to scope the tag list.

        Returns:
            List of dicts with "tag" (name) and "count" (number of items).
        """
        if collection_key:
            resp = self._web_client.get(f"/collections/{collection_key}/tags")
        else:
            resp = self._web_client.get("/tags")
        resp.raise_for_status()
        return [
            {"tag": t["tag"], "count": t.get("meta", {}).get("numItems", 0)}
            for t in resp.json()
        ]

    def remove_tag(self, tag: str) -> dict:
        """Remove a tag from all items in the library.

        Args:
            tag: Tag name to remove.

        Returns:
            Dict with "removed_tag" and "status".
        """
        from urllib.parse import quote

        # Get current library version for the version header
        ver_resp = self._web_client.get("/items", params={"limit": 0})
        version = ver_resp.headers.get("Last-Modified-Version", "0")

        encoded_tag = quote(tag, safe="")
        resp = self._web_client.delete(
            f"/tags/{encoded_tag}",
            headers={"If-Unmodified-Since-Version": version},
        )
        resp.raise_for_status()
        logger.info("Removed tag '%s' from all items", tag)
        return {"removed_tag": tag, "status": "removed"}

    def rename_tag(self, old_tag: str, new_tag: str) -> dict:
        """Rename a tag across all items that have it.

        Fetches all items with old_tag, replaces it with new_tag via PATCH.
        Items without old_tag are skipped.

        Args:
            old_tag: Tag name to replace.
            new_tag: New tag name.

        Returns:
            Dict with "updated_count" and "failed_keys".
        """
        # Fetch all items that have this tag (using tag filter)
        all_items = self.search_items("", limit=100, tag=old_tag)

        updated = []
        failed = []

        for item_summary in all_items:
            key = item_summary["key"]
            try:
                item = self._read_item(key)
                version = item.get("version", 0)
                existing_tags = item.get("tags", [])

                # Replace old_tag with new_tag, preserve others
                new_tags = [
                    {"tag": new_tag if t.get("tag") == old_tag else t["tag"]}
                    for t in existing_tags
                ]
                # Deduplicate in case new_tag already exists
                seen: set[str] = set()
                deduped: list[dict] = []
                for t in new_tags:
                    if t["tag"] not in seen:
                        seen.add(t["tag"])
                        deduped.append(t)

                resp = _retry_request(
                    lambda k=key, v=version, tags=deduped: self._web_client.patch(
                        f"/items/{k}",
                        headers={"If-Unmodified-Since-Version": str(v)},
                        json={"tags": tags},
                    )
                )
                resp.raise_for_status()
                updated.append(key)
            except Exception as exc:
                logger.warning("rename_tag failed for item %s: %s", key, exc)
                failed.append(key)

        logger.info("Renamed tag '%s' → '%s' on %d items", old_tag, new_tag, len(updated))
        return {
            "old_tag": old_tag,
            "new_tag": new_tag,
            "updated_count": len(updated),
            "failed_keys": failed,
        }
```

- [ ] **Step 4: Add three tools to `server.py`**

Add after the `find_duplicates` tool definition (after line ~641):

```python
@mcp.tool(
    description="List all tags in the library or a collection, with item counts",
    annotations={"readOnlyHint": True},
)
def get_tags(collection_key: str | None = None) -> str:
    """List all tags with their item counts.

    Args:
        collection_key: Optional collection key to scope the list.
    """
    if collection_key:
        _validate_key(collection_key, "collection_key")
        collection_key = collection_key.strip()
    result = _get_web().get_tags(collection_key)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description="Remove a tag from ALL items in the library (irreversible via this tool)",
    annotations={"destructiveHint": True},
)
def remove_tag(tag: str) -> str:
    """Remove a tag from every item in the library.

    Args:
        tag: Tag name to remove.
    """
    if not tag or not tag.strip():
        raise ValueError("tag must not be empty")
    result = _get_web().remove_tag(tag.strip())
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    description="Rename a tag across all items that have it",
)
def rename_tag(old_tag: str, new_tag: str) -> str:
    """Replace old_tag with new_tag on every item that has it.

    Args:
        old_tag: Tag name to replace.
        new_tag: New tag name.
    """
    if not old_tag or not old_tag.strip():
        raise ValueError("old_tag must not be empty")
    if not new_tag or not new_tag.strip():
        raise ValueError("new_tag must not be empty")
    result = _get_web().rename_tag(old_tag.strip(), new_tag.strip())
    return json.dumps(result, ensure_ascii=False)
```

- [ ] **Step 5: Update tool count in `tests/test_server.py`**

In `tests/test_server.py`, update `test_server_has_all_tools`:

```python
    expected = {
        # ... existing 24 tools ...
        "get_tags",
        "remove_tag",
        "rename_tag",
    }
    # ...
    assert len(tools) == 27
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/test_tag_management.py tests/test_server.py -v
```

Expected: all pass.

- [ ] **Step 7: Run full suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 8: Commit**

```bash
git add src/zotero_mcp/web_client.py src/zotero_mcp/server.py tests/test_tag_management.py tests/test_server.py
git commit -m "feat: add get_tags, remove_tag, rename_tag tools for tag management"
```

---

## Task 8: `get_pdf_content` DOI-First Free PDF Routing

**Files:**
- Modify: `src/zotero_mcp/server.py` (`get_pdf_content`, lines ~322–336)
- Test: `tests/test_pdf_content.py` (extend)

The current routing is: PMCID → local PDF → web attachment → not_found.
This task adds a step after the attachment path fails: if a DOI is available, try `_download_free_pdf(doi)` proactively before returning `not_found`. This helps papers with a DOI but no PMID (non-biomedical literature, books, preprints).

- [ ] **Step 1: Write failing test**

Add to `tests/test_pdf_content.py`:

```python
@respx.mock
def test_get_pdf_content_falls_back_to_doi_free_pdf(tmp_path, monkeypatch):
    """When no PDF attachment exists but DOI is present, try free PDF download."""
    import zotero_mcp.server as srv
    from unittest.mock import MagicMock, patch

    item_key = "ITEM0001"
    doi = "10.1234/test"
    fake_pdf = b"%PDF-1.4 fake content"

    mock_web = MagicMock()
    mock_web.resolve_pmid_to_pmcid.return_value = None
    mock_web.get_item.return_value = {"key": item_key, "DOI": doi, "extra": "", "url": ""}
    # No children = no PDF attachments
    mock_web.get_children = MagicMock(return_value=[])  # called via _read_local_or_web

    # _download_free_pdf returns a valid PDF
    mock_web._download_free_pdf.return_value = (fake_pdf, "paper.pdf", "unpaywall")

    with (
        patch.object(srv, "_local_failed_at", 0.0),  # force local unavailable
        patch.object(srv, "_LOCAL_RETRY_INTERVAL", 9999),
        patch.object(srv, "_web", mock_web),
    ):
        import json
        result = json.loads(srv.get_pdf_content(item_key))

    assert result["content_source"] == "free_pdf"
    assert result["pdf_path"].endswith(".pdf")
    assert result.get("source") in ("unpaywall", "pmc", "biorxiv", "medrxiv")
    # Clean up temp file
    import os
    if os.path.exists(result["pdf_path"]):
        os.unlink(result["pdf_path"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest "tests/test_pdf_content.py::test_get_pdf_content_falls_back_to_doi_free_pdf" -v
```

Expected: `FAILED`.

- [ ] **Step 3: Add free-PDF fallback step in `get_pdf_content`**

In `src/zotero_mcp/server.py`, replace the final "no PDF" block (lines ~322–336):

```python
    # Step 5: No attachment — try free PDF download via DOI
    if doi:
        try:
            web = _get_web()
            pdf_bytes, filename, source = web._download_free_pdf(doi)
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
                return json.dumps(
                    {
                        "item_key": item_key,
                        "content_source": "free_pdf",
                        "pdf_path": tmp.name,
                        "source": source,
                        "message": "Free PDF downloaded. Read this PDF path.",
                    }
                )
        except Exception as exc:
            logger.warning("Free PDF download failed for %s (DOI %s): %s", item_key, doi, exc)

    # Step 6: No PDF available from any source
    result: dict = {
        "item_key": item_key,
        "content_source": "not_found",
        "message": "No PDF attached. Try DOI or ask user for the file.",
    }
    if doi:
        result["doi"] = doi
    if url:
        result["url"] = url
    return json.dumps(result, ensure_ascii=False)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_pdf_content.py -v
```

Expected: all pass.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add src/zotero_mcp/server.py tests/test_pdf_content.py
git commit -m "feat: add DOI-based free PDF fallback in get_pdf_content before returning not_found"
```

---

## Task 9: bioRxiv Latest Version Detection

**Files:**
- Modify: `src/zotero_mcp/web_client.py` (`_download_free_pdf`, lines ~1576–1587)
- Test: `tests/test_web_client_pdf.py` (extend)

- [ ] **Step 1: Write failing test**

Add to `tests/test_web_client_pdf.py`:

```python
@respx.mock
def test_download_free_pdf_uses_latest_biorxiv_version():
    """bioRxiv download should use the latest version from the API, not hardcoded v1."""
    doi = "10.1101/2024.01.01.123456"
    safe_doi = doi.replace("/", "_").replace(".", "_")
    fake_pdf = b"%PDF-1.4 latest version content"

    # bioRxiv API returns version info
    respx.get(f"https://api.biorxiv.org/details/biorxiv/{doi}").mock(
        return_value=httpx.Response(200, json={
            "collection": [
                {"version": "1", "date": "2024-01-01"},
                {"version": "2", "date": "2024-02-01"},
                {"version": "3", "date": "2024-03-01"},
            ]
        })
    )
    # Should request v3, not v1
    v3_route = respx.get(f"https://www.biorxiv.org/content/{doi}v3.full.pdf").mock(
        return_value=httpx.Response(200, content=fake_pdf)
    )
    # Unpaywall: no PDF
    respx.get(f"https://api.unpaywall.org/v2/{doi}").mock(
        return_value=httpx.Response(200, json={"best_oa_location": None})
    )

    client = WebClient(api_key="testkey", user_id="123456")
    pdf, name, src = client._download_free_pdf(doi)
    assert v3_route.called, "Should have requested v3 from bioRxiv"
    assert pdf == fake_pdf
    assert src == "biorxiv"


@respx.mock
def test_download_free_pdf_biorxiv_falls_back_to_v1_on_api_failure():
    """If bioRxiv version API fails, fall back to v1."""
    doi = "10.1101/2024.01.01.999999"
    fake_pdf = b"%PDF-1.4 v1 content"

    respx.get(f"https://api.biorxiv.org/details/biorxiv/{doi}").mock(
        side_effect=httpx.ConnectError("API down")
    )
    respx.get(f"https://www.biorxiv.org/content/{doi}v1.full.pdf").mock(
        return_value=httpx.Response(200, content=fake_pdf)
    )
    respx.get(f"https://api.unpaywall.org/v2/{doi}").mock(
        return_value=httpx.Response(200, json={"best_oa_location": None})
    )

    client = WebClient(api_key="testkey", user_id="123456")
    pdf, name, src = client._download_free_pdf(doi)
    assert pdf == fake_pdf
    assert src == "biorxiv"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest "tests/test_web_client_pdf.py::test_download_free_pdf_uses_latest_biorxiv_version" "tests/test_web_client_pdf.py::test_download_free_pdf_biorxiv_falls_back_to_v1_on_api_failure" -v
```

Expected: at least the first test `FAILED` — still uses v1 hardcoded.

- [ ] **Step 3: Update bioRxiv download section in `_download_free_pdf`**

In `src/zotero_mcp/web_client.py`, replace the bioRxiv/medRxiv block (lines ~1576–1587):

```python
        # 3. Try bioRxiv/medRxiv (DOIs starting with 10.1101/)
        if doi.startswith("10.1101/"):
            try:
                # Fetch latest version number from bioRxiv API
                version_str = "1"  # default fallback
                try:
                    br_resp = httpx.get(
                        f"https://api.biorxiv.org/details/biorxiv/{doi}",
                        timeout=TIMEOUT,
                    )
                    if br_resp.status_code == 200:
                        collection = br_resp.json().get("collection", [])
                        if collection:
                            version_str = str(collection[-1].get("version", "1"))
                except Exception as exc:
                    logger.warning("bioRxiv version lookup failed for %s: %s", doi, exc)

                pdf_url = f"https://www.biorxiv.org/content/{doi}v{version_str}.full.pdf"
                pdf_resp = httpx.get(pdf_url, timeout=30.0, follow_redirects=True)
                if pdf_resp.status_code == 200 and _is_valid_pdf(pdf_resp.content):
                    return pdf_resp.content, f"{safe_doi}.pdf", "biorxiv"

                # Try medRxiv with same version
                pdf_url = f"https://www.medrxiv.org/content/{doi}v{version_str}.full.pdf"
                pdf_resp = httpx.get(pdf_url, timeout=30.0, follow_redirects=True)
                if pdf_resp.status_code == 200 and _is_valid_pdf(pdf_resp.content):
                    return pdf_resp.content, f"{safe_doi}.pdf", "medrxiv"
            except Exception as exc:
                logger.warning("bioRxiv/medRxiv PDF download failed for %s: %s", doi, exc)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_web_client_pdf.py -v
```

Expected: all pass.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add src/zotero_mcp/web_client.py tests/test_web_client_pdf.py
git commit -m "feat: detect latest bioRxiv version via API instead of hardcoding v1"
```

---

## Task 10: Structured Error Responses

**Files:**
- Modify: `src/zotero_mcp/server.py` (add `_error_response` helper, update all error returns)
- Test: `tests/test_error_responses.py` (new file)

- [ ] **Step 1: Write failing tests**

Create `tests/test_error_responses.py`:

```python
"""Tests that tool error responses use structured {error: {code, message}} format."""
import json
import pytest
from unittest.mock import MagicMock, patch
import zotero_mcp.server as srv


def make_mock_web(item=None):
    mock = MagicMock()
    mock.get_item.return_value = item or "bibtex string"
    return mock


def test_get_pdf_content_item_read_error_is_structured():
    """get_pdf_content returns structured error when item metadata cannot be read."""
    with (
        patch.object(srv, "_local_failed_at", 0.0),
        patch.object(srv, "_LOCAL_RETRY_INTERVAL", 9999),
        patch.object(srv, "_web", make_mock_web()),
    ):
        result = json.loads(srv.get_pdf_content("ABCD1234"))

    assert "error" in result
    assert "code" in result["error"]
    assert "message" in result["error"]
    assert result["error"]["code"] == "item_read_error"


def test_get_citation_graph_no_doi_is_structured():
    """get_citation_graph returns structured error when item has no DOI."""
    mock_web = MagicMock()
    mock_web.get_item.return_value = {"key": "ABCD1234", "title": "Test", "DOI": ""}

    with patch.object(srv, "_web", mock_web):
        result = json.loads(srv.get_citation_graph("ABCD1234"))

    assert "error" in result
    assert result["error"]["code"] == "no_doi"


def test_get_citation_graph_item_read_error_is_structured():
    """get_citation_graph returns structured error when item cannot be read."""
    mock_web = MagicMock()
    mock_web.get_item.return_value = "bibtex string"  # Unexpected string = error

    with patch.object(srv, "_web", mock_web):
        result = json.loads(srv.get_citation_graph("ABCD1234"))

    assert "error" in result
    assert result["error"]["code"] == "item_read_error"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_error_responses.py -v
```

Expected: `FAILED` — current error returns don't have this structure.

- [ ] **Step 3: Add `_error_response` helper to `server.py`**

Add after `_parse_list_param` (after line ~108):

```python
def _error_response(code: str, message: str, **extra) -> str:
    """Build a structured error response JSON string.

    Args:
        code: Machine-readable error code (e.g. "item_read_error", "no_doi").
        message: Human-readable explanation.
        **extra: Additional context fields (e.g. item_key, doi).

    Returns:
        JSON string: {"error": {"code": ..., "message": ..., ...extra}}
    """
    return json.dumps({"error": {"code": code, "message": message, **extra}}, ensure_ascii=False)
```

- [ ] **Step 4: Update error returns in `get_pdf_content`**

In `src/zotero_mcp/server.py`, `get_pdf_content`, replace the metadata read error return (lines ~238–245):

```python
    if isinstance(item, str):
        return _error_response(
            "item_read_error",
            "Could not read item metadata.",
            item_key=item_key,
        )
```

- [ ] **Step 5: Update error returns in `get_citation_graph`**

In `src/zotero_mcp/server.py`, `get_citation_graph`, replace the two error returns:

```python
    # Item read error (line ~453):
    if isinstance(item, str):
        return _error_response("item_read_error", "Could not read item metadata", item_key=item_key)

    # No DOI error (line ~457):
    if not doi:
        return _error_response(
            "no_doi",
            "No DOI on this item — cannot query citation graph",
            item_key=item_key,
        )
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/test_error_responses.py -v
```

Expected: all pass.

- [ ] **Step 7: Run full suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 8: Commit**

```bash
git add src/zotero_mcp/server.py tests/test_error_responses.py
git commit -m "feat: add _error_response helper, use structured error format in get_pdf_content and get_citation_graph"
```

---

## Final Verification

- [ ] **Run complete test suite**

```bash
cd /Users/ali.soroush/Library/CloudStorage/OneDrive-Personal/Desktop/experiment/zotero-mcp
python -m pytest tests/ -v 2>&1 | tee /tmp/test_results.txt
tail -30 /tmp/test_results.txt
```

Expected: all tests pass. Tool count is 27 (24 original + get_tags + remove_tag + rename_tag).

- [ ] **Verify server starts**

```bash
python -c "from zotero_mcp.server import mcp; print('Server imports OK')"
```

Expected: `Server imports OK`

- [ ] **Verify tool count**

```bash
python -c "
import asyncio
from zotero_mcp.server import mcp
tools = asyncio.run(mcp.list_tools())
print(f'Total tools: {len(tools)}')
for t in sorted(tools, key=lambda x: x.name):
    print(f'  {t.name}')
"
```

Expected: 27 tools listed.
