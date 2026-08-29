"""Tests for the _retry_request backoff helper and its application to write ops."""

from unittest.mock import patch

import httpx
import pytest
import respx

from zotero_mcp.web_client import WebClient, _retry_request

WEB_BASE = "https://api.zotero.org"
USER_ID = "123456"
BASE = f"{WEB_BASE}/users/{USER_ID}"


def make_client():
    return WebClient(api_key="testkey", user_id=USER_ID)


def test_retry_request_returns_on_success():
    """_retry_request returns immediately on a 200 response."""
    call_count = 0

    def succeed():
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"ok": True})

    resp = _retry_request(succeed)
    assert resp.status_code == 200
    assert call_count == 1


def test_retry_request_retries_on_429():
    """_retry_request retries on 429 and returns the successful response."""
    call_count = 0

    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    with patch("time.sleep"):
        resp = _retry_request(flaky)
    assert resp.status_code == 200
    assert call_count == 2


def test_retry_request_raises_after_max_attempts():
    """_retry_request raises HTTPStatusError after max_attempts of 429."""

    def always_429():
        return httpx.Response(429, headers={"Retry-After": "0"})

    with patch("time.sleep"), pytest.raises(httpx.HTTPStatusError):
        _retry_request(always_429, max_attempts=3)


def test_retry_request_caps_sleep_at_30s():
    """Retry sleep is capped at 30 seconds regardless of Retry-After header."""
    sleep_calls = []

    def always_429():
        return httpx.Response(429, headers={"Retry-After": "999"})

    with patch("time.sleep", side_effect=lambda d: sleep_calls.append(d)):
        with pytest.raises(httpx.HTTPStatusError):
            _retry_request(always_429, max_attempts=2)

    assert all(d <= 30.0 for d in sleep_calls)


@respx.mock
def test_update_item_retries_on_429():
    """update_item retries on 429 before succeeding."""
    item_key = "ABCD1234"
    call_count = 0

    respx.get(f"{BASE}/items/{item_key}").mock(
        return_value=httpx.Response(
            200, json={"data": {"key": item_key, "version": 5, "title": "Test"}}
        )
    )

    def patch_side_effect(request, route):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(204, headers={"Last-Modified-Version": "6"})

    respx.patch(f"{BASE}/items/{item_key}").mock(side_effect=patch_side_effect)

    client = make_client()
    with patch("time.sleep"):
        result = client.update_item(item_key, {"title": "New Title"})
    assert result["version"] == 6
    assert call_count == 2


@respx.mock
def test_get_item_retries_on_429():
    """Read GETs go through _retry_request too (ZOT-40)."""
    item_key = "ABCD1234"

    route = respx.get(f"{BASE}/items/{item_key}").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"data": {"key": item_key, "title": "Test"}}),
        ]
    )

    client = make_client()
    with patch("time.sleep"):
        result = client.get_item(item_key)
    assert result["title"] == "Test"
    assert route.call_count == 2


@respx.mock
def test_backoff_header_delays_next_read():
    """A Zotero `Backoff` header makes the NEXT read wait out the window (ZOT-40)."""
    respx.get(f"{BASE}/collections").mock(
        return_value=httpx.Response(200, json=[], headers={"Backoff": "5"})
    )

    client = make_client()
    client.get_collections()  # 200 + Backoff: 5 — records the window
    assert client._backoff_until > 0.0

    with patch("time.sleep") as mock_sleep:
        client.get_collections()  # must sleep out the remaining window first
    assert mock_sleep.call_count >= 1
    slept = mock_sleep.call_args_list[0][0][0]
    assert 0.0 < slept <= 5.0


@respx.mock
def test_backoff_junk_header_ignored():
    """A non-numeric Backoff value is ignored, not crashed on (ZOT-24/40)."""
    respx.get(f"{BASE}/collections").mock(
        return_value=httpx.Response(200, json=[], headers={"Backoff": "soon"})
    )

    client = make_client()
    client.get_collections()
    assert client._backoff_until == 0.0

    with patch("time.sleep") as mock_sleep:
        client.get_collections()
    mock_sleep.assert_not_called()
