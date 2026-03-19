"""Tests for LocalClient — read operations via Zotero local API."""

import httpx
import pytest
import respx

from zotero_mcp.local_client import LocalClient

LOCAL_BASE = "http://localhost:23119/api"


@respx.mock
def test_search_items_returns_summaries():
    """search_items returns formatted item summaries."""
    respx.get(f"{LOCAL_BASE}/users/0/items").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "ABC123",
                    "version": 5,
                    "data": {
                        "key": "ABC123",
                        "version": 5,
                        "itemType": "journalArticle",
                        "title": "Test Paper",
                        "creators": [
                            {
                                "creatorType": "author",
                                "firstName": "John",
                                "lastName": "Doe",
                            }
                        ],
                        "date": "2024",
                        "DOI": "10.1234/test",
                        "collections": ["COL1"],
                        "tags": [{"tag": "oncology"}],
                    },
                }
            ],
        )
    )
    client = LocalClient()
    results = client.search_items("test")
    assert len(results) == 1
    assert results[0]["key"] == "ABC123"
    assert results[0]["title"] == "Test Paper"
    assert results[0]["creators"] == "John Doe"
    assert results[0]["tags"] == ["oncology"]


@respx.mock
def test_get_item_json():
    """get_item returns full metadata as dict."""
    respx.get(f"{LOCAL_BASE}/users/0/items/ABC123").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ABC123",
                "version": 5,
                "data": {
                    "key": "ABC123",
                    "version": 5,
                    "itemType": "journalArticle",
                    "title": "Test Paper",
                    "creators": [],
                    "date": "2024",
                    "DOI": "10.1234/test",
                },
            },
        )
    )
    client = LocalClient()
    result = client.get_item("ABC123")
    assert result["title"] == "Test Paper"
    assert result["DOI"] == "10.1234/test"


@respx.mock
def test_get_item_bibtex():
    """get_item with format=bibtex returns raw string."""
    respx.get(f"{LOCAL_BASE}/users/0/items/ABC123").mock(
        return_value=httpx.Response(
            200,
            text="@article{doe2024, title={Test Paper}}",
            headers={"content-type": "text/plain"},
        )
    )
    client = LocalClient()
    result = client.get_item("ABC123", fmt="bibtex")
    assert "@article" in result


@respx.mock
def test_get_collections():
    """get_collections returns flat list with parent info."""
    respx.get(f"{LOCAL_BASE}/users/0/collections").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "COL1",
                    "data": {
                        "key": "COL1",
                        "name": "Oncology",
                        "parentCollection": False,
                    },
                    "meta": {"numItems": 10},
                }
            ],
        )
    )
    client = LocalClient()
    results = client.get_collections()
    assert len(results) == 1
    assert results[0]["name"] == "Oncology"
    assert results[0]["num_items"] == 10


@respx.mock
def test_get_collection_items():
    """get_collection_items returns summaries for collection."""
    respx.get(f"{LOCAL_BASE}/users/0/collections/COL1/items").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "XYZ789",
                    "data": {
                        "key": "XYZ789",
                        "itemType": "journalArticle",
                        "title": "Collection Paper",
                        "creators": [],
                        "date": "2025",
                        "DOI": "",
                        "collections": ["COL1"],
                        "tags": [],
                    },
                }
            ],
        )
    )
    client = LocalClient()
    results = client.get_collection_items("COL1")
    assert len(results) == 1
    assert results[0]["title"] == "Collection Paper"


@respx.mock
def test_connection_error_gives_clear_message():
    """Connection refused gives actionable error message."""
    respx.get(f"{LOCAL_BASE}/users/0/items").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    client = LocalClient()
    with pytest.raises(RuntimeError, match="Zotero desktop must be running"):
        client.search_items("test")
