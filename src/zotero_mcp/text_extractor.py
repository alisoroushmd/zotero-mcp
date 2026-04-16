"""Full-text PDF extraction and FTS5 indexing for Zotero papers.

Extracts text from PDFs using pypdf and indexes it in a SQLite FTS5
virtual table for fast full-text search across the library.
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


def extract_text_from_pdf(source: str | bytes) -> str | None:
    """Extract text content from a PDF file path or raw bytes.

    Args:
        source: Either a file path (str) or PDF bytes.

    Returns:
        Extracted text string, or None if extraction fails or no text found.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.error(
            "pypdf is required for full-text extraction. "
            "Install with: pip install 'zotero-mcp[fulltext]'"
        )
        return None

    try:
        if isinstance(source, bytes):
            reader = PdfReader(io.BytesIO(source))
        else:
            reader = PdfReader(source)

        pages_text: list[str] = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages_text.append(text)

        if not pages_text:
            return None

        combined = "\n\n".join(pages_text)
        return combined if combined.strip() else None
    except Exception as exc:
        logger.warning("PDF text extraction failed: %s", exc)
        return None


def index_paper_text(store, doi: str, text: str) -> None:
    """Insert extracted text into the FTS5 index and record state.

    Args:
        store: GraphStore instance with fulltext tables.
        doi: Paper DOI.
        text: Extracted text content.
    """
    page_count = text.count("\n\n") + 1  # rough page estimate
    char_count = len(text)
    store.upsert_fulltext(doi, text, page_count, char_count)


def search_text(store, query: str, limit: int = 20) -> list[dict]:
    """Search indexed full text using FTS5.

    Args:
        store: GraphStore instance with fulltext tables.
        query: FTS5 search query string.
        limit: Maximum results to return.

    Returns:
        List of match dicts with doi, title, zotero_key, snippet, rank.
    """
    return store.search_fulltext(query, limit)
