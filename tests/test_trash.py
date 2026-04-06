"""Tests for trash management — trash_items and empty_trash."""

import httpx
import respx

from zotero_mcp.web_client import WEB_BASE, WebClient

USER_ID = "12345"
API_KEY = "testapikey"
BASE = f"{WEB_BASE}/users/{USER_ID}"


def _make_client() -> WebClient:
    return WebClient(api_key=API_KEY, user_id=USER_ID)


@respx.mock
def test_trash_items_single():
    """trash_items moves a single item to trash."""
    respx.get(f"{BASE}/items").mock(
        return_value=httpx.Response(
            200, json=[], headers={"Last-Modified-Version": "10"}
        )
    )
    respx.delete(f"{BASE}/items").mock(
        return_value=httpx.Response(204, headers={"Last-Modified-Version": "11"})
    )
    client = _make_client()
    result = client.trash_items(["ABC123"])
    assert "ABC123" in result["trashed"]
    assert result["failed"] == []


@respx.mock
def test_trash_items_batch_chunking():
    """trash_items chunks >50 keys into multiple requests."""
    keys = [f"KEY{i:04d}" for i in range(55)]
    respx.get(f"{BASE}/items").mock(
        return_value=httpx.Response(
            200, json=[], headers={"Last-Modified-Version": "100"}
        )
    )
    delete_route = respx.delete(f"{BASE}/items").mock(
        return_value=httpx.Response(204, headers={"Last-Modified-Version": "101"})
    )
    client = _make_client()
    result = client.trash_items(keys)
    assert len(result["trashed"]) == 55
    assert delete_route.call_count == 2


@respx.mock
def test_empty_trash():
    """empty_trash permanently deletes all trashed items."""
    respx.get(f"{BASE}/items").mock(
        return_value=httpx.Response(
            200, json=[], headers={"Last-Modified-Version": "50"}
        )
    )
    respx.delete(f"{BASE}/items/trash").mock(return_value=httpx.Response(204))
    client = _make_client()
    result = client.empty_trash()
    assert result["status"] == "emptied"
