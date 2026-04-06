"""Tests for get_pdf_content tool — content routing logic."""

import json
from unittest.mock import MagicMock, patch


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


def test_get_pdf_content_returns_pmcid_when_available():
    """If item has a PMID that maps to a PMCID, return PMC source."""
    item_data = {
        "key": "ABC123",
        "title": "Test Paper",
        "DOI": "10.1234/test",
        "extra": "PMID: 12345678",
    }

    mock_web = _mock_web_client(item_data)
    mock_web.resolve_pmid_to_pmcid.return_value = "PMC9046468"
    mock_local = _mock_local_client()

    import zotero_mcp.server as srv

    with (
        patch.object(srv, "_get_web", return_value=mock_web),
        patch.object(srv, "_get_local", return_value=mock_local),
    ):
        result = json.loads(srv.get_pdf_content("ABC123"))

    assert result["content_source"] == "pmc"
    assert result["pmcid"] == "PMC9046468"


def test_get_pdf_content_falls_through_on_pmc_failure():
    """If PMC lookup fails, fall through to PDF paths."""
    item_data = {
        "key": "ABC123",
        "title": "Test Paper",
        "DOI": "10.1234/test",
        "url": "https://example.com/paper",
        "extra": "PMID: 12345678",
    }

    mock_web = _mock_web_client(item_data, children=[])
    mock_web.resolve_pmid_to_pmcid.side_effect = Exception("Network timeout")
    mock_local = _mock_local_client()

    import zotero_mcp.server as srv

    with (
        patch.object(srv, "_get_web", return_value=mock_web),
        patch.object(srv, "_get_local", return_value=mock_local),
    ):
        result = json.loads(srv.get_pdf_content("ABC123"))

    # Should fall through to not_found since no PDF attachments either
    assert result["content_source"] == "not_found"
    assert result["doi"] == "10.1234/test"


def test_get_pdf_content_returns_local_path():
    """If no PMCID but local PDF exists, return local file path."""
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
            "linkMode": "imported_file",
            "path": "storage/ATT001/paper.pdf",
        }
    ]

    mock_web = _mock_web_client(item_data, children)
    mock_local = _mock_local_client(
        attachment_path="/Users/test/Zotero/storage/ATT001/paper.pdf"
    )
    mock_local.get_children.return_value = children

    import zotero_mcp.server as srv

    with (
        patch.object(srv, "_get_web", return_value=mock_web),
        patch.object(srv, "_get_local", return_value=mock_local),
    ):
        result = json.loads(srv.get_pdf_content("ABC123"))

    assert result["content_source"] == "local_pdf"
    assert "ATT001" in result["pdf_path"]


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

    with (
        patch.object(srv, "_get_web", return_value=mock_web),
        patch.object(srv, "_get_local", return_value=mock_local),
    ):
        result = json.loads(srv.get_pdf_content("ABC123"))

    assert result["content_source"] == "web_pdf"
    assert result["pdf_path"].endswith(".pdf")


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

    with (
        patch.object(srv, "_get_web", return_value=mock_web),
        patch.object(srv, "_get_local", return_value=mock_local),
    ):
        result = json.loads(srv.get_pdf_content("ABC123"))

    assert result["content_source"] == "not_found"
    assert result["doi"] == "10.1234/test"
