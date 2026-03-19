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
