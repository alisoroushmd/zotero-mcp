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

    def _format_work_summary(self, work: dict) -> dict:
        """Extract key fields from an OpenAlex work for display.

        Args:
            work: Raw OpenAlex work dict.

        Returns:
            Compact summary with title, doi, year, authors.
        """
        doi = (work.get("doi") or "").replace("https://doi.org/", "")
        authorships = work.get("authorships", [])
        authors = "; ".join(
            a.get("author", {}).get("display_name", "") for a in authorships[:3]
        )
        if len(authorships) > 3:
            authors += " et al."
        return {
            "openalex_id": work.get("id", ""),
            "title": work.get("title", ""),
            "doi": doi,
            "year": work.get("publication_year"),
            "authors": authors,
        }

    def get_citing_works(self, doi: str, limit: int = 20) -> list[dict]:
        """Get works that cite the given DOI.

        Args:
            doi: DOI of the target paper.
            limit: Max number of citing works to return.

        Returns:
            List of work summary dicts, sorted by recency.
        """
        work = self.get_work(doi)
        if not work:
            return []

        openalex_id = work.get("id", "").split("/")[-1]
        if not openalex_id:
            return []

        try:
            resp = self._client.get(
                "/works",
                params={
                    "filter": f"cites:{openalex_id}",
                    "sort": "publication_year:desc",
                    "per_page": min(limit, 50),
                },
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return [self._format_work_summary(w) for w in results]
        except Exception:
            logger.warning("Failed to fetch citing works for DOI %s", doi)
            return []

    def get_references(self, doi: str) -> list[dict]:
        """Get works referenced by the given DOI.

        Fetches referenced works in parallel (up to 5 concurrent).
        Limited to first 20 references to avoid excessive API calls.

        Args:
            doi: DOI of the target paper.

        Returns:
            List of work summary dicts.
        """
        from concurrent.futures import ThreadPoolExecutor

        work = self.get_work(doi)
        if not work:
            return []

        ref_ids = work.get("referenced_works", [])[:20]
        if not ref_ids:
            return []

        def _fetch_one(ref_url: str) -> dict | None:
            ref_id = ref_url.split("/")[-1]
            try:
                resp = self._client.get(f"/works/{ref_id}")
                if resp.status_code == 200:
                    return self._format_work_summary(resp.json())
            except Exception:
                pass
            return None

        with ThreadPoolExecutor(max_workers=5) as pool:
            fetched = list(pool.map(_fetch_one, ref_ids))

        return [r for r in fetched if r is not None]
