"""Tests for retraction and correction checks."""

import json
from unittest.mock import MagicMock, patch

import httpx
import respx

from zotero_mcp.web_client import WebClient

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

    with (
        patch.object(srv, "_get_web", return_value=mock_web),
        patch("zotero_mcp.openalex_client.OpenAlexClient", return_value=mock_openalex),
    ):
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

    with (
        patch.object(srv, "_get_web", return_value=mock_web),
        patch("zotero_mcp.openalex_client.OpenAlexClient", return_value=mock_openalex),
    ):
        result = json.loads(srv.check_retractions("DEF456"))

    assert result["retracted_count"] == 1
    assert result["results"][0]["retracted"] is True
    assert result["results"][0]["retraction_doi"] == "10.1234/retraction-notice"
