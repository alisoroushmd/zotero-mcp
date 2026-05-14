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
    mock_local = _mock_local_client(attachment_path="/Users/test/Zotero/storage/ATT001/paper.pdf")
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


def test_get_pdf_content_finds_imported_url_pdf_in_storage_dir():
    """imported_url attachment with no explicit path is found via ZOTERO_DATA_DIR/storage/KEY/filename."""
    item_data = {
        "key": "LXDHDBYE",
        "title": "Canals 2023",
        "DOI": "10.1016/j.compmedimag.2022.102170",
        "extra": "",
    }
    children = [
        {
            "key": "UE89AVJC",
            "itemType": "attachment",
            "contentType": "application/pdf",
            "linkMode": "imported_url",
            "path": "",
            "filename": "Canals2023.pdf",
        }
    ]

    mock_web = _mock_web_client(item_data, children)
    mock_local = MagicMock()
    mock_local.get_item.return_value = item_data
    mock_local.get_children.return_value = children
    # Local API returns None for path (imported_url with no explicit path field)
    mock_local.get_attachment_path.return_value = (
        "/Users/LEGION/Zotero/storage/UE89AVJC/Canals2023.pdf"
    )

    import zotero_mcp.server as srv

    with (
        patch.object(srv, "_get_web", return_value=mock_web),
        patch.object(srv, "_get_local", return_value=mock_local),
    ):
        result = json.loads(srv.get_pdf_content("LXDHDBYE"))

    assert result["content_source"] == "local_pdf"
    assert "UE89AVJC" in result["pdf_path"]
    assert result["attachment_key"] == "UE89AVJC"


def test_get_pdf_content_not_found_includes_attempted_routes():
    """not_found response includes which routes were tried, to aid debugging."""
    item_data = {
        "key": "LXDHDBYE",
        "title": "Canals 2023",
        "DOI": "10.1016/j.compmedimag.2022.102170",
        "url": "https://www.sciencedirect.com/article/pii/S0895611122001537",
        "extra": "",
    }
    children = [
        {
            "key": "UE89AVJC",
            "itemType": "attachment",
            "contentType": "application/pdf",
            "linkMode": "imported_url",
            "path": "",
            "filename": "Canals2023.pdf",
        }
    ]

    mock_web = _mock_web_client(item_data, children)
    mock_web.download_attachment.side_effect = Exception("403 Forbidden")
    mock_web._download_free_pdf.side_effect = Exception("Unpaywall: no OA version")
    mock_local = MagicMock()
    mock_local.get_item.return_value = item_data
    mock_local.get_children.return_value = children
    mock_local.get_attachment_path.return_value = None

    import zotero_mcp.server as srv

    with (
        patch.object(srv, "_get_web", return_value=mock_web),
        patch.object(srv, "_get_local", return_value=mock_local),
    ):
        result = json.loads(srv.get_pdf_content("LXDHDBYE"))

    assert result["content_source"] == "not_found"
    assert "routes_tried" in result


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


def test_get_pdf_content_with_extract_text(tmp_path):
    """extract_text=True adds text/page_count/char_count to result."""
    from unittest.mock import patch

    item_data = {
        "key": "PDF001",
        "DOI": "10.1234/test",
        "title": "Test PDF Paper",
        "itemType": "journalArticle",
        "extra": "",
    }
    children = [
        {
            "key": "ATT001",
            "contentType": "application/pdf",
            "linkMode": "stored_file",
            "filename": "test.pdf",
        }
    ]

    mock_web = _mock_web_client(item_data, children)
    pdf_bytes = b"%PDF-1.4 test content for extraction" + b" " * 100
    mock_web.download_attachment.return_value = pdf_bytes

    mock_local = _mock_local_client()
    mock_local.get_children.return_value = children
    # No local path → fall through to web download
    mock_local.get_attachment_path.return_value = None

    import zotero_mcp.server as srv

    extracted_text = "hello world paper text about gastric cancer findings"

    with (
        patch.object(srv, "_get_web", return_value=mock_web),
        patch.object(srv, "_get_local", return_value=mock_local),
        patch(
            "zotero_mcp.text_extractor.extract_text_from_pdf",
            return_value=extracted_text,
        ),
    ):
        result = json.loads(srv.get_pdf_content("PDF001", extract_text=True))

    assert "text" in result
    assert result["page_count"] >= 1
    assert result["char_count"] > 0
    assert result["char_count"] == len(extracted_text)
