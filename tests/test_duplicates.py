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
def test_create_item_manual_surfaces_dedup_check_failure():
    """When the dedup check fails transiently, the result flags it (ZOT-26)."""
    client = _make_client()
    respx.post(f"{BASE}/items").mock(
        return_value=httpx.Response(
            200, json={"successful": {"0": {"key": "NEWKEY", "data": {"key": "NEWKEY"}}}}
        )
    )
    # Simulate the duplicate search erroring (timeout/429): the helper sets the
    # flag and returns None (no duplicate found).
    with mock.patch.object(client, "search_items", side_effect=httpx.ReadTimeout("boom")):
        result = client.create_item_manual(
            item_type="journalArticle",
            title="A Brand New Paper Title",
        )

    assert result["key"] == "NEWKEY"
    assert result.get("dedup_check_failed") is True
    assert "duplicate" in result.get("dedup_warning", "").lower()


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

    title_groups = [g for g in result["duplicate_groups"] if g["match_type"] == "title_similarity"]
    assert len(title_groups) == 1
    assert len(title_groups[0]["items"]) == 2


def _rec(key, title, item_type="journalArticle", doi=""):
    return {
        "key": key,
        "title": title,
        "DOI": doi,
        "date": "2026",
        "item_type": item_type,
        "creators": "",
        "collections": [],
        "tags": [],
        "version": 1,
    }


def test_find_duplicates_excludes_child_attachments_and_notes():
    """Child attachments/notes must never form duplicate groups.

    Regression for the 2026-08-30 hygiene audit: generic attachment titles
    ("Snapshot", "arXiv.org Snapshot", "PubMed entry") are byte-identical, so
    they trivially clear the 0.85 similarity threshold and produced 3 groups /
    50 items that crowded out a real DOI duplicate.
    """
    client = _make_client()
    items = [
        _rec("A1", "Snapshot", "attachment"),
        _rec("A2", "Snapshot", "attachment"),
        _rec("A3", "arXiv.org Snapshot", "attachment"),
        _rec("A4", "arXiv.org Snapshot", "attachment"),
        _rec("N1", "PubMed entry", "note"),
        _rec("N2", "PubMed entry", "note"),
        _rec("J1", "A Unique Paper About Gastric Metaplasia", "journalArticle"),
    ]
    with mock.patch.object(client, "search_items", return_value=items):
        result = client.find_duplicates(limit=100)

    assert result["total_groups"] == 0, result["duplicate_groups"]
    assert result["total_duplicate_items"] == 0


def test_find_duplicates_still_reports_real_doi_dupes_amid_attachments():
    """A real DOI duplicate must survive alongside attachment noise."""
    client = _make_client()
    items = [_rec(f"A{i}", "Snapshot", "attachment") for i in range(20)]
    items += [
        _rec("R1", "Use of LLMs to Determine the Surveillance Interval", doi="10.1/abc"),
        _rec("R2", "Use of LLMs to Determine the Surveillance Interval", doi="10.1/abc"),
    ]
    with mock.patch.object(client, "search_items", return_value=items):
        result = client.find_duplicates(limit=100)

    doi_groups = [g for g in result["duplicate_groups"] if g["match_type"] == "doi"]
    assert len(doi_groups) == 1
    assert {i["key"] for i in doi_groups[0]["items"]} == {"R1", "R2"}


def test_read_item_never_uses_local_version():
    """_read_item must read via the Web API, not the local database.

    The returned version is sent as If-Unmodified-Since-Version on the next
    PATCH. The local DB keeps its own counter (0/1/2) unrelated to the cloud
    version, so a local read made every read-modify-write fail with HTTP 412
    whenever Zotero desktop was running.
    """
    client = _make_client()
    local = mock.Mock()
    local.get_item.return_value = {"key": "K1", "version": 2, "collections": []}
    client._local = local

    with mock.patch.object(
        client, "get_item", return_value={"key": "K1", "version": 8880, "collections": []}
    ) as web:
        item = client._read_item("K1")

    assert item["version"] == 8880, "must use the cloud version, not the local one"
    local.get_item.assert_not_called()
    web.assert_called_once_with("K1")
