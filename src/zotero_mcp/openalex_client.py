"""OpenAlex API client — citation graph and retraction data."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

OPENALEX_BASE = "https://api.openalex.org"
TIMEOUT = 10.0


class OpenAlexClient:
    """Wrapper for the OpenAlex API.

    Used for retraction checks (Feature 3) and citation graph (Feature 5).
    OpenAlex is free and requires no API key. Polite pool access uses
    an email in the User-Agent header.
    """

    def __init__(self, email: str = "zotero-mcp@example.com") -> None:
        self._client = httpx.Client(
            base_url=OPENALEX_BASE,
            headers={"User-Agent": f"zotero-mcp/1.0 (mailto:{email})"},
            timeout=TIMEOUT,
        )

    def get_work(self, doi: str) -> dict | None:
        """Get work metadata by DOI.

        Args:
            doi: DOI string (e.g. "10.1234/test", with or without https://doi.org/ prefix).

        Returns:
            OpenAlex work dict, or None if not found.
        """
        doi = doi.strip()
        if doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/") :]
        if doi.startswith("http://doi.org/"):
            doi = doi[len("http://doi.org/") :]

        try:
            resp = self._client.get(f"/works/doi:{doi}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.warning("OpenAlex lookup failed for DOI %s", doi)
            return None
