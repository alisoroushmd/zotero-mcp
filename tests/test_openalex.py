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


@respx.mock
def test_bulk_get_works_batches_dois():
    """bulk_get_works fetches metadata for multiple DOIs in batches."""
    respx.get(
        f"{OPENALEX_BASE}/works",
        params__contains={"filter": "doi:10.1/a|10.1/b"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "doi": "https://doi.org/10.1/a",
                        "title": "Paper A",
                        "publication_year": 2020,
                        "authorships": [],
                        "referenced_works": ["https://openalex.org/W99"],
                    },
                    {
                        "id": "https://openalex.org/W2",
                        "doi": "https://doi.org/10.1/b",
                        "title": "Paper B",
                        "publication_year": 2022,
                        "authorships": [],
                        "referenced_works": [],
                    },
                ]
            },
        )
    )
    client = OpenAlexClient()
    results = client.bulk_get_works(["10.1/a", "10.1/b"])
    assert len(results) == 2
    assert results[0]["doi"] == "https://doi.org/10.1/a"


@respx.mock
def test_resolve_openalex_ids_to_dois():
    """resolve_ids_to_dois converts OpenAlex IDs to DOIs."""
    respx.get(
        f"{OPENALEX_BASE}/works",
        params__contains={"filter": "openalex:W99|W100"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W99",
                        "doi": "https://doi.org/10.1/ref1",
                    },
                    {"id": "https://openalex.org/W100", "doi": None},
                ]
            },
        )
    )
    client = OpenAlexClient()
    mapping = client.resolve_ids_to_dois(["W99", "W100"])
    assert mapping == {"W99": "10.1/ref1"}
    # W100 has no DOI, so it's excluded


@respx.mock
def test_bulk_get_works_single_doi():
    """bulk_get_works works with a single DOI (no trailing pipe)."""
    respx.get(
        f"{OPENALEX_BASE}/works",
        params__contains={"filter": "doi:10.1/only"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "W1",
                        "doi": "https://doi.org/10.1/only",
                        "title": "Only",
                        "publication_year": 2023,
                        "authorships": [],
                        "referenced_works": [],
                    },
                ]
            },
        )
    )
    client = OpenAlexClient()
    results = client.bulk_get_works(["10.1/only"])
    assert len(results) == 1
