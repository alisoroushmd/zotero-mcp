"""Tests for retraction and correction checks."""

import httpx
import respx

from zotero_mcp.web_client import WebClient, WEB_BASE

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
