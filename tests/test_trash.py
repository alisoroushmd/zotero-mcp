"""Tests for trash management — trash_items and empty_trash.

Regression context (2026-09-16): ``trash_items`` used ``DELETE /items?itemKey``,
which the Zotero Web API defines as *permanent* deletion, while the MCP tool
described itself as reversible. 909 attachment records were lost. The trash
operation must set ``deleted: true`` via a write and must never issue DELETE.
"""

import json

import httpx
import respx

from zotero_mcp.web_client import WEB_BASE, WebClient

USER_ID = "12345"
API_KEY = "testapikey"
BASE = f"{WEB_BASE}/users/{USER_ID}"


def _make_client() -> WebClient:
    return WebClient(api_key=API_KEY, user_id=USER_ID)


def _item(key: str, version: int) -> dict:
    return {"key": key, "version": version, "data": {"key": key, "version": version}}


def _mock_reads(items_by_key: dict[str, int], library_version: str = "10"):
    """Mock GET /items for both the library-version probe and the itemKey lookup."""

    def _get(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if "itemKey" in params:
            wanted = params["itemKey"].split(",")
            assert len(wanted) <= 50, "itemKey lookups must stay within the 50-key limit"
            body = [_item(k, items_by_key[k]) for k in wanted if k in items_by_key]
            return httpx.Response(
                200, json=body, headers={"Last-Modified-Version": library_version}
            )
        # version probe: GET /items?limit=0
        return httpx.Response(200, json=[], headers={"Last-Modified-Version": library_version})

    return respx.get(f"{BASE}/items").mock(side_effect=_get)


def _ok_write(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "successful": {str(i): {"key": o["key"]} for i, o in enumerate(payload)},
            "unchanged": {},
            "failed": {},
        },
        headers={"Last-Modified-Version": "11"},
    )


@respx.mock
def test_trash_items_never_issues_delete_and_writes_deleted_true():
    """trash_items must PATCH deleted:true (batched POST) and never HTTP DELETE."""
    _mock_reads({"ABC123": 7, "DEF456": 9})
    write_route = respx.post(f"{BASE}/items").mock(side_effect=_ok_write)
    delete_items = respx.delete(f"{BASE}/items").mock(return_value=httpx.Response(204))
    delete_single = respx.delete(url__regex=rf"{BASE}/items/.*").mock(
        return_value=httpx.Response(204)
    )

    result = _make_client().trash_items(["ABC123", "DEF456"])

    assert sorted(result["trashed"]) == ["ABC123", "DEF456"]
    assert result["failed"] == []
    assert not delete_items.called, "trash_items issued a permanent DELETE /items"
    assert not delete_single.called, "trash_items issued a permanent DELETE /items/<key>"
    assert not any(c.request.method == "DELETE" for c in respx.calls)

    assert write_route.call_count == 1
    req = write_route.calls[0].request
    assert req.url.path == f"/users/{USER_ID}/items"
    assert req.headers["If-Unmodified-Since-Version"] == "10"
    payload = json.loads(req.content)
    assert payload == [
        {"key": "ABC123", "version": 7, "deleted": True},
        {"key": "DEF456", "version": 9, "deleted": True},
    ]


@respx.mock
def test_trash_items_batch_chunking():
    """trash_items chunks >50 keys into multiple deleted:true writes of <=50 each."""
    keys = [f"KEY{i:04d}" for i in range(55)]
    _mock_reads({k: i for i, k in enumerate(keys)})
    write_route = respx.post(f"{BASE}/items").mock(side_effect=_ok_write)

    result = _make_client().trash_items(keys)

    assert len(result["trashed"]) == 55
    assert result["failed"] == []
    assert write_route.call_count == 2
    sizes = [len(json.loads(c.request.content)) for c in write_route.calls]
    assert sizes == [50, 5]
    for c in write_route.calls:
        assert all(o["deleted"] is True for o in json.loads(c.request.content))
    assert not any(c.request.method == "DELETE" for c in respx.calls)


@respx.mock
def test_trash_items_unknown_key_reported_as_failed():
    """Keys the API does not return are not written and are reported as failed."""
    _mock_reads({"KNOWN1": 3})
    write_route = respx.post(f"{BASE}/items").mock(side_effect=_ok_write)

    result = _make_client().trash_items(["KNOWN1", "MISSING"])

    assert result["trashed"] == ["KNOWN1"]
    assert result["failed"] == ["MISSING"]
    assert "not found" in result["errors"]["MISSING"]
    payload = json.loads(write_route.calls[0].request.content)
    assert [o["key"] for o in payload] == ["KNOWN1"]


@respx.mock
def test_trash_items_reports_per_item_api_failures_and_unchanged():
    """Multi-status body: successful+unchanged count as trashed, failed as failed."""
    _mock_reads({"A": 1, "B": 2, "C": 3})
    respx.post(f"{BASE}/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "successful": {"0": {"key": "A"}},
                "unchanged": {"1": "B"},
                "failed": {"2": {"code": 400, "message": "Item is not editable"}},
            },
            headers={"Last-Modified-Version": "12"},
        )
    )

    result = _make_client().trash_items(["A", "B", "C"])

    assert sorted(result["trashed"]) == ["A", "B"]
    assert result["failed"] == ["C"]
    assert "not editable" in result["errors"]["C"]


@respx.mock
def test_trash_items_retries_once_on_412_with_fresh_library_version():
    """A 412 refreshes the library version and retries the batch once."""
    versions = iter(["10", "20", "20"])

    def _get(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if "itemKey" in params:
            return httpx.Response(
                200, json=[_item("A", 5)], headers={"Last-Modified-Version": "10"}
            )
        return httpx.Response(200, json=[], headers={"Last-Modified-Version": next(versions)})

    respx.get(f"{BASE}/items").mock(side_effect=_get)
    write_route = respx.post(f"{BASE}/items").mock(
        side_effect=[
            httpx.Response(412),
            httpx.Response(
                200,
                json={"successful": {"0": {"key": "A"}}, "unchanged": {}, "failed": {}},
                headers={"Last-Modified-Version": "21"},
            ),
        ]
    )

    result = _make_client().trash_items(["A"])

    assert result["trashed"] == ["A"]
    assert write_route.call_count == 2
    assert write_route.calls[0].request.headers["If-Unmodified-Since-Version"] == "10"
    assert write_route.calls[1].request.headers["If-Unmodified-Since-Version"] == "20"


@respx.mock
def test_empty_trash():
    """empty_trash permanently deletes all trashed items (the only permanent path)."""
    respx.get(f"{BASE}/items").mock(
        return_value=httpx.Response(200, json=[], headers={"Last-Modified-Version": "50"})
    )
    respx.delete(f"{BASE}/items/trash").mock(return_value=httpx.Response(204))
    client = _make_client()
    result = client.empty_trash()
    assert result["status"] == "emptied"
