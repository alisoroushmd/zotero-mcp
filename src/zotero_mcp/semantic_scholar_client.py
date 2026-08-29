"""Semantic Scholar API client — paper recommendations and similarity.

Uses raw httpx (no third-party wrapper). Only two endpoints needed:
recommendations and single-paper lookup.
"""

from __future__ import annotations

import logging
import math
import time

import httpx

logger = logging.getLogger(__name__)

S2_BASE = "https://api.semanticscholar.org"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _parse_retry_after(value: str | None, fallback: float) -> float:
    """Parse a Retry-After header to seconds, tolerating HTTP-date values (ZOT-24).

    Same contract as the web_client/openalex_client copies: only a finite,
    non-negative numeric form is honored; a date or junk falls back. Matters
    more now that errors propagate (ZOT-38) — an ``int()`` crash here would be
    misclassified as invalid input by ``_handle_tool_errors``.
    """
    if not value:
        return fallback
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        return fallback
    if not math.isfinite(parsed) or parsed < 0:
        return fallback
    return parsed


class SemanticScholarClient:
    """Client for Semantic Scholar API.

    Provides paper recommendations similar to Connected Papers /
    ResearchRabbit using the recommendations endpoint.
    """

    def __init__(self, api_key: str | None = None) -> None:
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key
        self._client = httpx.Client(
            base_url=S2_BASE,
            headers=headers,
            timeout=TIMEOUT,
        )

    def close(self) -> None:
        """Close the pooled HTTP client (ZOT-38).

        Idempotent; called from server.py's ``_cleanup_clients`` atexit handler.
        """
        self._client.close()

    def get_recommendations(self, seed_dois: list[str], limit: int = 10) -> list[dict]:
        """Get paper recommendations based on seed papers.

        Uses Semantic Scholar's recommendations endpoint which finds
        papers related to the given seed set.

        Args:
            seed_dois: List of DOIs to use as positive seeds (max 50).
            limit: Max recommendations to return.

        Returns:
            List of recommended paper dicts with title, doi, year, authors.

        Raises:
            httpx.HTTPError: On API or transport failure (ZOT-38). Failures
                used to be swallowed into an empty list, indistinguishable
                from "no recommendations"; they now propagate so the server's
                ``_handle_tool_errors`` can surface a structured error.
        """
        paper_ids = [{"doi": doi} for doi in seed_dois[:50]]

        resp = self._client.post(
            "/recommendations/v1/papers/",
            json={"positivePaperIds": paper_ids, "negativePaperIds": []},
            params={
                "limit": min(limit, 50),
                "fields": "title,year,authors,externalIds",
            },
        )
        if resp.status_code == 429:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"), 5.0)
            time.sleep(min(retry_after, 10))
            resp = self._client.post(
                "/recommendations/v1/papers/",
                json={"positivePaperIds": paper_ids, "negativePaperIds": []},
                params={
                    "limit": min(limit, 50),
                    "fields": "title,year,authors,externalIds",
                },
            )
        resp.raise_for_status()
        papers = resp.json().get("recommendedPapers", [])
        return [self._format_paper(p) for p in papers]

    def search_similar(self, doi: str, limit: int = 10) -> list[dict]:
        """Find papers similar to a given DOI."""
        return self.get_recommendations([doi], limit=limit)

    @staticmethod
    def _format_paper(paper: dict) -> dict:
        """Format a Semantic Scholar paper for display."""
        authors = paper.get("authors", [])
        author_str = "; ".join(a.get("name", "") for a in authors[:3])
        if len(authors) > 3:
            author_str += " et al."
        ext_ids = paper.get("externalIds", {})
        return {
            "title": paper.get("title", ""),
            "doi": ext_ids.get("DOI", ""),
            "year": paper.get("year"),
            "authors": author_str,
            "s2_id": paper.get("paperId", ""),
        }
