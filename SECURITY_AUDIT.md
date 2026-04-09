# Security Audit: zotero-mcp v0.4.0

**Date:** 2026-04-09
**Scope:** Full source audit of `src/zotero_mcp/` (7 modules, ~2,500 LOC)
**Severity levels:** CRITICAL / HIGH / MEDIUM / LOW / INFO

---

## Executive Summary

The zotero-mcp server has a generally reasonable security posture for an MCP tool server. Input validation exists for key parameters, API keys are read from environment variables rather than hardcoded, and the codebase avoids shell execution and dynamic code evaluation. However, several issues were identified ranging from medium to low severity, primarily around **path traversal**, **XML External Entity (XXE) injection**, **Server-Side Request Forgery (SSRF)**, **temporary file management**, and **information leakage**.

**Findings by severity:**
- CRITICAL: 0
- HIGH: 1 (XXE via XML parsing)
- MEDIUM: 4
- LOW: 4
- INFO: 3

---

## HIGH Severity

### H1: XML External Entity (XXE) Injection in PubMed XML Parsing

**File:** `src/zotero_mcp/web_client.py:719`
**Category:** Injection

The `_parse_pubmed_xml()` method parses XML returned from the PubMed efetch API using Python's `xml.etree.ElementTree.fromstring()` without disabling external entity resolution:

```python
root = ElementTree.fromstring(xml_text)
```

While `ElementTree` in CPython is not vulnerable to classic XXE by default (it uses the `expat` parser which does not resolve external entities), this is a **defense-in-depth concern**. If the runtime or a future Python version changes the underlying parser, or if the code is ever ported to use `lxml`, this becomes exploitable. More importantly, the input comes from an external network response (PubMed API), which could be intercepted via MITM if HTTPS certificate validation were weakened.

**Recommendation:** Use `defusedxml.ElementTree` as a hardened drop-in replacement, or explicitly disable entity resolution:

```python
from xml.etree.ElementTree import XMLParser
parser = XMLParser()
parser.entity = {}  # Block entity resolution
```

Or preferably:
```python
import defusedxml.ElementTree as ET
root = ET.fromstring(xml_text)
```

---

## MEDIUM Severity

### M1: Path Traversal in `attach_pdf` — Arbitrary File Read

**File:** `src/zotero_mcp/web_client.py:1631-1637`
**Category:** Path Traversal / Arbitrary File Read

The `attach_pdf` tool accepts a user-provided `pdf_path` and reads its contents without any path validation beyond a `.pdf` extension check:

```python
if pdf_path:
    path = Path(pdf_path)
    if not path.exists():
        raise RuntimeError(f"PDF file not found: {pdf_path}")
    pdf_bytes = path.read_bytes()
```

An MCP client (LLM) could be manipulated to provide paths like `/etc/shadow.pdf` (if symlinked) or `../../sensitive_data.pdf`. The only protection is the `.pdf` extension check in `server.py:957`, which prevents reading arbitrary non-PDF files but still allows reading **any PDF file on the filesystem** accessible to the process.

**Impact:** An attacker who can influence the LLM's tool calls could exfiltrate the contents of any `.pdf` file readable by the server process.

**Recommendation:**
- Restrict `pdf_path` to an allowlist of directories (e.g., user's home, Downloads, a configured upload directory)
- Resolve the path and verify it doesn't escape allowed directories:
  ```python
  resolved = Path(pdf_path).resolve()
  if not any(resolved.is_relative_to(d) for d in ALLOWED_DIRS):
      raise ValueError("pdf_path must be within allowed directories")
  ```

### M2: Path Traversal in `write_cited_document` and `insert_citations` — Arbitrary File Write

**Files:**
- `src/zotero_mcp/server.py:1082` (`write_cited_document`)
- `src/zotero_mcp/server.py:1008-1011` (`insert_citations`)
- `src/zotero_mcp/citation_writer.py:409` (`build_document`)
- `src/zotero_mcp/citation_writer.py:530` (`insert_citations`)

Both tools accept an `output_path` parameter with only a `.docx` extension check. The `build_document` function resolves and saves to any path:

```python
output = Path(output_path).resolve()
doc.save(str(output))
```

An LLM could be tricked into writing a `.docx` file to sensitive locations (e.g., overwriting configuration files that happen to not validate content, or writing to web-accessible directories).

**Impact:** Arbitrary `.docx` file write to any writable path on the filesystem.

**Recommendation:** Same directory allowlisting as M1.

### M3: Server-Side Request Forgery (SSRF) via `create_item_from_url`

**File:** `src/zotero_mcp/web_client.py:969-1063`
**Category:** SSRF

The `create_item_from_url` tool forwards user-provided URLs to the Zotero translation server:

```python
resp = self._translate_client.post(
    TRANSLATE_WEB_URL,
    content=url,
    headers={"Content-Type": "text/plain"},
)
```

While the actual HTTP request is made by the translation server (not directly by this code), the URL is still sent to an external service without validation. More concerning, if the translation server is self-hosted, this could be used to probe internal network services.

Additionally, `_download_free_pdf()` in `web_client.py:1778-1861` follows redirects from Unpaywall-provided URLs:

```python
pdf_resp = httpx.get(pdf_url, timeout=30.0, follow_redirects=True)
```

The `pdf_url` comes from the Unpaywall API response, which could potentially redirect to internal network addresses if the API response is compromised.

**Recommendation:**
- Validate URLs against a blocklist of internal/private IP ranges (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, ::1, etc.)
- Restrict URL schemes to `https://` only

### M4: Temporary File Leakage — PDF Files Not Cleaned Up

**File:** `src/zotero_mcp/server.py:392-410, 421-439`
**Category:** Information Leakage

The `get_pdf_content` tool downloads PDFs to temporary files with `delete=False` and returns the path to the LLM, but never cleans them up:

```python
tmp = tempfile.NamedTemporaryFile(
    prefix="zotero_mcp_", suffix=".pdf", delete=False
)
tmp.write(pdf_bytes)
tmp.close()
return json.dumps({
    "pdf_path": tmp.name,
    ...
})
```

These temporary PDF files accumulate in `/tmp/` indefinitely. On shared systems, other users may be able to read them (default `NamedTemporaryFile` permissions are often 0o600, but this is platform-dependent).

**Impact:** Research PDFs (potentially containing confidential preprints or licensed content) persist on disk indefinitely and may be accessible to other users on shared systems.

**Recommendation:**
- Implement a cleanup mechanism (e.g., track temp files and delete after a session timeout)
- Use `tempfile.mkdtemp()` with explicit restrictive permissions
- Consider using `tempfile.TemporaryDirectory()` with a context manager at the session level

---

## LOW Severity

### L1: MD5 Used for Hashing (Non-Security Context)

**Files:**
- `src/zotero_mcp/web_client.py:1685` — `hashlib.md5(pdf_bytes).hexdigest()` for Zotero upload
- `src/zotero_mcp/citation_writer.py:147` — `hashlib.md5(item_key.encode()).hexdigest()[:8]` for CSL-JSON IDs

MD5 is used in two places. Both are for non-security purposes (Zotero API compatibility and citation ID generation), so this is not directly exploitable. However, it may trigger security scanner alerts.

**Recommendation:** For the citation writer, consider using `hashlib.sha256` truncated. The Zotero upload API requires MD5, so that usage is unavoidable.

### L2: Hardcoded Email in User-Agent Strings

**Files:**
- `src/zotero_mcp/openalex_client.py:23` — `"zotero-mcp/1.0 (mailto:zotero-mcp@example.com)"`
- `src/zotero_mcp/web_client.py:271,324,854` — Same email in CrossRef requests

The placeholder email `zotero-mcp@example.com` is used in User-Agent headers for API "polite pool" access. While not a vulnerability, if many users deploy this, rate limiting will be shared across all instances under the same identity.

**Recommendation:** Allow users to configure their email via an environment variable (e.g., `ZOTERO_MCP_EMAIL`) for proper polite pool attribution.

### L3: Missing Input Validation on `update_item` Fields

**File:** `src/zotero_mcp/server.py:781-785`

The `update_item` tool passes a `fields` dict directly to the Zotero API without validating which fields are being updated:

```python
def update_item(item_key: str, fields: dict) -> str:
    _validate_key(item_key, "item_key")
    result = _get_web().update_item(item_key.strip(), fields)
```

While the Zotero Web API itself validates fields, an LLM could be tricked into passing unexpected fields. The Zotero API should reject invalid fields, but allowing an open dict pass-through reduces defense-in-depth.

**Recommendation:** Validate `fields` against an allowlist of known Zotero fields.

### L4: `_read_local_or_web` Uses `getattr` with User-Influenced Method Names

**File:** `src/zotero_mcp/server.py:195-196`

```python
local = _get_local()
return getattr(local, local_method)(*args, **kwargs)
```

The `local_method` parameter comes from hardcoded call sites within the same module (not from external input), so this is not directly exploitable. However, using `getattr` with string-based method dispatch is a pattern worth noting for future maintenance—if `local_method` ever became user-controlled, it could invoke arbitrary methods on the client objects.

**Recommendation:** No immediate action needed, but consider using a dispatch dict instead of `getattr` for clarity and safety.

---

## INFO

### I1: No Rate Limiting on MCP Tool Calls

The server does not implement any rate limiting on incoming MCP tool calls. While the Zotero Web API has its own rate limiting (handled via 429 retry logic), a malicious or runaway LLM client could make excessive calls that trigger upstream rate limits or cause DoS on the local Zotero API.

**Recommendation:** Consider implementing per-tool rate limiting or a global request budget.

### I2: Broad Exception Handling Masks Errors

**File:** `src/zotero_mcp/server.py:146-171`

The `_handle_tool_errors` decorator catches `ValueError`, `httpx.HTTPStatusError`, and `RuntimeError`, converting them to JSON error responses. While this prevents stack traces from reaching the LLM, it may mask unexpected errors during development.

Additionally, several places use bare `except Exception` catches (e.g., `web_client.py:278`, `openalex_client.py:51`) that swallow all errors silently.

**Recommendation:** Log all caught exceptions at `WARNING` level (most already do this). Consider narrowing exception types where possible.

### I3: `.env` Files Not in `.gitignore`

**File:** `.gitignore`

The `.gitignore` does not include `.env` or similar secret-containing files. If a developer creates a `.env` file with their `ZOTERO_API_KEY`, it could accidentally be committed.

**Recommendation:** Add `.env`, `.env.*`, and `*.local` to `.gitignore`.

---

## Positive Security Observations

The following good security practices were observed:

1. **No shell execution** — No use of `subprocess`, `os.system`, `eval`, or `exec` anywhere in the codebase
2. **Input validation on keys** — `_validate_key()` enforces alphanumeric-only pattern via regex (`^[A-Za-z0-9]+$`), preventing injection through Zotero item/collection keys
3. **Parameter clamping** — `_clamp_limit()` bounds numeric inputs to safe ranges
4. **API key via environment variables** — Secrets are read from `ZOTERO_API_KEY`/`ZOTERO_USER_ID` env vars, not hardcoded or passed through tool parameters
5. **Optimistic locking** — Write operations use `If-Unmodified-Since-Version` headers to prevent data races
6. **Retry with backoff** — Rate limit (429) responses are handled with exponential backoff, preventing rapid retry storms
7. **Thread-safe initialization** — Client initialization uses `threading.Lock` for thread safety
8. **PDF validation** — Downloaded PDFs are validated by magic bytes (`%PDF-`) before saving
9. **Structured error responses** — Errors are returned as structured JSON rather than raw exception strings
10. **Read-only annotations** — Read-only tools are properly annotated with `readOnlyHint: True`
11. **Destructive operation annotations** — `empty_trash` is annotated with `destructiveHint: True`
12. **Dependency pinning** — `uv.lock` provides reproducible dependency resolution

---

## Recommended Priority Actions

| Priority | Finding | Effort |
|----------|---------|--------|
| 1 | M1/M2: Add path validation for file read/write operations | Low |
| 2 | H1: Switch to `defusedxml` for XML parsing | Low |
| 3 | I3: Add `.env` to `.gitignore` | Trivial |
| 4 | M4: Implement temp file cleanup | Medium |
| 5 | M3: Add SSRF protections for URL-based operations | Medium |
| 6 | L2: Make polite-pool email configurable | Low |
