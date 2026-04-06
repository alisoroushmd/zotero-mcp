"""Tests for WebClient PDF attachment download."""

import httpx
import pytest
import respx

from zotero_mcp.web_client import WEB_BASE, WebClient

USER_ID = "12345"
API_KEY = "testapikey"
BASE = f"{WEB_BASE}/users/{USER_ID}"


def _make_client() -> WebClient:
    return WebClient(api_key=API_KEY, user_id=USER_ID)


@respx.mock
def test_download_attachment_returns_bytes():
    """download_attachment returns PDF bytes from Web API."""
    pdf_bytes = b"%PDF-1.4 fake pdf content here"
    respx.get(f"{BASE}/items/ATT001/file").mock(
        return_value=httpx.Response(
            200,
            content=pdf_bytes,
            headers={"Content-Type": "application/pdf"},
        )
    )
    client = _make_client()
    result = client.download_attachment("ATT001")
    assert result == pdf_bytes


@respx.mock
def test_download_attachment_raises_on_404():
    """download_attachment raises RuntimeError when attachment not found."""
    respx.get(f"{BASE}/items/ATT001/file").mock(return_value=httpx.Response(404))
    client = _make_client()
    with pytest.raises(httpx.HTTPStatusError):
        client.download_attachment("ATT001")
