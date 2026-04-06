"""Tests for duplicate detection — title similarity and audit tool."""

import unittest.mock as mock

import httpx
import respx

from zotero_mcp.web_client import WEB_BASE, WebClient

USER_ID = "12345"
API_KEY = "testapikey"
BASE = f"{WEB_BASE}/users/{USER_ID}"


def _make_client() -> WebClient:
    return WebClient(api_key=API_KEY, user_id=USER_ID)


def test_check_duplicate_title_finds_match():
    """Title similarity catches case/punctuation variants."""
    client = _make_client()
    existing = [
        {
            "key": "ABC123",
            "title": "Gastric Intestinal Metaplasia Detection: A Systematic Review",
            "DOI": "",
            "creators": "",
            "date": "2024",
            "item_type": "journalArticle",
            "collections": [],
            "tags": [],
            "version": 1,
        }
    ]
    with mock.patch.object(client, "search_items", return_value=existing):
        result = client._check_duplicate_title(
            "Gastric intestinal metaplasia detection: a systematic review"
        )
    assert result is not None
    assert result["key"] == "ABC123"


def test_check_duplicate_title_rejects_dissimilar():
    """Title similarity rejects clearly different papers."""
    client = _make_client()
    existing = [
        {
            "key": "ABC123",
            "title": "Machine Learning for Drug Discovery",
            "DOI": "",
            "creators": "",
            "date": "2024",
            "item_type": "journalArticle",
            "collections": [],
            "tags": [],
            "version": 1,
        }
    ]
    with mock.patch.object(client, "search_items", return_value=existing):
        result = client._check_duplicate_title(
            "Gastric intestinal metaplasia detection: a systematic review"
        )
    assert result is None


@respx.mock
def test_create_item_from_url_detects_duplicate_doi():
    """create_item_from_url checks DOI after URL resolution."""
    # Mock translation server to return item with DOI
    respx.post("https://translate.zotero.org/web").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "itemType": "journalArticle",
                    "title": "Test Paper",
                    "DOI": "10.1234/existing",
                }
            ],
        )
    )

    client = _make_client()
    existing = {"key": "EXIST1", "title": "Test Paper", "DOI": "10.1234/existing"}
    with mock.patch.object(client, "_check_duplicate_doi", return_value=existing):
        result = client.create_item_from_url("https://example.com/paper")

    assert result.get("duplicate") is True
    assert result["key"] == "EXIST1"


@respx.mock
def test_create_item_manual_detects_duplicate_title():
    """create_item_manual checks title similarity when no DOI provided."""
    client = _make_client()
    existing = {
        "key": "EXIST2",
        "title": "Gastric Intestinal Metaplasia Detection",
        "similarity": 0.95,
        "match_type": "title_similarity",
    }
    with (
        mock.patch.object(client, "_check_duplicate_doi", return_value=None),
        mock.patch.object(client, "_check_duplicate_title", return_value=existing),
    ):
        result = client.create_item_manual(
            item_type="journalArticle",
            title="Gastric intestinal metaplasia detection",
        )

    assert result.get("duplicate") is True
    assert result["key"] == "EXIST2"
    assert result["match_type"] == "title_similarity"


@respx.mock
def test_create_item_manual_checks_doi_first():
    """create_item_manual checks DOI before title similarity."""
    client = _make_client()
    existing = {"key": "EXIST3", "title": "Test", "DOI": "10.1234/test"}
    with mock.patch.object(client, "_check_duplicate_doi", return_value=existing):
        result = client.create_item_manual(
            item_type="journalArticle",
            title="Different Title Entirely",
            doi="10.1234/test",
        )

    assert result.get("duplicate") is True
    assert result["key"] == "EXIST3"


def test_find_duplicates_groups_by_doi():
    """find_duplicates groups items with identical DOIs."""
    client = _make_client()
    items = [
        {
            "key": "A1",
            "title": "Paper One",
            "DOI": "10.1234/same",
            "date": "2024",
            "item_type": "journalArticle",
            "creators": "",
            "collections": [],
            "tags": [],
            "version": 1,
        },
        {
            "key": "A2",
            "title": "Paper One (copy)",
            "DOI": "10.1234/same",
            "date": "2024",
            "item_type": "journalArticle",
            "creators": "",
            "collections": [],
            "tags": [],
            "version": 2,
        },
        {
            "key": "B1",
            "title": "Unique Paper",
            "DOI": "10.5678/unique",
            "date": "2024",
            "item_type": "journalArticle",
            "creators": "",
            "collections": [],
            "tags": [],
            "version": 3,
        },
    ]
    import unittest.mock as mock

    with mock.patch.object(client, "search_items", return_value=items):
        result = client.find_duplicates(limit=100)

    assert result["total_groups"] >= 1
    doi_groups = [g for g in result["duplicate_groups"] if g["match_type"] == "doi"]
    assert len(doi_groups) == 1
    assert doi_groups[0]["doi"] == "10.1234/same"
    assert len(doi_groups[0]["items"]) == 2


def test_find_duplicates_groups_by_title_similarity():
    """find_duplicates groups items with similar titles (no DOI)."""
    client = _make_client()
    items = [
        {
            "key": "C1",
            "title": "Gastric Intestinal Metaplasia Detection",
            "DOI": "",
            "date": "2024",
            "item_type": "journalArticle",
            "creators": "",
            "collections": [],
            "tags": [],
            "version": 1,
        },
        {
            "key": "C2",
            "title": "Gastric intestinal metaplasia detection: a review",
            "DOI": "",
            "date": "2024",
            "item_type": "journalArticle",
            "creators": "",
            "collections": [],
            "tags": [],
            "version": 2,
        },
        {
            "key": "D1",
            "title": "Completely Different Topic",
            "DOI": "",
            "date": "2024",
            "item_type": "journalArticle",
            "creators": "",
            "collections": [],
            "tags": [],
            "version": 3,
        },
    ]
    import unittest.mock as mock

    with mock.patch.object(client, "search_items", return_value=items):
        result = client.find_duplicates(limit=100)

    title_groups = [
        g for g in result["duplicate_groups"] if g["match_type"] == "title_similarity"
    ]
    assert len(title_groups) == 1
    assert len(title_groups[0]["items"]) == 2
