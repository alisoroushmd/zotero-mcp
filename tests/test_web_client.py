"""Tests for WebClient — write operations via Zotero Web API."""

import httpx
import pytest
import respx

from zotero_mcp.web_client import WebClient

WEB_BASE = "https://api.zotero.org"
TRANSLATE_URL = "https://translate.zotero.org/search"


@respx.mock
def test_create_item_from_identifier_doi():
    """create_item_from_identifier resolves DOI and creates item."""
    respx.post(TRANSLATE_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "itemType": "journalArticle",
                    "title": "Test Paper From DOI",
                    "creators": [
                        {
                            "creatorType": "author",
                            "firstName": "Jane",
                            "lastName": "Smith",
                        }
                    ],
                    "DOI": "10.1234/test",
                    "date": "2024",
                }
            ],
        )
    )
    respx.post(f"{WEB_BASE}/users/12345/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "successful": {"0": {"key": "NEW123", "data": {"key": "NEW123"}}},
                "success": {"0": "NEW123"},
                "unchanged": {},
                "failed": {},
            },
        )
    )

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_item_from_identifier("10.1234/test")
    assert result["key"] == "NEW123"
    assert result["title"] == "Test Paper From DOI"


@respx.mock
def test_create_item_from_identifier_with_collections_and_tags():
    """create_item_from_identifier applies collections and tags."""
    respx.post(TRANSLATE_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "itemType": "journalArticle",
                    "title": "Tagged Paper",
                    "creators": [],
                    "DOI": "10.5678/tagged",
                }
            ],
        )
    )
    respx.post(f"{WEB_BASE}/users/12345/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "successful": {"0": {"key": "TAG456", "data": {"key": "TAG456"}}},
                "success": {"0": "TAG456"},
                "unchanged": {},
                "failed": {},
            },
        )
    )

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_item_from_identifier(
        "10.5678/tagged",
        collection_keys=["COL1"],
        tags=["oncology", "review"],
    )
    assert result["key"] == "TAG456"

    request = respx.calls[-1].request
    import json

    body = json.loads(request.content)
    assert body[0]["collections"] == ["COL1"]
    assert {"tag": "oncology"} in body[0]["tags"]


@respx.mock
def test_create_item_translation_server_down_falls_back():
    """Falls back to PubMed when translation server is unavailable."""
    respx.post(TRANSLATE_URL).mock(side_effect=httpx.ConnectError("Connection refused"))
    # Mock PubMed DOI search
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(
            200, json={"esearchresult": {"idlist": ["12345678"]}}
        )
    )
    # Mock PubMed summary
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "12345678": {
                        "title": "Fallback Paper",
                        "authors": [{"name": "Smith J"}],
                        "pubdate": "2024",
                        "fulljournalname": "Test Journal",
                        "volume": "1",
                        "issue": "2",
                        "pages": "10-20",
                        "issn": "",
                        "articleids": [{"idtype": "doi", "value": "10.1234/test"}],
                    }
                }
            },
        )
    )
    # Mock Web API create
    respx.post(f"{WEB_BASE}/users/12345/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "successful": {"0": {"key": "FB123", "data": {"key": "FB123"}}},
                "success": {"0": "FB123"},
                "unchanged": {},
                "failed": {},
            },
        )
    )

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_item_from_identifier("10.1234/test")
    assert result["key"] == "FB123"
    assert result["title"] == "Fallback Paper"


@respx.mock
def test_create_item_unresolvable_identifier():
    """Raises error naming the identifier when translation returns empty."""
    respx.post(TRANSLATE_URL).mock(return_value=httpx.Response(200, json=[]))

    client = WebClient(api_key="test-key", user_id="12345")
    with pytest.raises(RuntimeError, match="No metadata found.*99999999"):
        client.create_item_from_identifier("99999999")


@respx.mock
def test_create_item_duplicate_doi_returns_existing():
    """Returns existing item key when DOI already in library."""
    LOCAL_BASE = "http://localhost:23119/api"
    respx.get(f"{LOCAL_BASE}/users/0/items").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "EXISTING1",
                    "data": {
                        "key": "EXISTING1",
                        "itemType": "journalArticle",
                        "title": "Already Here",
                        "DOI": "10.1234/existing",
                        "creators": [],
                        "date": "2024",
                        "collections": [],
                        "tags": [],
                    },
                }
            ],
        )
    )
    respx.post(TRANSLATE_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "itemType": "journalArticle",
                    "title": "Already Here",
                    "DOI": "10.1234/existing",
                    "creators": [],
                }
            ],
        )
    )

    from zotero_mcp.local_client import LocalClient

    local = LocalClient()
    client = WebClient(api_key="test-key", user_id="12345", local_client=local)
    result = client.create_item_from_identifier("10.1234/existing")
    assert result["key"] == "EXISTING1"
    assert result["duplicate"] is True


def test_missing_api_key_raises_error():
    """Missing API key gives clear error with link."""
    with pytest.raises(ValueError, match="ZOTERO_API_KEY.*zotero.org/settings/keys"):
        WebClient(api_key="", user_id="12345")


def test_missing_user_id_raises_error():
    """Missing user ID gives clear error with link."""
    with pytest.raises(ValueError, match="ZOTERO_API_KEY.*zotero.org/settings/keys"):
        WebClient(api_key="test-key", user_id="")


LOCAL_BASE = "http://localhost:23119/api"


@respx.mock
def test_add_to_collection():
    """add_to_collection reads item locally, patches via web API."""
    respx.get(f"{LOCAL_BASE}/users/0/items/ITEM1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ITEM1",
                "version": 10,
                "data": {
                    "key": "ITEM1",
                    "version": 10,
                    "collections": ["COL1"],
                },
            },
        )
    )
    respx.patch(f"{WEB_BASE}/users/12345/items/ITEM1").mock(
        return_value=httpx.Response(204)
    )

    from zotero_mcp.local_client import LocalClient

    local = LocalClient()
    client = WebClient(api_key="test-key", user_id="12345", local_client=local)
    result = client.add_to_collection("ITEM1", "COL2")
    assert "COL1" in result["collections"]
    assert "COL2" in result["collections"]


@respx.mock
def test_update_item():
    """update_item reads locally, patches via web API with version."""
    respx.get(f"{LOCAL_BASE}/users/0/items/ITEM1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ITEM1",
                "version": 10,
                "data": {
                    "key": "ITEM1",
                    "version": 10,
                    "title": "Old Title",
                },
            },
        )
    )
    respx.patch(f"{WEB_BASE}/users/12345/items/ITEM1").mock(
        return_value=httpx.Response(204)
    )

    from zotero_mcp.local_client import LocalClient

    local = LocalClient()
    client = WebClient(api_key="test-key", user_id="12345", local_client=local)
    result = client.update_item("ITEM1", {"title": "New Title"})
    assert result["key"] == "ITEM1"

    request = respx.calls[-1].request
    assert request.headers["If-Unmodified-Since-Version"] == "10"


@respx.mock
def test_update_item_version_conflict():
    """update_item raises clear error on 412 Precondition Failed."""
    respx.get(f"{LOCAL_BASE}/users/0/items/ITEM1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ITEM1",
                "version": 10,
                "data": {"key": "ITEM1", "version": 10, "title": "Old"},
            },
        )
    )
    respx.patch(f"{WEB_BASE}/users/12345/items/ITEM1").mock(
        return_value=httpx.Response(412)
    )

    from zotero_mcp.local_client import LocalClient

    local = LocalClient()
    client = WebClient(api_key="test-key", user_id="12345", local_client=local)
    with pytest.raises(RuntimeError, match="Version conflict.*ITEM1.*retry"):
        client.update_item("ITEM1", {"title": "New"})


@respx.mock
def test_web_api_rate_limit_surfaces_error():
    """Rate limit (429) error is surfaced to the user."""
    respx.post(TRANSLATE_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "itemType": "journalArticle",
                    "title": "Paper",
                    "DOI": "10.1/x",
                    "creators": [],
                }
            ],
        )
    )
    respx.post(f"{WEB_BASE}/users/12345/items").mock(return_value=httpx.Response(429))

    client = WebClient(api_key="test-key", user_id="12345")
    with pytest.raises(httpx.HTTPStatusError):
        client.create_item_from_identifier("10.1/x")
