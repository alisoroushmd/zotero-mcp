"""Tests for full-text PDF extraction and FTS5 indexing."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from zotero_mcp.graph_store import GraphStore
from zotero_mcp.text_extractor import index_paper_text, search_text


@pytest.fixture
def tmp_db():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


def test_extract_text_from_pdf_bytes():
    """extract_text_from_pdf extracts text from PDF bytes via pypdf."""
    mock_page1 = MagicMock()
    mock_page1.extract_text.return_value = "Page one content about gastric cancer."
    mock_page2 = MagicMock()
    mock_page2.extract_text.return_value = "Page two discusses treatment options."

    mock_reader = MagicMock()
    mock_reader.pages = [mock_page1, mock_page2]

    with patch("zotero_mcp.text_extractor.PdfReader", return_value=mock_reader, create=True):
        # Patch the import inside the function
        import zotero_mcp.text_extractor as te

        original_func = te.extract_text_from_pdf

        def patched_extract(source):

            with patch.dict("sys.modules", {"pypdf": MagicMock()}):
                # Directly test the logic by mocking PdfReader at the point of use
                pass
            return original_func(source)

    # Simpler approach: mock at module level
    mock_pypdf = MagicMock()
    mock_reader_inst = MagicMock()
    page1 = MagicMock()
    page1.extract_text.return_value = "Page one content about gastric cancer."
    page2 = MagicMock()
    page2.extract_text.return_value = "Page two discusses treatment options."
    mock_reader_inst.pages = [page1, page2]
    mock_pypdf.PdfReader.return_value = mock_reader_inst

    with patch.dict("sys.modules", {"pypdf": mock_pypdf}):
        # Re-import to pick up the mock
        import importlib

        import zotero_mcp.text_extractor as te

        importlib.reload(te)

        result = te.extract_text_from_pdf(b"%PDF-fake-bytes")
        assert result is not None
        assert "gastric cancer" in result
        assert "treatment options" in result


def test_extract_text_returns_none_for_empty():
    """extract_text_from_pdf returns None when pages have no text."""
    mock_pypdf = MagicMock()
    mock_reader_inst = MagicMock()
    page1 = MagicMock()
    page1.extract_text.return_value = ""
    page2 = MagicMock()
    page2.extract_text.return_value = ""
    mock_reader_inst.pages = [page1, page2]
    mock_pypdf.PdfReader.return_value = mock_reader_inst

    with patch.dict("sys.modules", {"pypdf": mock_pypdf}):
        import importlib

        import zotero_mcp.text_extractor as te

        importlib.reload(te)

        result = te.extract_text_from_pdf(b"%PDF-fake-bytes")
        assert result is None


def test_index_and_search_fulltext(tmp_db):
    """Text can be indexed and found via FTS5 search."""
    store = GraphStore(tmp_db)
    # Add a paper to join against
    store.upsert_paper(
        doi="10.1234/gastric",
        zotero_key="ABC123",
        title="Gastric Cancer Risk",
        year=2024,
        authors="Smith J",
        openalex_id="W1",
    )

    index_paper_text(
        store,
        "10.1234/gastric",
        "This paper discusses gastric cancer risk stratification "
        "and intestinal metaplasia in a large cohort study.",
    )

    results = search_text(store, "gastric cancer")
    assert len(results) >= 1
    assert results[0]["doi"] == "10.1234/gastric"


def test_search_fulltext_returns_snippets(tmp_db):
    """FTS5 search results include highlighted snippets."""
    store = GraphStore(tmp_db)
    store.upsert_paper(
        doi="10.1/snippet",
        zotero_key="SNIP1",
        title="Snippet Test Paper",
        year=2024,
        authors="Doe J",
        openalex_id="W2",
    )

    store.upsert_fulltext(
        doi="10.1/snippet",
        content=(
            "Background: Helicobacter pylori infection is a major risk factor "
            "for gastric adenocarcinoma. This study evaluates novel biomarkers "
            "for early detection of premalignant gastric conditions."
        ),
        page_count=1,
        char_count=200,
    )

    results = store.search_fulltext("Helicobacter pylori", limit=5)
    assert len(results) >= 1
    match = results[0]
    assert match["doi"] == "10.1/snippet"
    assert match["title"] == "Snippet Test Paper"
    assert match["zotero_key"] == "SNIP1"
    # FTS5 snippet wraps matches in <b> tags
    assert "<b>" in match["snippet"]
    assert "rank" in match


def test_search_fulltext_no_results(tmp_db):
    """FTS5 search returns empty list for non-matching query."""
    store = GraphStore(tmp_db)
    store.upsert_fulltext(
        doi="10.1/only",
        content="This paper is about machine learning in radiology.",
        page_count=1,
        char_count=50,
    )

    results = store.search_fulltext("quantum computing")
    assert results == []


def test_get_indexed_dois(tmp_db):
    """get_indexed_dois returns the set of DOIs that have been indexed."""
    store = GraphStore(tmp_db)

    # Initially empty
    assert store.get_indexed_dois() == set()

    # Index two papers
    store.upsert_fulltext("10.1/a", "Content A", 1, 10)
    store.upsert_fulltext("10.1/b", "Content B", 2, 20)

    indexed = store.get_indexed_dois()
    assert indexed == {"10.1/a", "10.1/b"}


def test_delete_fulltext(tmp_db):
    """delete_fulltext removes from both FTS and state tables."""
    store = GraphStore(tmp_db)
    store.upsert_fulltext("10.1/del", "Some content to delete", 1, 25)

    assert "10.1/del" in store.get_indexed_dois()

    store.delete_fulltext("10.1/del")

    assert "10.1/del" not in store.get_indexed_dois()
    results = store.search_fulltext("content delete")
    assert results == []


def test_upsert_fulltext_replaces_existing(tmp_db):
    """Upserting the same DOI replaces the content."""
    store = GraphStore(tmp_db)
    store.upsert_fulltext("10.1/up", "Original content about oncology", 1, 30)
    store.upsert_fulltext("10.1/up", "Replacement content about cardiology", 1, 35)

    # Old content should not match
    old_results = store.search_fulltext("oncology")
    assert old_results == []

    # New content should match
    new_results = store.search_fulltext("cardiology")
    assert len(new_results) == 1
    assert new_results[0]["doi"] == "10.1/up"


def test_build_fulltext_index_incremental(tmp_db):
    """Incremental build skips already-indexed DOIs."""
    store = GraphStore(tmp_db)

    # Pre-index one DOI
    store.upsert_fulltext("10.1/already", "Already indexed content", 1, 25)

    indexed = store.get_indexed_dois()
    assert "10.1/already" in indexed

    # Simulate items from library
    items = [
        {"key": "K1", "DOI": "10.1/already"},
        {"key": "K2", "DOI": "10.1/new"},
    ]

    # Filter like the tool does (incremental mode)
    to_process = [it for it in items if it["DOI"] not in indexed]
    assert len(to_process) == 1
    assert to_process[0]["DOI"] == "10.1/new"
