"""Tests that silent exception blocks emit log warnings."""

import logging
import httpx
import pytest
import respx
from unittest.mock import MagicMock, patch
from zotero_mcp.web_client import WebClient
from zotero_mcp.openalex_client import OpenAlexClient

WEB_BASE = "https://api.zotero.org"
PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CROSSREF_BASE = "https://api.crossref.org"


def make_client():
    return WebClient(api_key="testkey", user_id="123456")


@respx.mock
def test_resolve_pmid_to_pmcid_logs_on_failure(caplog):
    """PMCID lookup failure should log a warning, not swallow silently."""
    respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(
        side_effect=httpx.ConnectError("network down")
    )
    client = make_client()
    with caplog.at_level(logging.WARNING, logger="zotero_mcp.web_client"):
        result = client.resolve_pmid_to_pmcid("12345678")
    assert result is None
    assert any(
        "pmcid" in r.message.lower() or "pmid" in r.message.lower()
        for r in caplog.records
    )


@respx.mock
def test_check_crossref_updates_logs_on_network_failure(caplog):
    """CrossRef failure should log a warning."""
    respx.get(f"{CROSSREF_BASE}/works/10.1234/test").mock(
        side_effect=httpx.ConnectError("network down")
    )
    client = make_client()
    with caplog.at_level(logging.WARNING, logger="zotero_mcp.web_client"):
        result = client.check_crossref_updates("10.1234/test")
    assert result["has_retraction"] is False
    assert any("crossref" in r.message.lower() for r in caplog.records)


@respx.mock
def test_check_duplicate_doi_logs_on_failure(caplog):
    """Duplicate DOI check failure should log a warning."""
    respx.get(f"{WEB_BASE}/users/123456/items/top").mock(
        side_effect=httpx.ConnectError("network down")
    )
    client = make_client()
    with caplog.at_level(logging.WARNING, logger="zotero_mcp.web_client"):
        result = client._check_duplicate_doi("10.1234/test")
    assert result is None
    assert any("duplicate" in r.message.lower() for r in caplog.records)


@respx.mock
def test_download_free_pdf_logs_on_unpaywall_failure(caplog):
    """Unpaywall PDF source failure should log a warning."""
    respx.get("https://api.unpaywall.org/v2/10.1234/test").mock(
        side_effect=httpx.ConnectError("down")
    )
    respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )
    client = make_client()
    with caplog.at_level(logging.WARNING, logger="zotero_mcp.web_client"):
        pdf, name, src = client._download_free_pdf("10.1234/test")
    assert pdf is None
    assert any("unpaywall" in r.message.lower() for r in caplog.records)


def test_openalex_get_references_logs_on_fetch_failure(caplog):
    """OpenAlex reference fetch failure should log."""
    client = OpenAlexClient()
    # get_work returning None means get_references returns [] immediately
    # so patch get_work to return a work with referenced_works
    fake_work = {
        "id": "https://openalex.org/W123",
        "referenced_works": ["https://openalex.org/W456"],
    }
    with patch.object(client, "get_work", return_value=fake_work):
        with patch.object(
            client._client,
            "get",
            side_effect=httpx.ConnectError("down"),
        ):
            with caplog.at_level(logging.WARNING, logger="zotero_mcp.openalex_client"):
                result = client.get_references("10.1234/test")
    assert result == []
    assert any(
        "openalex" in r.message.lower()
        or "reference" in r.message.lower()
        or "fetch" in r.message.lower()
        for r in caplog.records
    )
