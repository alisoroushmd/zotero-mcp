"""OpenAlex API client — citation graph, retraction data, and bulk queries."""

from __future__ import annotations

import logging
import math
import time

import httpx

from zotero_mcp.config import get_config

logger = logging.getLogger(__name__)

OPENALEX_BASE = "https://api.openalex.org"
TIMEOUT = 10.0
_MAX_RETRIES = 3
_BASE_BACKOFF = 1.5  # seconds; doubled each attempt
_MAX_RETRY_AFTER = 30.0  # cap a server-supplied Retry-After sleep
_MAX_ABSTRACT_CHARS = 10_000  # defensive cap on reconstructed abstracts (ZOT-30)


def _parse_retry_after(value: str | None, fallback: float) -> float:
    """Parse a Retry-After header value to seconds.

    Per RFC 7231 the header may be delta-seconds OR an HTTP-date. We only honor
    the numeric form; anything else (a date, or junk) falls back to ``fallback``
    so a date-valued header never crashes the retry loop (ZOT-24).
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


class OpenAlexClient:
    """Wrapper for the OpenAlex API.

    Used for retraction checks, citation graph, and knowledge graph bulk queries.
    As of Feb 2026, OpenAlex requires a free API key. Register at
    https://openalex.org/users/me and set OPENALEX_API_KEY env var.
    """

    def __init__(self, api_key: str = "", email: str = "") -> None:
        cfg = get_config()
        api_key = api_key or cfg.openalex_api_key
        email = email or cfg.polite_email
        headers = {"User-Agent": f"zotero-mcp/1.0 (mailto:{email})"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=OPENALEX_BASE,
            headers=headers,
            timeout=TIMEOUT,
        )

    def close(self) -> None:
        """Close the pooled HTTP client (ZOT-23)."""
        self._client.close()

    def _get_with_retry(self, path: str, params: dict | None = None) -> httpx.Response:
        """GET with backoff on 429/5xx and transport errors (ZOT-18).

        OpenAlex enforces ~10 req/s and 100k/day. Without this, a rate-limited
        response silently degraded callers to empty results, producing an
        incomplete graph with no error. Honors ``Retry-After`` on 429 (capped),
        exponential backoff on 5xx and transport errors.

        Args:
            path: Request path relative to the OpenAlex base URL.
            params: Optional query parameters.

        Returns:
            The successful ``httpx.Response`` (caller handles 404 / status codes).

        Raises:
            httpx.HTTPError: if all retries are exhausted.
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            backoff = _BASE_BACKOFF * (2**attempt)
            try:
                resp = self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt == _MAX_RETRIES - 1:
                    raise
                logger.warning(
                    "OpenAlex transport error (%s) on %s; retry %d/%d",
                    exc.__class__.__name__,
                    path,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                time.sleep(backoff)
                continue

            if resp.status_code == 429:
                if attempt == _MAX_RETRIES - 1:
                    return resp  # let the caller see the 429 via raise_for_status
                sleep_for = min(
                    _parse_retry_after(resp.headers.get("Retry-After"), backoff),
                    _MAX_RETRY_AFTER,
                )
                logger.warning("OpenAlex 429; sleeping %.1fs before retry", sleep_for)
                time.sleep(sleep_for)
                continue

            if 500 <= resp.status_code < 600 and attempt < _MAX_RETRIES - 1:
                logger.warning("OpenAlex %d on %s; retry %d", resp.status_code, path, attempt + 1)
                time.sleep(backoff)
                continue

            return resp

        # Unreachable on success; only hit if loop exits via transport-error path.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("OpenAlex request failed after retries")  # pragma: no cover

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
            resp = self._get_with_retry(f"/works/doi:{doi}")
            if resp.status_code == 404:
                return None
            # Surface auth failures instead of swallowing them as "not found"
            # (ZOT-09). OpenAlex requires a key as of 2026; without a valid one
            # every call 401/403s, which is indistinguishable from a real 404
            # unless we report it.
            if resp.status_code in (401, 403):
                logger.error("OpenAlex auth failed (HTTP %s) for DOI %s", resp.status_code, doi)
                raise RuntimeError(
                    f"OpenAlex authentication failed (HTTP {resp.status_code}). "
                    "OpenAlex requires a free API key as of 2026: register at "
                    "https://openalex.org/users/me and set OPENALEX_API_KEY."
                )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
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
        authors = "; ".join(a.get("author", {}).get("display_name", "") for a in authorships[:3])
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
            resp = self._get_with_retry(
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

    def get_references(self, doi: str, limit: int = 20) -> list[dict]:
        """Get works referenced by the given DOI.

        Resolves the referenced works in a single batched query via the
        ``openalex:`` filter (ZOT-18) rather than N individual GETs in a thread
        pool. This cuts up to ``limit`` requests down to one, removes the
        per-request rate-limit blind spot, and goes through ``_get_with_retry``.

        Args:
            doi: DOI of the target paper.
            limit: Maximum number of references to resolve.

        Returns:
            List of work summary dicts (order follows the API response).
        """
        work = self.get_work(doi)
        if not work:
            return []

        ref_urls = work.get("referenced_works", [])[: max(0, limit)]
        if not ref_urls:
            return []

        ref_ids = [url.split("/")[-1] for url in ref_urls if url]
        works = self._bulk_get_works_by_openalex_id(ref_ids)
        return [self._format_work_summary(w) for w in works]

    def _bulk_get_works_by_openalex_id(
        self, openalex_ids: list[str], batch_size: int = 50
    ) -> list[dict]:
        """Batch-fetch full work dicts by OpenAlex ID via the ``openalex:`` filter.

        Args:
            openalex_ids: OpenAlex work IDs (e.g. ["W123", "W456"]).
            batch_size: Max IDs per request.

        Returns:
            List of raw OpenAlex work dicts (missing IDs are simply absent).
        """
        all_works: list[dict] = []
        for i in range(0, len(openalex_ids), batch_size):
            batch = openalex_ids[i : i + batch_size]
            id_filter = "openalex:" + "|".join(batch)
            try:
                resp = self._get_with_retry(
                    "/works",
                    params={"filter": id_filter, "per_page": batch_size},
                )
                resp.raise_for_status()
                all_works.extend(resp.json().get("results", []))
            except Exception as exc:
                logger.warning("OpenAlex ref batch %d failed: %s", i, exc)
        return all_works

    def check_published_version(self, doi: str) -> dict:
        """Check if a preprint DOI has been formally published in a journal.

        Inspects the work's type and locations in OpenAlex. A preprint is
        identified by type=="preprint" or primary_location.source.type=="repository".
        A published version is signalled by a location with source.type=="journal"
        whose landing_page_url contains a doi.org link.

        Args:
            doi: DOI of the preprint (e.g. "10.1101/2024.01.01.123456").

        Returns:
            Dict with:
            - is_preprint (bool): whether the item looks like a preprint
            - published_doi (str | None): DOI of published version, if found
            - journal (str | None): journal display name, if found
        """
        work = self.get_work(doi)
        if not work:
            return {"is_preprint": False, "published_doi": None, "journal": None}

        # Determine if this work is a preprint
        is_preprint = work.get("type") == "preprint"
        if not is_preprint:
            primary = work.get("primary_location") or {}
            source = primary.get("source") or {}
            is_preprint = source.get("type") == "repository"

        if not is_preprint:
            return {"is_preprint": False, "published_doi": None, "journal": None}

        # Look for a journal location with a DOI
        for loc in work.get("locations", []):
            source = loc.get("source") or {}
            if source.get("type") == "journal":
                url = loc.get("landing_page_url", "")
                published_doi = None
                if "doi.org/" in url:
                    published_doi = url.split("doi.org/")[-1].strip()
                return {
                    "is_preprint": True,
                    "published_doi": published_doi,
                    "journal": source.get("display_name"),
                }

        return {"is_preprint": True, "published_doi": None, "journal": None}

    def bulk_get_works(self, dois: list[str], batch_size: int = 50) -> list[dict]:
        """Batch-fetch work metadata for multiple DOIs.

        OpenAlex filter syntax: ``doi:10.1/a|10.1/b`` (prefix once, values pipe-separated).
        Up to ~50 per query to stay within URL length limits.

        Args:
            dois: List of DOI strings (without https://doi.org/ prefix).
            batch_size: Max DOIs per API request.

        Returns:
            List of raw OpenAlex work dicts.
        """
        all_works: list[dict] = []
        for i in range(0, len(dois), batch_size):
            batch = dois[i : i + batch_size]
            doi_filter = "doi:" + "|".join(batch)
            try:
                resp = self._get_with_retry(
                    "/works",
                    params={"filter": doi_filter, "per_page": batch_size},
                )
                resp.raise_for_status()
                all_works.extend(resp.json().get("results", []))
            except Exception as exc:
                logger.warning("OpenAlex bulk query failed for batch %d: %s", i, exc)
        return all_works

    def resolve_ids_to_dois(self, openalex_ids: list[str], batch_size: int = 50) -> dict[str, str]:
        """Resolve OpenAlex work IDs to DOIs.

        ``referenced_works`` from OpenAlex are IDs like ``https://openalex.org/W123``,
        not DOIs. This method batch-fetches those works and extracts DOIs.

        Args:
            openalex_ids: List of OpenAlex work IDs (e.g. ["W123", "W456"]).
            batch_size: Max IDs per API request.

        Returns:
            Dict mapping OpenAlex ID -> DOI (only for works that have DOIs).
        """
        id_to_doi: dict[str, str] = {}
        for i in range(0, len(openalex_ids), batch_size):
            batch = openalex_ids[i : i + batch_size]
            id_filter = "openalex:" + "|".join(batch)
            try:
                resp = self._get_with_retry(
                    "/works",
                    params={
                        "filter": id_filter,
                        "per_page": batch_size,
                        "select": "id,doi",
                    },
                )
                resp.raise_for_status()
                for work in resp.json().get("results", []):
                    oa_id = (work.get("id") or "").split("/")[-1]
                    doi = work.get("doi")
                    if oa_id and doi:
                        id_to_doi[oa_id] = doi.replace("https://doi.org/", "")
            except Exception as exc:
                logger.warning("OpenAlex ID resolution failed for batch %d: %s", i, exc)
        return id_to_doi

    @staticmethod
    def extract_topics(work: dict) -> list[dict]:
        """Extract topic hierarchy from an OpenAlex work.

        Args:
            work: Raw OpenAlex work dict from bulk_get_works.

        Returns:
            List of dicts with keys: topic_id, topic_name, subfield, field, domain, score.
        """
        topics = []
        for t in work.get("topics", []):
            topic_id = (t.get("id") or "").split("/")[-1]  # Extract ID from URL
            if not topic_id:
                continue
            topics.append(
                {
                    "topic_id": topic_id,
                    "topic_name": t.get("display_name", ""),
                    "subfield": (t.get("subfield") or {}).get("display_name", ""),
                    "field": (t.get("field") or {}).get("display_name", ""),
                    "domain": (t.get("domain") or {}).get("display_name", ""),
                    "score": t.get("score", 0.0),
                }
            )
        return topics

    @staticmethod
    def reconstruct_abstract(work: dict) -> str | None:
        """Convert OpenAlex abstract_inverted_index to plain text.

        Args:
            work: Raw OpenAlex work dict.

        Returns:
            Reconstructed abstract string, or None if not available.
        """
        inv_index = work.get("abstract_inverted_index")
        if not inv_index:
            return None
        positions: dict[int, str] = {}
        for word, pos_list in inv_index.items():
            for pos in pos_list:
                positions[pos] = word
        if not positions:
            return None
        text = " ".join(positions[i] for i in sorted(positions))
        # Defensive cap (ZOT-30): a malformed/huge inverted index should never
        # produce a multi-MB string fed to the LLM or stored in SQLite.
        if len(text) > _MAX_ABSTRACT_CHARS:
            text = text[:_MAX_ABSTRACT_CHARS].rstrip() + "…"
        return text

    @staticmethod
    def extract_authorships(work: dict) -> list[dict]:
        """Extract structured author records from an OpenAlex work.

        Args:
            work: Raw OpenAlex work dict from bulk_get_works.

        Returns:
            List of dicts with keys: openalex_author_id, display_name, orcid,
            institution, position.
        """
        authors = []
        for i, a in enumerate(work.get("authorships", [])):
            author = a.get("author", {})
            author_id = (author.get("id") or "").split("/")[-1]
            if not author_id:
                continue
            institutions = a.get("institutions", [])
            institution = institutions[0].get("display_name", "") if institutions else ""
            authors.append(
                {
                    "openalex_author_id": author_id,
                    "display_name": author.get("display_name", ""),
                    "orcid": (author.get("orcid") or "").replace("https://orcid.org/", ""),
                    "institution": institution,
                    "position": i,
                }
            )
        return authors
