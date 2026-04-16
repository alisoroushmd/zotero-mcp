"""Tests for WebClient PDF attachment download."""

import os
import tempfile

import httpx
import pytest
import respx

from zotero_mcp.web_client import WEB_BASE, WebClient, _is_valid_pdf

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


@respx.mock
def test_attach_pdf_cleans_up_orphan_on_s3_failure():
    """If S3 upload fails after attachment item is created, the orphan is deleted."""
    parent_key = "ABCD1234"
    attach_key = "ATTACH01"
    pdf_bytes = b"%PDF-1.4 fake pdf content for testing purposes padding"

    # Step 1: Create attachment item — succeeds
    respx.post(f"{BASE}/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "successful": {"0": {"key": attach_key, "data": {"key": attach_key, "version": 1}}}
            },
        )
    )
    # Step 2: Get upload auth — succeeds
    respx.post(f"{BASE}/items/{attach_key}/file").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": "https://s3.amazonaws.com/upload",
                "prefix": "",
                "suffix": "",
                "contentType": "application/pdf",
                "uploadKey": "testkey123",
            },
        )
    )
    # Step 3: S3 upload — FAILS
    respx.post("https://s3.amazonaws.com/upload").mock(
        side_effect=httpx.ConnectError("S3 unreachable")
    )
    # Cleanup: GET version + DELETE orphan
    respx.get(f"{BASE}/items").mock(
        return_value=httpx.Response(200, json=[], headers={"Last-Modified-Version": "10"})
    )
    delete_route = respx.delete(f"{BASE}/items").mock(return_value=httpx.Response(204))

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp_path = f.name

    try:
        client = _make_client()
        with pytest.raises(Exception):
            client.attach_pdf(parent_key, pdf_path=tmp_path)
        assert delete_route.called, "Expected orphan attachment DELETE to be called"
        # Verify correct key was passed
        assert attach_key in delete_route.calls[0].request.url.params.get("itemKey", "")
    finally:
        os.unlink(tmp_path)


# -- Magic byte validation --


def test_is_valid_pdf_accepts_pdf_magic_bytes():
    assert _is_valid_pdf(b"%PDF-1.4 content here") is True
    assert _is_valid_pdf(b"%PDF-2.0\n") is True


def test_is_valid_pdf_rejects_html():
    assert _is_valid_pdf(b"<html>" + b"x" * 2000) is False


def test_is_valid_pdf_rejects_too_short():
    assert _is_valid_pdf(b"%PDF") is False  # only 4 bytes
    assert _is_valid_pdf(b"") is False


@respx.mock
def test_download_free_pdf_rejects_html_response():
    """Content that doesn't start with %PDF- is rejected even if large."""
    doi = "10.1234/test"
    not_a_pdf = b"<html>" + b"x" * 2000

    respx.get(f"https://api.unpaywall.org/v2/{doi}").mock(
        return_value=httpx.Response(
            200,
            json={"best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf"}},
        )
    )
    respx.get("https://example.com/paper.pdf").mock(
        return_value=httpx.Response(200, content=not_a_pdf)
    )
    # PMC: no results
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )

    client = _make_client()
    pdf, name, src = client._download_free_pdf(doi)
    assert pdf is None, "Should reject HTML page masquerading as PDF"


@respx.mock
def test_download_free_pdf_accepts_small_valid_pdf():
    """A valid PDF starting with %PDF- should be accepted regardless of size."""
    doi = "10.1234/test"
    valid_pdf = b"%PDF-1.4 minimal"  # Small but valid magic bytes

    respx.get(f"https://api.unpaywall.org/v2/{doi}").mock(
        return_value=httpx.Response(
            200,
            json={"best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf"}},
        )
    )
    respx.get("https://example.com/paper.pdf").mock(
        return_value=httpx.Response(200, content=valid_pdf)
    )

    client = _make_client()
    pdf, name, src = client._download_free_pdf(doi)
    assert pdf == valid_pdf
    assert src == "unpaywall"


@respx.mock
def test_download_free_pdf_biorxiv_uses_latest_version():
    """bioRxiv PDF download uses the latest version detected from the API."""
    doi = "10.1101/2024.01.01.123"
    valid_pdf = b"%PDF-1.4 biorxiv latest version"

    # Unpaywall: no OA location
    respx.get(f"https://api.unpaywall.org/v2/{doi}").mock(
        return_value=httpx.Response(200, json={"best_oa_location": None})
    )
    # PMC: no results
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )
    # bioRxiv API: latest version is 3
    respx.get(f"https://api.biorxiv.org/details/biorxiv/{doi}").mock(
        return_value=httpx.Response(
            200,
            json={"collection": [{"version": "1"}, {"version": "2"}, {"version": "3"}]},
        )
    )
    # PDF at v3
    respx.get(f"https://www.biorxiv.org/content/{doi}v3.full.pdf").mock(
        return_value=httpx.Response(200, content=valid_pdf)
    )

    client = _make_client()
    pdf, name, src = client._download_free_pdf(doi)
    assert pdf == valid_pdf
    assert src == "biorxiv"


@respx.mock
def test_download_free_pdf_biorxiv_falls_back_to_v1_on_api_error():
    """If the bioRxiv API is unavailable, fall back to version 1."""
    doi = "10.1101/2024.01.01.456"
    valid_pdf = b"%PDF-1.4 fallback v1"

    respx.get(f"https://api.unpaywall.org/v2/{doi}").mock(
        return_value=httpx.Response(200, json={"best_oa_location": None})
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )
    # bioRxiv API: 500 error
    respx.get(f"https://api.biorxiv.org/details/biorxiv/{doi}").mock(
        return_value=httpx.Response(500)
    )
    # PDF at v1 (fallback)
    respx.get(f"https://www.biorxiv.org/content/{doi}v1.full.pdf").mock(
        return_value=httpx.Response(200, content=valid_pdf)
    )

    client = _make_client()
    pdf, name, src = client._download_free_pdf(doi)
    assert pdf == valid_pdf
    assert src == "biorxiv"
