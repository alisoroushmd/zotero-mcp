"""Tests for citation graph — citing works and references via OpenAlex."""

import httpx
import respx

from zotero_mcp.openalex_client import OPENALEX_BASE, OpenAlexClient


@respx.mock
def test_get_citing_works_returns_list():
    """get_citing_works returns recent citing papers."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/test").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W12345",
                "doi": "https://doi.org/10.1234/test",
                "title": "Original Paper",
                "is_retracted": False,
                "cited_by_count": 2,
                "cited_by_api_url": f"{OPENALEX_BASE}/works?filter=cites:W12345",
                "referenced_works": [],
            },
        )
    )
    respx.get(f"{OPENALEX_BASE}/works").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W99999",
                        "doi": "https://doi.org/10.5678/citing",
                        "title": "Citing Paper",
                        "publication_year": 2025,
                        "authorships": [
                            {"author": {"display_name": "Smith J"}},
                        ],
                    }
                ],
            },
        )
    )
    client = OpenAlexClient()
    results = client.get_citing_works("10.1234/test", limit=10)
    assert len(results) == 1
    assert results[0]["title"] == "Citing Paper"
    assert results[0]["doi"] == "10.5678/citing"
    assert results[0]["year"] == 2025


@respx.mock
def test_get_references_returns_list():
    """get_references returns papers cited by the target."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/test").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W12345",
                "doi": "https://doi.org/10.1234/test",
                "title": "Original Paper",
                "is_retracted": False,
                "cited_by_count": 0,
                "cited_by_api_url": "",
                "referenced_works": ["https://openalex.org/W88888"],
            },
        )
    )
    respx.get(f"{OPENALEX_BASE}/works/W88888").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W88888",
                "doi": "https://doi.org/10.9999/referenced",
                "title": "Referenced Paper",
                "publication_year": 2020,
                "authorships": [{"author": {"display_name": "Lee A"}}],
            },
        )
    )
    client = OpenAlexClient()
    results = client.get_references("10.1234/test")
    assert len(results) == 1
    assert results[0]["title"] == "Referenced Paper"
    assert results[0]["doi"] == "10.9999/referenced"


@respx.mock
def test_get_citing_works_returns_empty_for_unknown_doi():
    """get_citing_works returns empty list when DOI not found."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/unknown").mock(
        return_value=httpx.Response(404)
    )
    client = OpenAlexClient()
    results = client.get_citing_works("10.1234/unknown", limit=10)
    assert results == []
