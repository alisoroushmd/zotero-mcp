# Semantic Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add semantic (vector) search over Zotero library items using local embeddings (sentence-transformers) and ChromaDB, with incremental sync from the Zotero API.

**Architecture:** New `SemanticIndex` class in `semantic.py` handles embedding, storage, and search. Three new tools: `semantic_search`, `build_index`, `rebuild_index`. All gated behind `[semantic]` optional extra with graceful degradation.

**Tech Stack:** sentence-transformers (all-MiniLM-L6-v2), chromadb (SQLite backend), no external API.

---

### Task 1: Add `[semantic]` optional extra to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the semantic extra**

In `pyproject.toml`, add to `[project.optional-dependencies]`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "respx>=0.22.0",
]
semantic = [
    "sentence-transformers>=2.0",
    "chromadb>=0.4",
]
```

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add [semantic] optional extra for vector search dependencies"
```

---

### Task 2: Create SemanticIndex class

**Files:**
- Create: `src/zotero_mcp/semantic.py`
- Test: `tests/test_semantic.py` (new)

- [ ] **Step 1: Write failing test for graceful degradation (no deps)**

Create `tests/test_semantic.py`:

```python
"""Tests for semantic search — SemanticIndex class.

These tests use mocks for sentence-transformers and chromadb to avoid
requiring the [semantic] extra during CI. Tests that verify actual
embedding quality should be run manually with the extra installed.
"""

from unittest.mock import MagicMock, patch
import sys


def test_import_error_gives_clear_message():
    """SemanticIndex raises clear error when deps not installed."""
    # Temporarily remove chromadb from sys.modules if present
    hidden = {}
    for mod_name in list(sys.modules.keys()):
        if "chromadb" in mod_name or "sentence_transformers" in mod_name:
            hidden[mod_name] = sys.modules.pop(mod_name)

    import importlib
    # Mock the imports to raise ImportError
    with patch.dict(sys.modules, {"chromadb": None, "sentence_transformers": None}):
        try:
            # Force reimport
            if "zotero_mcp.semantic" in sys.modules:
                del sys.modules["zotero_mcp.semantic"]
            from zotero_mcp.semantic import SemanticIndex
            idx = SemanticIndex.__new__(SemanticIndex)
            # The error should come when trying to use it
            import pytest
            with pytest.raises(ImportError, match="pip install zotero-mcp\\[semantic\\]"):
                idx._check_deps()
        finally:
            # Restore
            sys.modules.update(hidden)
```

- [ ] **Step 2: Write failing tests for core operations (mocked deps)**

Add to `tests/test_semantic.py`:

```python
def _make_mock_index():
    """Create a SemanticIndex with mocked dependencies."""
    mock_model = MagicMock()
    mock_model.encode.return_value = [[0.1, 0.2, 0.3, 0.4]]  # fake embedding

    mock_collection = MagicMock()
    mock_collection.count.return_value = 0
    mock_collection.query.return_value = {
        "ids": [["ABC123"]],
        "distances": [[0.15]],
        "metadatas": [[{
            "title": "Test Paper",
            "date": "2024",
            "collections": "COL1",
        }]],
    }

    mock_chroma = MagicMock()
    mock_chroma.get_or_create_collection.return_value = mock_collection

    with patch("zotero_mcp.semantic.SentenceTransformer", return_value=mock_model), \
         patch("zotero_mcp.semantic.chromadb") as mock_chromadb:
        mock_chromadb.PersistentClient.return_value = mock_chroma
        from zotero_mcp.semantic import SemanticIndex
        idx = SemanticIndex(db_path="/tmp/test_semantic")

    idx._model = mock_model
    idx._collection = mock_collection
    return idx, mock_model, mock_collection


def test_add_items_embeds_title_and_abstract():
    """add_items concatenates title + abstract for embedding."""
    idx, mock_model, mock_collection = _make_mock_index()

    items = [
        {
            "key": "ABC123",
            "title": "Test Paper",
            "abstractNote": "This is the abstract.",
            "date": "2024",
            "collections": ["COL1"],
        }
    ]
    count = idx.add_items(items)
    assert count == 1

    # Check that embed was called with title + abstract
    call_args = mock_model.encode.call_args
    text = call_args[0][0][0]  # first positional arg, first item in batch
    assert "Test Paper" in text
    assert "This is the abstract." in text


def test_search_returns_ranked_results():
    """search returns items ranked by similarity."""
    idx, mock_model, mock_collection = _make_mock_index()

    results = idx.search("gastric metaplasia", limit=5)
    assert len(results) == 1
    assert results[0]["key"] == "ABC123"
    assert "score" in results[0]


def test_add_items_skips_empty_titles():
    """add_items skips items with no title and no abstract."""
    idx, mock_model, mock_collection = _make_mock_index()

    items = [
        {"key": "A1", "title": "", "abstractNote": "", "date": "2024", "collections": []},
        {"key": "A2", "title": "Real Paper", "abstractNote": "Content", "date": "2024", "collections": []},
    ]
    count = idx.add_items(items)
    assert count == 1  # only A2 should be embedded
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_semantic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zotero_mcp.semantic'`

- [ ] **Step 4: Implement SemanticIndex**

Create `src/zotero_mcp/semantic.py`:

```python
"""Semantic search over Zotero library using local embeddings + ChromaDB."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Lazy imports — these are optional deps behind [semantic] extra
try:
    import chromadb
    from sentence_transformers import SentenceTransformer

    _DEPS_AVAILABLE = True
except ImportError:
    chromadb = None  # type: ignore[assignment]
    SentenceTransformer = None  # type: ignore[assignment,misc]
    _DEPS_AVAILABLE = False

DEFAULT_MODEL = "all-MiniLM-L6-v2"
COLLECTION_NAME = "zotero_items"
VERSION_KEY = "__library_version__"


class SemanticIndex:
    """Embedding-based search index for Zotero items.

    Stores title+abstract embeddings in ChromaDB (SQLite backend).
    Supports incremental sync via Zotero's library version.
    """

    def __init__(
        self,
        db_path: str | None = None,
        model_name: str = DEFAULT_MODEL,
    ) -> None:
        self._check_deps()

        if db_path is None:
            db_path = str(Path.home() / ".zotero-mcp" / "semantic")
        Path(db_path).mkdir(parents=True, exist_ok=True)

        self._model = SentenceTransformer(model_name)
        self._chroma = chromadb.PersistentClient(path=db_path)
        self._collection = self._chroma.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _check_deps() -> None:
        """Raise clear error if optional dependencies are missing."""
        if not _DEPS_AVAILABLE:
            raise ImportError(
                "Semantic search requires additional dependencies. "
                "Install with: pip install zotero-mcp[semantic]"
            )

    def _make_text(self, item: dict) -> str:
        """Concatenate title + abstract for embedding."""
        title = item.get("title", "")
        abstract = item.get("abstractNote", "")
        parts = [p for p in [title, abstract] if p.strip()]
        return " ".join(parts)

    def add_items(self, items: list[dict]) -> int:
        """Embed and store items in the index.

        Args:
            items: List of Zotero item dicts (must have key, title).

        Returns:
            Number of items actually indexed (skips empty title+abstract).
        """
        texts: list[str] = []
        ids: list[str] = []
        metadatas: list[dict] = []

        for item in items:
            text = self._make_text(item)
            if not text.strip():
                continue
            texts.append(text)
            ids.append(item["key"])
            metadatas.append({
                "title": item.get("title", ""),
                "date": item.get("date", ""),
                "collections": ",".join(item.get("collections", [])),
            })

        if not texts:
            return 0

        embeddings = self._model.encode(texts, show_progress_bar=False).tolist()
        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        return len(texts)

    def search(
        self,
        query: str,
        limit: int = 10,
        collection_filter: str | None = None,
    ) -> list[dict]:
        """Search the index by semantic similarity.

        Args:
            query: Natural language search query.
            limit: Max results to return.
            collection_filter: Optional collection key to filter results.

        Returns:
            List of dicts with key, title, score, date, collections.
        """
        query_embedding = self._model.encode([query], show_progress_bar=False).tolist()

        where_filter = None
        if collection_filter:
            where_filter = {"collections": {"$contains": collection_filter}}

        results = self._collection.query(
            query_embeddings=query_embedding,
            n_results=limit,
            where=where_filter,
        )

        items: list[dict] = []
        for i, item_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i]
            metadata = results["metadatas"][0][i]
            items.append({
                "key": item_id,
                "title": metadata.get("title", ""),
                "score": round(1.0 - distance, 4),  # cosine: lower distance = higher similarity
                "date": metadata.get("date", ""),
                "collections": metadata.get("collections", "").split(","),
            })
        return items

    def get_library_version(self) -> int:
        """Get the stored library version watermark."""
        try:
            result = self._collection.get(ids=[VERSION_KEY])
            if result["metadatas"] and result["metadatas"][0]:
                return int(result["metadatas"][0].get("version", 0))
        except Exception:
            pass
        return 0

    def set_library_version(self, version: int) -> None:
        """Store the library version watermark."""
        self._collection.upsert(
            ids=[VERSION_KEY],
            embeddings=[[0.0] * self._model.get_sentence_embedding_dimension()],
            metadatas=[{"version": str(version), "title": "__version_marker__"}],
        )

    def sync(self, web_client, since_version: int) -> int:
        """Incremental sync: fetch items changed since version, upsert.

        Args:
            web_client: WebClient instance for fetching items.
            since_version: Library version to sync from.

        Returns:
            Number of items synced.
        """
        from zotero_mcp.local_client import _format_summary

        items: list[dict] = []
        start = 0
        while True:
            resp = web_client._web_client.get(
                "/items",
                params={
                    "since": since_version,
                    "limit": 100,
                    "start": start,
                    "itemType": "-attachment || -note",
                },
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            # Get full data for embedding (need abstractNote)
            for item_json in batch:
                data = item_json.get("data", item_json)
                items.append(data)
            start += len(batch)
            if len(batch) < 100:
                break

        if not items:
            return 0

        count = self.add_items(items)

        # Update version watermark
        new_version = int(resp.headers.get("Last-Modified-Version", since_version))
        self.set_library_version(new_version)

        return count

    def rebuild(self, web_client) -> int:
        """Full rebuild: drop index and re-index entire library.

        Args:
            web_client: WebClient instance for fetching items.

        Returns:
            Number of items indexed.
        """
        # Drop and recreate collection
        self._chroma.delete_collection(COLLECTION_NAME)
        self._collection = self._chroma.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

        # Fetch all items
        items: list[dict] = []
        start = 0
        while True:
            resp = web_client._web_client.get(
                "/items",
                params={
                    "limit": 100,
                    "start": start,
                    "itemType": "-attachment || -note",
                },
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for item_json in batch:
                data = item_json.get("data", item_json)
                items.append(data)
            start += len(batch)
            if len(batch) < 100:
                break

        count = self.add_items(items)

        new_version = int(resp.headers.get("Last-Modified-Version", 0))
        self.set_library_version(new_version)

        return count

    def item_count(self) -> int:
        """Number of items in the index (excluding version marker)."""
        total = self._collection.count()
        # Subtract version marker if it exists
        try:
            self._collection.get(ids=[VERSION_KEY])
            return max(0, total - 1)
        except Exception:
            return total
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_semantic.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/zotero_mcp/semantic.py tests/test_semantic.py
git commit -m "feat: add SemanticIndex class with embedding, search, and incremental sync"
```

---

### Task 3: Add semantic search tools to server.py

**Files:**
- Modify: `src/zotero_mcp/server.py`
- Modify: `src/zotero_mcp/capabilities.py`

- [ ] **Step 1: Write failing test for tool graceful degradation**

Add to `tests/test_semantic.py`:

```python
def test_semantic_search_tool_without_deps_gives_clear_error():
    """semantic_search tool returns install instructions when deps missing."""
    import json
    from unittest.mock import patch

    import zotero_mcp.server as srv

    with patch("zotero_mcp.server._SEMANTIC_AVAILABLE", False):
        result = json.loads(srv.semantic_search("test query"))

    assert "error" in result
    assert "pip install" in result["error"]
```

- [ ] **Step 2: Implement the three semantic tools**

Add to `src/zotero_mcp/server.py`, near the top after the existing imports:

```python
# Check if semantic search deps are available (lazy, no import-time failure)
try:
    from zotero_mcp.semantic import SemanticIndex  # noqa: F401
    _SEMANTIC_AVAILABLE = True
except ImportError:
    _SEMANTIC_AVAILABLE = False

_semantic_index: "SemanticIndex | None" = None
_semantic_lock = threading.Lock()
```

Then add the tools after the existing read tools section:

```python
def _get_semantic_index() -> "SemanticIndex":
    """Lazy-initialize the semantic index (thread-safe)."""
    global _semantic_index
    if _semantic_index is not None:
        return _semantic_index
    with _semantic_lock:
        if _semantic_index is not None:
            return _semantic_index
        from zotero_mcp.semantic import SemanticIndex
        db_path = os.environ.get("ZOTERO_SEMANTIC_DB")
        _semantic_index = SemanticIndex(db_path=db_path)
        return _semantic_index


@mcp.tool(
    description=(
        "Search Zotero library by meaning, not just keywords. Finds "
        "semantically similar items using embeddings. Requires the "
        "[semantic] extra: pip install zotero-mcp[semantic]. "
        "Call build_index first if the index hasn't been built yet."
    )
)
def semantic_search(
    query: str, limit: str | int = 10, collection_key: str | None = None
) -> str:
    """Search library by semantic similarity.

    Args:
        query: Natural language search query.
        limit: Max results.
        collection_key: Optional collection to scope the search.

    Returns:
        JSON with ranked results and similarity scores.
    """
    if not _SEMANTIC_AVAILABLE:
        return json.dumps({
            "error": (
                "Semantic search requires additional dependencies. "
                "Install with: pip install zotero-mcp[semantic]"
            )
        })

    idx = _get_semantic_index()

    # Auto-sync if library has advanced
    try:
        stored_version = idx.get_library_version()
        web = _get_web()
        resp = web._web_client.get("/items", params={"limit": 0})
        current_version = int(resp.headers.get("Last-Modified-Version", 0))
        if current_version > stored_version:
            synced = idx.sync(web, stored_version)
            logger.info("Auto-synced %d items to semantic index", synced)
    except Exception:
        pass  # Sync failure shouldn't block search

    results = idx.search(
        query, _clamp_limit(limit, lo=1, hi=50), collection_key
    )

    return json.dumps({
        "results": results,
        "index_version": idx.get_library_version(),
        "items_indexed": idx.item_count(),
    }, ensure_ascii=False)


@mcp.tool(
    description=(
        "Build the semantic search index. Fetches all items from Zotero "
        "and embeds title + abstract. Run this once before using "
        "semantic_search. Safe to run multiple times (idempotent). "
        "Requires: pip install zotero-mcp[semantic]"
    )
)
def build_index() -> str:
    """Build or update the semantic search index."""
    if not _SEMANTIC_AVAILABLE:
        return json.dumps({
            "error": (
                "Semantic search requires additional dependencies. "
                "Install with: pip install zotero-mcp[semantic]"
            )
        })

    import time as _time

    start = _time.time()
    idx = _get_semantic_index()
    web = _get_web()

    stored = idx.get_library_version()
    if stored > 0:
        count = idx.sync(web, stored)
    else:
        count = idx.rebuild(web)

    duration = round(_time.time() - start, 1)

    return json.dumps({
        "items_indexed": count,
        "total_in_index": idx.item_count(),
        "duration_seconds": duration,
        "index_version": idx.get_library_version(),
    })


@mcp.tool(
    description=(
        "Rebuild the semantic search index from scratch. Drops the "
        "existing index and re-indexes the entire library. Use when "
        "the index seems out of sync. Requires: pip install zotero-mcp[semantic]"
    )
)
def rebuild_index() -> str:
    """Drop and rebuild the semantic search index."""
    if not _SEMANTIC_AVAILABLE:
        return json.dumps({
            "error": (
                "Semantic search requires additional dependencies. "
                "Install with: pip install zotero-mcp[semantic]"
            )
        })

    import time as _time

    start = _time.time()
    idx = _get_semantic_index()
    web = _get_web()
    count = idx.rebuild(web)
    duration = round(_time.time() - start, 1)

    return json.dumps({
        "items_indexed": count,
        "total_in_index": idx.item_count(),
        "duration_seconds": duration,
        "index_version": idx.get_library_version(),
    })
```

- [ ] **Step 3: Update capabilities.py TOOL_MODES**

Add to `TOOL_MODES` dict in `src/zotero_mcp/capabilities.py`:

```python
"semantic_search": ["any_read"],
"build_index": ["cloud_crud"],
"rebuild_index": ["cloud_crud"],
```

- [ ] **Step 4: Update test_server.py**

Add `"semantic_search"`, `"build_index"`, `"rebuild_index"` to the `expected` set and update the tool count to 27 (final count with all features).

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/zotero_mcp/server.py src/zotero_mcp/capabilities.py tests/test_semantic.py tests/test_server.py
git commit -m "feat: add semantic_search, build_index, rebuild_index tools"
```
