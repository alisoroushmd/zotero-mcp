"""Tests for OpenAlexClient — OpenAlex API wrapper."""

import httpx
import pytest
import respx

from zotero_mcp.openalex_client import OpenAlexClient

OPENALEX_BASE = "https://api.openalex.org"


@respx.mock
def test_get_work_returns_metadata():
    """get_work returns work metadata for a valid DOI."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/test").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W12345",
                "doi": "https://doi.org/10.1234/test",
                "title": "Test Paper",
                "is_retracted": False,
                "cited_by_count": 42,
                "cited_by_api_url": f"{OPENALEX_BASE}/works?filter=cites:W12345",
                "referenced_works": ["https://openalex.org/W99999"],
            },
        )
    )
    client = OpenAlexClient()
    result = client.get_work("10.1234/test")
    assert result is not None
    assert result["title"] == "Test Paper"
    assert result["is_retracted"] is False
    assert result["cited_by_count"] == 42


@respx.mock
def test_get_work_returns_none_for_404():
    """get_work returns None when DOI not found."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/nonexistent").mock(
        return_value=httpx.Response(404)
    )
    client = OpenAlexClient()
    result = client.get_work("10.1234/nonexistent")
    assert result is None


@respx.mock
def test_get_work_returns_retracted_flag():
    """get_work correctly reports retracted papers."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/retracted").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W11111",
                "doi": "https://doi.org/10.1234/retracted",
                "title": "Retracted Paper",
                "is_retracted": True,
                "cited_by_count": 5,
                "cited_by_api_url": "",
                "referenced_works": [],
            },
        )
    )
    client = OpenAlexClient()
    result = client.get_work("10.1234/retracted")
    assert result["is_retracted"] is True


@respx.mock
def test_get_work_strips_doi_prefix():
    """get_work handles DOIs with https://doi.org/ prefix."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/test").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W12345",
                "doi": "https://doi.org/10.1234/test",
                "title": "Test Paper",
                "is_retracted": False,
                "cited_by_count": 10,
            },
        )
    )
    client = OpenAlexClient()
    result = client.get_work("https://doi.org/10.1234/test")
    assert result is not None
    assert result["title"] == "Test Paper"
