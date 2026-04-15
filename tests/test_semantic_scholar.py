"""Tests for SemanticScholarClient — paper recommendations via S2 API."""

import httpx
import respx

from zotero_mcp.semantic_scholar_client import SemanticScholarClient

S2_BASE = "https://api.semanticscholar.org"


@respx.mock
def test_get_recommendations_returns_papers():
    """get_recommendations returns formatted paper list."""
    respx.post(url__regex=r".*/recommendations/v1/papers/.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "recommendedPapers": [
                    {
                        "paperId": "abc123",
                        "title": "Related Paper",
                        "year": 2023,
                        "authors": [{"name": "Smith J"}, {"name": "Lee A"}],
                        "externalIds": {"DOI": "10.1/related"},
                    },
                ]
            },
        )
    )
    client = SemanticScholarClient()
    results = client.get_recommendations(["10.1/seed"], limit=5)
    assert len(results) == 1
    assert results[0]["title"] == "Related Paper"
    assert results[0]["doi"] == "10.1/related"
    assert results[0]["year"] == 2023
    assert "Smith J" in results[0]["authors"]


@respx.mock
def test_get_recommendations_handles_empty():
    """get_recommendations returns empty list when no recommendations."""
    respx.post(url__regex=r".*/recommendations/v1/papers/.*").mock(
        return_value=httpx.Response(200, json={"recommendedPapers": []})
    )
    client = SemanticScholarClient()
    results = client.get_recommendations(["10.1/seed"])
    assert results == []


@respx.mock
def test_get_recommendations_handles_error():
    """get_recommendations returns empty list on API error."""
    respx.post(url__regex=r".*/recommendations/v1/papers/.*").mock(
        return_value=httpx.Response(500)
    )
    client = SemanticScholarClient()
    results = client.get_recommendations(["10.1/seed"])
    assert results == []


@respx.mock
def test_search_similar_delegates():
    """search_similar delegates to get_recommendations with single seed."""
    respx.post(url__regex=r".*/recommendations/v1/papers/.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "recommendedPapers": [
                    {
                        "paperId": "xyz",
                        "title": "Similar Paper",
                        "year": 2024,
                        "authors": [{"name": "Doe J"}],
                        "externalIds": {"DOI": "10.1/sim"},
                    },
                ]
            },
        )
    )
    client = SemanticScholarClient()
    results = client.search_similar("10.1/seed", limit=5)
    assert len(results) == 1
    assert results[0]["doi"] == "10.1/sim"


@respx.mock
def test_format_paper_truncates_authors():
    """Papers with >3 authors get 'et al.' suffix."""
    respx.post(url__regex=r".*/recommendations/v1/papers/.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "recommendedPapers": [
                    {
                        "paperId": "multi",
                        "title": "Multi-Author Paper",
                        "year": 2023,
                        "authors": [
                            {"name": "A"},
                            {"name": "B"},
                            {"name": "C"},
                            {"name": "D"},
                        ],
                        "externalIds": {"DOI": "10.1/multi"},
                    },
                ]
            },
        )
    )
    client = SemanticScholarClient()
    results = client.get_recommendations(["10.1/seed"])
    assert "et al." in results[0]["authors"]
