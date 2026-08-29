"""Tests for SemanticScholarClient — paper recommendations via S2 API."""

from unittest.mock import patch

import httpx
import pytest
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
def test_get_recommendations_raises_on_error():
    """get_recommendations raises on API error instead of swallowing it (ZOT-38).

    A 500 used to be logged and returned as [], indistinguishable from "no
    recommendations"; it must now propagate so _handle_tool_errors can report it.
    """
    respx.post(url__regex=r".*/recommendations/v1/papers/.*").mock(return_value=httpx.Response(500))
    client = SemanticScholarClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.get_recommendations(["10.1/seed"])


@respx.mock
def test_get_recommendations_retries_once_on_429():
    """A 429 is retried once after sleeping, then the retry result is used."""
    route = respx.post(url__regex=r".*/recommendations/v1/papers/.*").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json={"recommendedPapers": []}),
        ]
    )
    client = SemanticScholarClient()
    with patch("time.sleep") as mock_sleep:
        results = client.get_recommendations(["10.1/seed"])
    assert results == []
    assert route.call_count == 2
    mock_sleep.assert_called_once_with(1.0)


@respx.mock
def test_retry_after_http_date_falls_back():
    """An HTTP-date Retry-After doesn't crash; the 5s fallback is used (ZOT-24/38)."""
    respx.post(url__regex=r".*/recommendations/v1/papers/.*").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            httpx.Response(200, json={"recommendedPapers": []}),
        ]
    )
    client = SemanticScholarClient()
    with patch("time.sleep") as mock_sleep:
        results = client.get_recommendations(["10.1/seed"])
    assert results == []
    mock_sleep.assert_called_once_with(5.0)


def test_close_is_idempotent():
    """close() can be called repeatedly (atexit cleanup, ZOT-38)."""
    client = SemanticScholarClient()
    client.close()
    client.close()


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
