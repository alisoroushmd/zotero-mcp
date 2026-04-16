"""Tests for capability detection and server_status tool."""

import os
from unittest.mock import patch

import httpx
import respx

from zotero_mcp.capabilities import (
    ServerCapabilities,
    check_capabilities,
    format_status,
)


def test_all_available():
    """Both APIs available reports all modes active."""
    caps = ServerCapabilities(local_api=True, web_api=True, local_api_error="", web_api_error="")
    assert caps.local_read is True
    assert caps.cloud_crud is True
    assert caps.any_read is True


def test_local_only():
    """Only local API available — can read but not write."""
    caps = ServerCapabilities(
        local_api=True, web_api=False, local_api_error="", web_api_error="missing key"
    )
    assert caps.local_read is True
    assert caps.cloud_crud is False
    assert caps.any_read is True


def test_web_only():
    """Only web API available — all tools work, reads via web."""
    caps = ServerCapabilities(
        local_api=False, web_api=True, local_api_error="not running", web_api_error=""
    )
    assert caps.local_read is False
    assert caps.cloud_crud is True
    assert caps.any_read is True


def test_neither_available():
    """Neither API available — nothing works."""
    caps = ServerCapabilities(
        local_api=False, web_api=False, local_api_error="down", web_api_error="missing"
    )
    assert caps.local_read is False
    assert caps.cloud_crud is False
    assert caps.any_read is False


@respx.mock
def test_check_capabilities_local_up():
    """check_capabilities detects local API is reachable."""
    respx.get("http://localhost:23119/api/users/0/items").mock(
        return_value=httpx.Response(200, json=[])
    )
    with patch.dict(os.environ, {"ZOTERO_API_KEY": "key", "ZOTERO_USER_ID": "123"}):
        caps = check_capabilities()
    assert caps.local_api is True
    assert caps.web_api is True
    assert caps.local_api_error == ""
    assert caps.web_api_error == ""


@respx.mock
def test_check_capabilities_local_down():
    """check_capabilities detects local API is unreachable."""
    respx.get("http://localhost:23119/api/users/0/items").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    with patch.dict(os.environ, {}, clear=False):
        env = {k: v for k, v in os.environ.items() if k not in ("ZOTERO_API_KEY", "ZOTERO_USER_ID")}
        with patch.dict(os.environ, env, clear=True):
            caps = check_capabilities()
    assert caps.local_api is False
    assert "Zotero desktop not running" in caps.local_api_error


def test_check_capabilities_missing_env():
    """check_capabilities reports missing env vars."""
    with patch.dict(os.environ, {}, clear=True), respx.mock:
        respx.get("http://localhost:23119/api/users/0/items").mock(
            side_effect=httpx.ConnectError("refused")
        )
        caps = check_capabilities()
    assert caps.web_api is False
    assert "ZOTERO_API_KEY" in caps.web_api_error


def test_format_status_shows_fix_instructions():
    """format_status includes fix info for unavailable modes."""
    caps = ServerCapabilities(
        local_api=False,
        web_api=False,
        local_api_error="Start Zotero",
        web_api_error="Set API key",
    )
    status = format_status(caps)
    assert status["modes"]["local_read"]["available"] is False
    assert "Start Zotero" in status["modes"]["local_read"]["fix"]
    assert status["modes"]["cloud_crud"]["available"] is False
    assert "Set API key" in status["modes"]["cloud_crud"]["fix"]
    # server_status has no mode requirements, so it's always available
    unavailable = [t for t in status["unavailable_tools"] if t["name"] != "server_status"]
    assert len(unavailable) > 0


def test_format_status_web_only():
    """With just web API, all tools are available."""
    caps = ServerCapabilities(
        local_api=False, web_api=True, local_api_error="not running", web_api_error=""
    )
    status = format_status(caps)
    assert status["modes"]["cloud_crud"]["available"] is True
    assert status["modes"]["local_read"]["available"] is False
    # All tools should be available (web provides reads + writes)
    assert len(status["unavailable_tools"]) == 0


def test_format_status_all_available():
    """format_status with everything available."""
    caps = ServerCapabilities(local_api=True, web_api=True, local_api_error="", web_api_error="")
    status = format_status(caps)
    assert all(m["available"] for m in status["modes"].values())
    assert len(status["unavailable_tools"]) == 0
    assert len(status["available_tools"]) > 0
