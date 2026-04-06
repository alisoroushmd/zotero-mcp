# Duplicate Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent duplicate items on create (DOI, PMID, title similarity) and add a `find_duplicates` audit tool for library-wide scanning.

**Architecture:** Extends existing `_check_duplicate_doi()` pattern in web_client.py. Title similarity uses stdlib `difflib.SequenceMatcher`. New `find_duplicates` tool scans by DOI grouping + title clustering.

**Tech Stack:** difflib (stdlib), no new dependencies.

---

### Task 1: Add title similarity helper to WebClient

**Files:**
- Modify: `src/zotero_mcp/web_client.py`
- Test: `tests/test_duplicates.py` (new)

- [ ] **Step 1: Write the failing test for title normalization and matching**

Create `tests/test_duplicates.py`:

```python
"""Tests for duplicate detection — title similarity and audit tool."""

import httpx
import respx

from zotero_mcp.web_client import WEB_BASE, WebClient

USER_ID = "12345"
API_KEY = "testapikey"
BASE = f"{WEB_BASE}/users/{USER_ID}"


def _make_client() -> WebClient:
    return WebClient(api_key=API_KEY, user_id=USER_ID)


def test_check_duplicate_title_finds_match():
    """Title similarity catches case/punctuation variants."""
    client = _make_client()
    # Mock search_items to return a similar title
    existing = [
        {
            "key": "ABC123",
            "title": "Gastric Intestinal Metaplasia Detection: A Systematic Review",
            "DOI": "",
            "creators": "",
            "date": "2024",
            "item_type": "journalArticle",
            "collections": [],
            "tags": [],
            "version": 1,
        }
    ]
    # Patch the search to return our item
    import unittest.mock as mock
    with mock.patch.object(client, "search_items", return_value=existing):
        result = client._check_duplicate_title(
            "Gastric intestinal metaplasia detection: a systematic review"
        )
    assert result is not None
    assert result["key"] == "ABC123"


def test_check_duplicate_title_rejects_dissimilar():
    """Title similarity rejects clearly different papers."""
    client = _make_client()
    existing = [
        {
            "key": "ABC123",
            "title": "Machine Learning for Drug Discovery",
            "DOI": "",
            "creators": "",
            "date": "2024",
            "item_type": "journalArticle",
            "collections": [],
            "tags": [],
            "version": 1,
        }
    ]
    import unittest.mock as mock
    with mock.patch.object(client, "search_items", return_value=existing):
        result = client._check_duplicate_title(
            "Gastric intestinal metaplasia detection: a systematic review"
        )
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_duplicates.py -v`
Expected: FAIL with `AttributeError: 'WebClient' object has no attribute '_check_duplicate_title'`

- [ ] **Step 3: Implement `_check_duplicate_title`**

Add to `src/zotero_mcp/web_client.py` in the `WebClient` class, after `_check_duplicate_doi`:

```python
def _check_duplicate_title(
    self, title: str, threshold: float = 0.90
) -> dict | None:
    """Check if a similar title already exists in the library.

    Normalizes both titles (lowercase, strip punctuation) and uses
    SequenceMatcher for fuzzy comparison.

    Args:
        title: Title to check against library.
        threshold: Minimum similarity ratio (0-1) to consider a match.

    Returns:
        Item summary dict with added 'similarity' field, or None.
    """
    import re as _re
    from difflib import SequenceMatcher

    def _normalize(t: str) -> str:
        t = t.lower().strip()
        t = _re.sub(r"[^\w\s]", "", t)
        t = _re.sub(r"\s+", " ", t)
        return t

    normalized = _normalize(title)
    if not normalized:
        return None

    # Search using first few significant words
    search_words = normalized.split()[:4]
    search_query = " ".join(search_words)

    try:
        if self._local:
            results = self._local.search_items(search_query, limit=20)
        else:
            results = self.search_items(search_query, limit=20)
    except Exception:
        return None

    for item in results:
        existing_normalized = _normalize(item.get("title", ""))
        ratio = SequenceMatcher(None, normalized, existing_normalized).ratio()
        if ratio >= threshold:
            item["similarity"] = round(ratio, 3)
            item["match_type"] = "title_similarity"
            return item

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_duplicates.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/zotero_mcp/web_client.py tests/test_duplicates.py
git commit -m "feat: add _check_duplicate_title for fuzzy title matching"
```

---

### Task 2: Extend create paths with duplicate checks

**Files:**
- Modify: `src/zotero_mcp/web_client.py`
- Test: `tests/test_duplicates.py`

- [ ] **Step 1: Write failing test for create_item_from_url duplicate check**

Add to `tests/test_duplicates.py`:

```python
@respx.mock
def test_create_item_from_url_detects_duplicate_doi():
    """create_item_from_url checks DOI after URL resolution."""
    # Mock translation server to return item with DOI
    respx.post("https://translate.zotero.org/web").mock(
        return_value=httpx.Response(
            200,
            json=[{
                "itemType": "journalArticle",
                "title": "Test Paper",
                "DOI": "10.1234/existing",
            }],
        )
    )

    client = _make_client()
    # Mock _check_duplicate_doi to find existing item
    import unittest.mock as mock
    existing = {"key": "EXIST1", "title": "Test Paper", "DOI": "10.1234/existing"}
    with mock.patch.object(client, "_check_duplicate_doi", return_value=existing):
        result = client.create_item_from_url("https://example.com/paper")

    assert result.get("duplicate") is True
    assert result["key"] == "EXIST1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_duplicates.py::test_create_item_from_url_detects_duplicate_doi -v`
Expected: FAIL (create_item_from_url currently does not check for duplicates)

- [ ] **Step 3: Add duplicate check to `create_item_from_url`**

In `src/zotero_mcp/web_client.py`, modify `create_item_from_url`. After the line `if title: metadata["title"] = title` and before `if "url" not in metadata:`, add:

```python
# Check for duplicate by DOI
doi = metadata.get("DOI", "")
if doi:
    existing = self._check_duplicate_doi(doi)
    if existing:
        return {
            "key": existing["key"],
            "title": existing["title"],
            "duplicate": True,
            "match_type": "doi",
            "message": f"Item already exists in library with DOI {doi}",
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_duplicates.py::test_create_item_from_url_detects_duplicate_doi -v`
Expected: PASS

- [ ] **Step 5: Write failing test for create_item_manual duplicate checks**

Add to `tests/test_duplicates.py`:

```python
@respx.mock
def test_create_item_manual_detects_duplicate_title():
    """create_item_manual checks title similarity when no DOI provided."""
    client = _make_client()
    import unittest.mock as mock
    existing = {
        "key": "EXIST2",
        "title": "Gastric Intestinal Metaplasia Detection",
        "similarity": 0.95,
        "match_type": "title_similarity",
    }
    with mock.patch.object(client, "_check_duplicate_doi", return_value=None), \
         mock.patch.object(client, "_check_duplicate_title", return_value=existing):
        result = client.create_item_manual(
            item_type="journalArticle",
            title="Gastric intestinal metaplasia detection",
        )

    assert result.get("duplicate") is True
    assert result["key"] == "EXIST2"
    assert result["match_type"] == "title_similarity"


@respx.mock
def test_create_item_manual_checks_doi_first():
    """create_item_manual checks DOI before title similarity."""
    client = _make_client()
    import unittest.mock as mock
    existing = {"key": "EXIST3", "title": "Test", "DOI": "10.1234/test"}
    with mock.patch.object(client, "_check_duplicate_doi", return_value=existing):
        result = client.create_item_manual(
            item_type="journalArticle",
            title="Different Title Entirely",
            doi="10.1234/test",
        )

    assert result.get("duplicate") is True
    assert result["key"] == "EXIST3"
```

- [ ] **Step 6: Add duplicate checks to `create_item_manual`**

In `src/zotero_mcp/web_client.py`, modify `create_item_manual`. After building the `metadata` dict and before `self._apply_collections_and_tags(...)`, add:

```python
# Check for duplicates: DOI first, then title similarity
if doi:
    existing = self._check_duplicate_doi(doi)
    if existing:
        return {
            "key": existing["key"],
            "title": existing["title"],
            "duplicate": True,
            "match_type": "doi",
            "message": f"Item already exists in library with DOI {doi}",
        }

if not doi and title:
    existing = self._check_duplicate_title(title)
    if existing:
        return {
            "key": existing["key"],
            "title": existing.get("title", ""),
            "duplicate": True,
            "match_type": existing.get("match_type", "title_similarity"),
            "similarity": existing.get("similarity", 0),
            "message": f"Similar item already exists in library",
        }
```

- [ ] **Step 7: Run all duplicate tests**

Run: `python -m pytest tests/test_duplicates.py -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/zotero_mcp/web_client.py tests/test_duplicates.py
git commit -m "feat: add duplicate checks to create_item_from_url and create_item_manual"
```

---

### Task 3: Add `find_duplicates` audit tool

**Files:**
- Modify: `src/zotero_mcp/web_client.py`
- Modify: `src/zotero_mcp/server.py`
- Modify: `src/zotero_mcp/capabilities.py`
- Test: `tests/test_duplicates.py`

- [ ] **Step 1: Write failing test for find_duplicates**

Add to `tests/test_duplicates.py`:

```python
def test_find_duplicates_groups_by_doi():
    """find_duplicates groups items with identical DOIs."""
    client = _make_client()
    items = [
        {"key": "A1", "title": "Paper One", "DOI": "10.1234/same", "date": "2024",
         "item_type": "journalArticle", "creators": "", "collections": [], "tags": [], "version": 1},
        {"key": "A2", "title": "Paper One (copy)", "DOI": "10.1234/same", "date": "2024",
         "item_type": "journalArticle", "creators": "", "collections": [], "tags": [], "version": 2},
        {"key": "B1", "title": "Unique Paper", "DOI": "10.5678/unique", "date": "2024",
         "item_type": "journalArticle", "creators": "", "collections": [], "tags": [], "version": 3},
    ]
    import unittest.mock as mock
    with mock.patch.object(client, "search_items", return_value=items):
        result = client.find_duplicates(limit=100)

    assert result["total_groups"] >= 1
    doi_groups = [g for g in result["duplicate_groups"] if g["match_type"] == "doi"]
    assert len(doi_groups) == 1
    assert doi_groups[0]["doi"] == "10.1234/same"
    assert len(doi_groups[0]["items"]) == 2


def test_find_duplicates_groups_by_title_similarity():
    """find_duplicates groups items with similar titles (no DOI)."""
    client = _make_client()
    items = [
        {"key": "C1", "title": "Gastric Intestinal Metaplasia Detection", "DOI": "", "date": "2024",
         "item_type": "journalArticle", "creators": "", "collections": [], "tags": [], "version": 1},
        {"key": "C2", "title": "Gastric intestinal metaplasia detection: a review", "DOI": "", "date": "2024",
         "item_type": "journalArticle", "creators": "", "collections": [], "tags": [], "version": 2},
        {"key": "D1", "title": "Completely Different Topic", "DOI": "", "date": "2024",
         "item_type": "journalArticle", "creators": "", "collections": [], "tags": [], "version": 3},
    ]
    import unittest.mock as mock
    with mock.patch.object(client, "search_items", return_value=items):
        result = client.find_duplicates(limit=100)

    title_groups = [g for g in result["duplicate_groups"] if g["match_type"] == "title_similarity"]
    assert len(title_groups) == 1
    assert len(title_groups[0]["items"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_duplicates.py::test_find_duplicates_groups_by_doi tests/test_duplicates.py::test_find_duplicates_groups_by_title_similarity -v`
Expected: FAIL with `AttributeError: 'WebClient' object has no attribute 'find_duplicates'`

- [ ] **Step 3: Implement `find_duplicates` in WebClient**

Add to `src/zotero_mcp/web_client.py` in the `WebClient` class, after `_check_duplicate_title`:

```python
def find_duplicates(
    self,
    collection_key: str | None = None,
    limit: int = 100,
    title_threshold: float = 0.85,
) -> dict:
    """Scan library for duplicate items.

    Groups by exact DOI match, then clusters remaining items by
    normalized title similarity.

    Args:
        collection_key: Optional collection to scope the scan.
        limit: Max items to scan.
        title_threshold: Similarity ratio for title matching (0-1).

    Returns:
        Dict with duplicate_groups, total_groups, total_duplicate_items.
    """
    import re as _re
    from difflib import SequenceMatcher

    def _normalize(t: str) -> str:
        t = t.lower().strip()
        t = _re.sub(r"[^\w\s]", "", t)
        t = _re.sub(r"\s+", " ", t)
        return t

    # Fetch items
    if collection_key:
        if self._local:
            try:
                items = self._local.get_collection_items(collection_key, limit)
            except RuntimeError:
                items = self.get_collection_items(collection_key, limit)
        else:
            items = self.get_collection_items(collection_key, limit)
    else:
        if self._local:
            try:
                items = self._local.search_items("", limit)
            except RuntimeError:
                items = self.search_items("", limit)
        else:
            items = self.search_items("", limit)

    groups: list[dict] = []

    # Phase 1: Group by exact DOI
    doi_map: dict[str, list[dict]] = {}
    no_doi_items: list[dict] = []
    for item in items:
        doi = (item.get("DOI") or "").strip().lower()
        if doi:
            doi_map.setdefault(doi, []).append(item)
        else:
            no_doi_items.append(item)

    for doi, group_items in doi_map.items():
        if len(group_items) >= 2:
            groups.append({
                "match_type": "doi",
                "doi": doi,
                "items": [
                    {"key": i["key"], "title": i["title"], "date": i.get("date", "")}
                    for i in group_items
                ],
            })

    # Phase 2: Cluster remaining items by title similarity
    used: set[str] = set()
    for i, item_a in enumerate(no_doi_items):
        if item_a["key"] in used:
            continue
        norm_a = _normalize(item_a.get("title", ""))
        if not norm_a:
            continue
        cluster = [item_a]
        for item_b in no_doi_items[i + 1:]:
            if item_b["key"] in used:
                continue
            norm_b = _normalize(item_b.get("title", ""))
            if not norm_b:
                continue
            ratio = SequenceMatcher(None, norm_a, norm_b).ratio()
            if ratio >= title_threshold:
                cluster.append(item_b)
                used.add(item_b["key"])
        if len(cluster) >= 2:
            used.add(item_a["key"])
            groups.append({
                "match_type": "title_similarity",
                "similarity": round(
                    SequenceMatcher(
                        None,
                        _normalize(cluster[0].get("title", "")),
                        _normalize(cluster[1].get("title", "")),
                    ).ratio(),
                    3,
                ),
                "items": [
                    {"key": i["key"], "title": i["title"], "date": i.get("date", "")}
                    for i in cluster
                ],
            })

    total_dup_items = sum(len(g["items"]) for g in groups)
    return {
        "duplicate_groups": groups,
        "total_groups": len(groups),
        "total_duplicate_items": total_dup_items,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_duplicates.py -v`
Expected: All tests PASS

- [ ] **Step 5: Add `find_duplicates` tool to server.py**

Add to `src/zotero_mcp/server.py`, after the `batch_organize` tool:

```python
@mcp.tool(
    description=(
        "Scan the Zotero library for duplicate items. Groups by exact DOI "
        "match and by title similarity. Use to audit and clean up the library. "
        "Returns duplicate groups with item keys for review."
    )
)
def find_duplicates(
    collection_key: str | None = None, limit: str | int = 100
) -> str:
    """Find duplicate items in the library or a collection."""
    limit_int = _clamp_limit(limit)
    if collection_key:
        _validate_key(collection_key, "collection_key")
        collection_key = collection_key.strip()
    result = _get_web().find_duplicates(collection_key, limit_int)
    return json.dumps(result, ensure_ascii=False)
```

- [ ] **Step 6: Update capabilities.py TOOL_MODES**

Add to `TOOL_MODES` dict in `src/zotero_mcp/capabilities.py`:

```python
"find_duplicates": ["cloud_crud"],
```

- [ ] **Step 7: Update test_server.py**

Add `"find_duplicates"` to the `expected` set and update the count (should now be 20 if Feature 1 was already added, or 19 if building this standalone — adjust based on current state).

- [ ] **Step 8: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 9: Commit**

```bash
git add src/zotero_mcp/web_client.py src/zotero_mcp/server.py src/zotero_mcp/capabilities.py tests/
git commit -m "feat: add find_duplicates tool and extend create paths with duplicate checks"
```
