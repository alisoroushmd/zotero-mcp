"""Semantic Scholar API client — paper recommendations and similarity.

Uses raw httpx (no third-party wrapper). Only two endpoints needed:
recommendations and single-paper lookup.
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

S2_BASE = "https://api.semanticscholar.org"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


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

    def get_recommendations(self, seed_dois: list[str], limit: int = 10) -> list[dict]:
        """Get paper recommendations based on seed papers.

        Uses Semantic Scholar's recommendations endpoint which finds
        papers related to the given seed set.

        Args:
            seed_dois: List of DOIs to use as positive seeds (max 50).
            limit: Max recommendations to return.

        Returns:
            List of recommended paper dicts with title, doi, year, authors.
        """
        paper_ids = [{"doi": doi} for doi in seed_dois[:50]]

        try:
            resp = self._client.post(
                "/recommendations/v1/papers/",
                json={"positivePaperIds": paper_ids, "negativePaperIds": []},
                params={
                    "limit": min(limit, 50),
                    "fields": "title,year,authors,externalIds",
                },
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
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
        except Exception as exc:
            logger.warning("Semantic Scholar recommendations failed: %s", exc)
            return []

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
