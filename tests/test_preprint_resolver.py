"""Tests for Feature 6: Preprint-to-Publication Resolver."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from unittest.mock import MagicMock, patch

from zotero_mcp.openalex_client import OpenAlexClient
from zotero_mcp.web_client import WebClient

OPENALEX_BASE = "https://api.openalex.org"
CROSSREF_BASE = "https://api.crossref.org"
WEB_BASE = "https://api.zotero.org"


def make_web_client():
    return WebClient(api_key="testkey", user_id="123456")


# ---------------------------------------------------------------------------
# OpenAlexClient.check_published_version
# ---------------------------------------------------------------------------


@respx.mock
def test_check_published_version_detects_preprint_with_journal_location():
    """A preprint that has a journal location returns has_published_version=True."""
    doi = "10.1101/2024.01.01.123456"
    respx.get(f"{OPENALEX_BASE}/works/doi:{doi}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W1234",
                "doi": f"https://doi.org/{doi}",
                "title": "My bioRxiv preprint",
                "type": "preprint",
                "primary_location": {
                    "source": {"type": "repository", "display_name": "bioRxiv"},
                    "landing_page_url": f"https://www.biorxiv.org/content/{doi}",
                },
                "locations": [
                    {
                        "source": {"type": "repository", "display_name": "bioRxiv"},
                        "landing_page_url": f"https://www.biorxiv.org/content/{doi}",
                    },
                    {
                        "source": {
                            "type": "journal",
                            "display_name": "Nature Medicine",
                        },
                        "landing_page_url": "https://doi.org/10.1038/s41591-024-01234-5",
                    },
                ],
            },
        )
    )
    client = OpenAlexClient()
    result = client.check_published_version(doi)
    assert result["is_preprint"] is True
    assert result["published_doi"] == "10.1038/s41591-024-01234-5"
    assert result["journal"] == "Nature Medicine"


@respx.mock
def test_check_published_version_returns_false_for_journal_article():
    """A journal article (non-preprint) returns is_preprint=False."""
    doi = "10.1038/s41591-024-99999-0"
    respx.get(f"{OPENALEX_BASE}/works/doi:{doi}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W9999",
                "doi": f"https://doi.org/{doi}",
                "title": "Published journal article",
                "type": "article",
                "primary_location": {
                    "source": {"type": "journal", "display_name": "Nature Medicine"},
                    "landing_page_url": f"https://doi.org/{doi}",
                },
                "locations": [
                    {
                        "source": {
                            "type": "journal",
                            "display_name": "Nature Medicine",
                        },
                        "landing_page_url": f"https://doi.org/{doi}",
                    }
                ],
            },
        )
    )
    client = OpenAlexClient()
    result = client.check_published_version(doi)
    assert result["is_preprint"] is False
    assert result["published_doi"] is None


@respx.mock
def test_check_published_version_preprint_without_journal_location():
    """A preprint with no journal location returns is_preprint=True, published_doi=None."""
    doi = "10.1101/2024.01.01.999999"
    respx.get(f"{OPENALEX_BASE}/works/doi:{doi}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W5555",
                "doi": f"https://doi.org/{doi}",
                "title": "Unpublished preprint",
                "type": "preprint",
                "primary_location": {
                    "source": {"type": "repository", "display_name": "bioRxiv"},
                    "landing_page_url": f"https://www.biorxiv.org/content/{doi}",
                },
                "locations": [
                    {
                        "source": {"type": "repository", "display_name": "bioRxiv"},
                        "landing_page_url": f"https://www.biorxiv.org/content/{doi}",
                    }
                ],
            },
        )
    )
    client = OpenAlexClient()
    result = client.check_published_version(doi)
    assert result["is_preprint"] is True
    assert result["published_doi"] is None


@respx.mock
def test_check_published_version_not_found_in_openalex():
    """Work not in OpenAlex returns is_preprint=False, published_doi=None."""
    doi = "10.1101/2099.01.01.000001"
    respx.get(f"{OPENALEX_BASE}/works/doi:{doi}").mock(return_value=httpx.Response(404))
    client = OpenAlexClient()
    result = client.check_published_version(doi)
    assert result["is_preprint"] is False
    assert result["published_doi"] is None


# ---------------------------------------------------------------------------
# WebClient.check_crossref_published
# ---------------------------------------------------------------------------


@respx.mock
def test_check_crossref_published_finds_journal_doi():
    """CrossRef relation.is-preprint-of gives the published journal DOI."""
    doi = "10.1101/2024.01.01.123456"
    respx.get(f"{CROSSREF_BASE}/works/{doi}").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "DOI": doi,
                    "relation": {
                        "is-preprint-of": [
                            {
                                "id": "10.1038/s41591-024-01234-5",
                                "id-type": "doi",
                                "asserted-by": "subject",
                            }
                        ]
                    },
                }
            },
        )
    )
    client = make_web_client()
    result = client.check_crossref_published(doi)
    assert result["published_doi"] == "10.1038/s41591-024-01234-5"


@respx.mock
def test_check_crossref_published_returns_none_when_no_relation():
    """CrossRef with no is-preprint-of returns published_doi=None."""
    doi = "10.1101/2024.01.01.999999"
    respx.get(f"{CROSSREF_BASE}/works/{doi}").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "DOI": doi,
                    "relation": {},
                }
            },
        )
    )
    client = make_web_client()
    result = client.check_crossref_published(doi)
    assert result["published_doi"] is None


@respx.mock
def test_check_crossref_published_handles_network_error():
    """Network failure returns published_doi=None without raising."""
    doi = "10.1101/2024.01.01.111111"
    respx.get(f"{CROSSREF_BASE}/works/{doi}").mock(
        side_effect=httpx.ConnectError("down")
    )
    client = make_web_client()
    result = client.check_crossref_published(doi)
    assert result["published_doi"] is None


# ---------------------------------------------------------------------------
# check_published_versions tool (server-level)
# ---------------------------------------------------------------------------


def test_check_published_versions_tool_finds_published_preprint():
    """Tool returns has_published_version=True for a preprint with a known published DOI."""
    import zotero_mcp.server as srv

    mock_web = MagicMock()
    mock_web.get_item.return_value = {
        "key": "PREP0001",
        "title": "My preprint",
        "DOI": "10.1101/2024.01.01.123456",
        "extra": "",
    }
    mock_web.check_crossref_published.return_value = {
        "published_doi": "10.1038/s41591-024-01234-5"
    }
    mock_web._check_duplicate_doi.return_value = None  # not in library

    mock_oa = MagicMock()
    mock_oa.check_published_version.return_value = {
        "is_preprint": True,
        "published_doi": "10.1038/s41591-024-01234-5",
        "journal": "Nature Medicine",
    }

    with (
        patch.object(srv, "_web", mock_web),
        patch("zotero_mcp.openalex_client.OpenAlexClient", return_value=mock_oa),
    ):
        result = json.loads(srv.check_published_versions("PREP0001"))

    assert result["published_count"] == 1
    entry = result["results"][0]
    assert entry["has_published_version"] is True
    assert entry["published_doi"] == "10.1038/s41591-024-01234-5"
    assert entry["journal"] == "Nature Medicine"
    assert entry["in_library"] is False


def test_check_published_versions_tool_flags_in_library():
    """Tool sets in_library=True and zotero_key when published version is already saved."""
    import zotero_mcp.server as srv

    mock_web = MagicMock()
    mock_web.get_item.return_value = {
        "key": "PREP0002",
        "title": "My preprint",
        "DOI": "10.1101/2024.01.01.654321",
        "extra": "",
    }
    mock_web.check_crossref_published.return_value = {
        "published_doi": "10.1016/j.cell.2024.01.001"
    }
    # Published version IS in library
    mock_web._check_duplicate_doi.return_value = {
        "key": "PUB00001",
        "title": "Published: My preprint",
    }

    mock_oa = MagicMock()
    mock_oa.check_published_version.return_value = {
        "is_preprint": True,
        "published_doi": "10.1016/j.cell.2024.01.001",
        "journal": "Cell",
    }

    with (
        patch.object(srv, "_web", mock_web),
        patch("zotero_mcp.openalex_client.OpenAlexClient", return_value=mock_oa),
    ):
        result = json.loads(srv.check_published_versions("PREP0002"))

    entry = result["results"][0]
    assert entry["has_published_version"] is True
    assert entry["in_library"] is True
    assert entry["zotero_key"] == "PUB00001"


def test_check_published_versions_tool_handles_no_doi():
    """Tool gracefully handles items with no DOI."""
    import zotero_mcp.server as srv

    mock_web = MagicMock()
    mock_web.get_item.return_value = {
        "key": "NODOI001",
        "title": "Item without DOI",
        "DOI": "",
        "extra": "",
    }

    mock_oa = MagicMock()

    with (
        patch.object(srv, "_web", mock_web),
        patch("zotero_mcp.openalex_client.OpenAlexClient", return_value=mock_oa),
    ):
        result = json.loads(srv.check_published_versions("NODOI001"))

    entry = result["results"][0]
    assert entry["has_published_version"] is False
    assert "warning" in entry
    mock_web.check_crossref_published.assert_not_called()


def test_check_published_versions_crossref_fallback_when_openalex_empty():
    """When OpenAlex returns no published_doi, CrossRef result is used."""
    import zotero_mcp.server as srv

    mock_web = MagicMock()
    mock_web.get_item.return_value = {
        "key": "PREP0003",
        "title": "Preprint only in CrossRef",
        "DOI": "10.1101/2024.06.01.000001",
        "extra": "",
    }
    mock_web.check_crossref_published.return_value = {
        "published_doi": "10.1056/NEJMoa2024001"
    }
    mock_web._check_duplicate_doi.return_value = None

    mock_oa = MagicMock()
    mock_oa.check_published_version.return_value = {
        "is_preprint": True,
        "published_doi": None,  # OpenAlex doesn't know
        "journal": None,
    }

    with (
        patch.object(srv, "_web", mock_web),
        patch("zotero_mcp.openalex_client.OpenAlexClient", return_value=mock_oa),
    ):
        result = json.loads(srv.check_published_versions("PREP0003"))

    entry = result["results"][0]
    assert entry["has_published_version"] is True
    assert entry["published_doi"] == "10.1056/NEJMoa2024001"
