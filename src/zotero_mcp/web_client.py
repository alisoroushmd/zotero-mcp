"""Zotero Web API client — primary path for reads and writes."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlparse

import defusedxml.ElementTree as ElementTree

import httpx

if TYPE_CHECKING:
    from zotero_mcp.local_client import LocalClient

logger = logging.getLogger(__name__)


_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _validate_url(url: str) -> None:
    """Validate a URL to prevent SSRF attacks.

    Blocks private/loopback IP ranges and non-HTTPS schemes.

    Raises:
        ValueError: If the URL targets a private network or uses a blocked scheme.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme must be http or https, got: {parsed.scheme!r}")

    hostname = parsed.hostname or ""
    # Block obvious internal hostnames
    if hostname in ("localhost", "0.0.0.0"):
        raise ValueError(f"URLs targeting internal hosts are not allowed: {hostname}")

    # Try to parse hostname as IP address and check against blocked ranges
    try:
        addr = ipaddress.ip_address(hostname)
        for network in _BLOCKED_NETWORKS:
            if addr in network:
                raise ValueError(
                    f"URLs targeting private/internal networks are not allowed: {hostname}"
                )
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
        # Not an IP literal — hostname is a domain name, allow it

WEB_BASE = "https://api.zotero.org"
TRANSLATE_URL = "https://translate.zotero.org/search"
TRANSLATE_WEB_URL = "https://translate.zotero.org/web"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
def _get_polite_email() -> str:
    """Get polite email from config (lazy to avoid import-time env read)."""
    from zotero_mcp.config import get_config
    return get_config().polite_email
SEARCH_TIMEOUT = httpx.Timeout(
    45.0, connect=5.0
)  # searches can be slow on large libraries


# medRxiv/bioRxiv DOI prefixes (medRxiv migrated from 10.1101 to 10.64898)
_PREPRINT_DOI_PREFIXES = ("10.1101/", "10.64898/")


def _is_preprint_doi(doi: str) -> bool:
    """Check if a DOI belongs to bioRxiv or medRxiv."""
    return doi.startswith(_PREPRINT_DOI_PREFIXES)


def _is_valid_pdf(content: bytes) -> bool:
    """Validate PDF by magic bytes rather than content length."""
    return len(content) >= 5 and content[:5] == b"%PDF-"


def _retry_request(
    fn,
    max_attempts: int = 3,
    base_delay: float = 1.0,
) -> httpx.Response:
    """Call fn() and retry on 429 with exponential backoff.

    Args:
        fn: Zero-argument callable that returns an httpx.Response.
        max_attempts: Maximum number of attempts (including first).
        base_delay: Base sleep in seconds; doubles each retry.

    Returns:
        The successful response.

    Raises:
        httpx.HTTPStatusError: If rate-limited on the final attempt.
    """
    resp = fn()
    for attempt in range(1, max_attempts):
        if resp.status_code != 429:
            return resp
        retry_after = float(
            resp.headers.get("Retry-After", base_delay * (2 ** (attempt - 1)))
        )
        delay = min(retry_after, 30.0)
        logger.warning(
            "Rate limited (429), retrying in %.1fs (attempt %d/%d)",
            delay,
            attempt,
            max_attempts,
        )
        time.sleep(delay)
        resp = fn()
    if resp.status_code == 429:
        try:
            resp.raise_for_status()
        except RuntimeError:
            # Response has no request attached (e.g. in tests); raise directly
            raise httpx.HTTPStatusError(
                f"429 Too Many Requests after {max_attempts} attempts",
                request=httpx.Request("GET", ""),
                response=resp,
            )
    return resp


class WebClient:
    """Primary client for Zotero Web API — handles reads and writes.

    Reads use the Web API by default. If a LocalClient is provided,
    read-modify-write operations try the local API first (faster,
    no rate limits) before falling back to web reads.
    """

    def __init__(
        self,
        api_key: str,
        user_id: str,
        local_client: LocalClient | None = None,
    ) -> None:
        if not api_key or not user_id:
            raise ValueError(
                "ZOTERO_API_KEY and ZOTERO_USER_ID are required for write operations. "
                "Get your API key at https://www.zotero.org/settings/keys"
            )
        self._api_key = api_key
        self._user_id = user_id
        self._base = f"{WEB_BASE}/users/{user_id}"
        self._local = local_client
        self._web_client = httpx.Client(
            base_url=self._base,
            headers={"Zotero-API-Key": api_key, "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        self._translate_client = httpx.Client(timeout=TIMEOUT)
        self._pubmed_client = httpx.Client(
            base_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
            timeout=TIMEOUT,
        )

    # -- Web API read methods (primary read path) --

    def search_items(
        self,
        query: str,
        limit: int = 25,
        item_type: str | None = None,
        tag: str | None = None,
    ) -> list[dict]:
        """Search items via Web API. Excludes attachments and notes.

        Args:
            query: Keyword search string.
            limit: Max results (1–100).
            item_type: Filter by Zotero item type, e.g. "journalArticle".
            tag: Filter by tag name (exact match).

        Returns:
            List of item summary dicts.
        """
        from zotero_mcp.local_client import _format_summary

        params: dict = {"q": query, "limit": limit}
        if item_type:
            params["itemType"] = item_type
        if tag:
            params["tag"] = tag
        resp = self._web_client.get(
            "/items/top",
            params=params,
            timeout=SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        return [_format_summary(item) for item in resp.json()]

    def get_item(self, item_key: str, fmt: str = "json") -> dict | str:
        """Get item metadata or BibTeX via Web API."""
        params = {}
        if fmt == "bibtex":
            params["format"] = "bibtex"
        resp = self._web_client.get(f"/items/{item_key}", params=params)
        resp.raise_for_status()
        if fmt == "bibtex":
            return resp.text
        data = resp.json()
        return data.get("data", data)

    def get_collections(self) -> list[dict]:
        """List all collections via Web API."""
        resp = self._web_client.get("/collections")
        resp.raise_for_status()
        return [
            {
                "key": c["data"]["key"],
                "name": c["data"]["name"],
                "parent_key": c["data"].get("parentCollection") or "",
                "num_items": c.get("meta", {}).get("numItems", 0),
            }
            for c in resp.json()
        ]

    def get_collection_items(self, collection_key: str, limit: int = 100) -> list[dict]:
        """Get items in a collection via Web API."""
        from zotero_mcp.local_client import _format_summary

        resp = self._web_client.get(
            f"/collections/{collection_key}/items/top",
            params={"limit": limit},
        )
        resp.raise_for_status()
        return [_format_summary(item) for item in resp.json()]

    def get_children(self, parent_key: str, item_type: str | None = None) -> list[dict]:
        """Get child items via Web API."""
        params = {}
        if item_type:
            params["itemType"] = item_type
        resp = self._web_client.get(
            f"/items/{parent_key}/children", params=params or None
        )
        resp.raise_for_status()
        return [item.get("data", item) for item in resp.json()]

    def get_notes(self, parent_key: str) -> list[dict]:
        """Get child notes via Web API."""
        children = self.get_children(parent_key, item_type="note")
        return [
            {
                "key": data.get("key", ""),
                "note": data.get("note", ""),
                "tags": [t["tag"] for t in data.get("tags", [])],
                "dateModified": data.get("dateModified", ""),
            }
            for data in children
        ]

    def download_attachment(self, attachment_key: str) -> bytes:
        """Download an attachment file from Zotero cloud storage.

        Args:
            attachment_key: Zotero key of the attachment item.

        Returns:
            Raw file bytes.

        Raises:
            httpx.HTTPStatusError: If the download fails (404, 403, etc.).
        """
        resp = self._web_client.get(f"/items/{attachment_key}/file")
        resp.raise_for_status()
        return resp.content

    def resolve_pmid_to_pmcid(self, pmid: str) -> str | None:
        """Convert a PubMed ID to a PubMed Central ID.

        Args:
            pmid: PubMed ID string (digits only).

        Returns:
            PMCID string (e.g. "PMC9046468"), or None if not in PMC.
        """
        try:
            resp = self._pubmed_client.get(
                "/esearch.fcgi",
                params={"db": "pmc", "term": f"{pmid}[pmid]", "retmode": "json"},
            )
            if resp.status_code == 200:
                ids = resp.json().get("esearchresult", {}).get("idlist", [])
                if ids:
                    return f"PMC{ids[0]}"
        except Exception as exc:
            logger.warning("PMID→PMCID lookup failed for %s: %s", pmid, exc)
        return None

    def check_crossref_updates(self, doi: str) -> dict:
        """Check CrossRef for retractions, corrections, and errata.

        Args:
            doi: DOI to check.

        Returns:
            Dict with has_retraction, retraction_doi, retraction_date,
            and corrections list.
        """
        result: dict = {
            "has_retraction": False,
            "retraction_doi": "",
            "retraction_date": "",
            "corrections": [],
        }

        try:
            resp = httpx.get(
                f"https://api.crossref.org/works/{doi}",
                headers={
                    "User-Agent": f"zotero-mcp/1.0 (mailto:{_get_polite_email()})"
                },
                timeout=TIMEOUT,
            )
            if resp.status_code != 200:
                return result
            work = resp.json().get("message", {})
        except Exception as exc:
            logger.warning("CrossRef update check failed for %s: %s", doi, exc)
            return result

        updates = work.get("update-to", [])
        for update in updates:
            update_type = update.get("type", "").lower()
            update_doi = update.get("DOI", "")
            date_parts = update.get("updated", {}).get("date-parts", [[]])
            date_str = "-".join(str(p) for p in date_parts[0]) if date_parts[0] else ""

            if update_type == "retraction":
                result["has_retraction"] = True
                result["retraction_doi"] = update_doi
                result["retraction_date"] = date_str
            else:
                result["corrections"].append(
                    {
                        "type": update_type,
                        "doi": update_doi,
                        "date": date_str,
                    }
                )

        return result

    def check_crossref_published(self, doi: str) -> dict:
        """Check CrossRef for a published journal version of a preprint.

        Reads the ``relation.is-preprint-of`` field, which publishers populate
        when a preprint DOI is linked to its final journal article.

        Args:
            doi: DOI to check (typically a bioRxiv/medRxiv DOI starting with 10.1101/ or 10.64898/).

        Returns:
            Dict with published_doi (str | None).
        """
        result: dict = {"published_doi": None}
        try:
            resp = httpx.get(
                f"https://api.crossref.org/works/{doi}",
                headers={
                    "User-Agent": f"zotero-mcp/1.0 (mailto:{_get_polite_email()})"
                },
                timeout=TIMEOUT,
            )
            if resp.status_code != 200:
                return result
            work = resp.json().get("message", {})
        except Exception as exc:
            logger.warning(
                "CrossRef published-version check failed for %s: %s", doi, exc
            )
            return result

        for item in work.get("relation", {}).get("is-preprint-of", []):
            if item.get("id-type") == "doi" and item.get("id"):
                result["published_doi"] = item["id"]
                break

        return result

    # -- Read helpers for read-modify-write operations --

    def _read_item(self, item_key: str) -> dict:
        """Read item for read-modify-write: tries local (fast), falls back to web."""
        if self._local:
            try:
                result = self._local.get_item(item_key)
                if isinstance(result, dict):
                    return result
            except RuntimeError:
                pass  # Local unavailable, fall through to web
        # Web API read
        result = self.get_item(item_key)
        if isinstance(result, str):
            raise RuntimeError(f"Expected dict for item {item_key}, got BibTeX string")
        return result

    def _check_duplicate_doi(self, doi: str) -> dict | None:
        """Check if a DOI already exists in the library. Returns item summary or None."""
        if not doi:
            return None
        # Try local first (faster), fall back to web search
        try:
            if self._local:
                results = self._local.search_items(doi, limit=10)
            else:
                results = self.search_items(doi, limit=10)
            for item in results:
                if item.get("DOI", "").strip().lower() == doi.strip().lower():
                    return item
        except Exception as exc:
            logger.warning("Duplicate DOI check failed for %s: %s", doi, exc)
        return None

    def _check_duplicate_title(
        self, title: str, threshold: float = 0.90
    ) -> dict | None:
        """Check if a similar title already exists in the library.

        Normalizes both titles (lowercase, strip punctuation) and uses
        SequenceMatcher for fuzzy comparison.

        Args:
            title: Title to check against library.
            threshold: Minimum similarity ratio (0-1) to consider a match.

        Returns:
            Item summary dict with added 'similarity' field, or None.
        """
        import re as _re
        from difflib import SequenceMatcher

        def _normalize(t: str) -> str:
            t = t.lower().strip()
            t = _re.sub(r"[^\w\s]", "", t)
            t = _re.sub(r"\s+", " ", t)
            return t

        normalized = _normalize(title)
        if not normalized:
            return None

        # Search using first few significant words
        search_words = normalized.split()[:4]
        search_query = " ".join(search_words)

        try:
            if self._local:
                results = self._local.search_items(search_query, limit=20)
            else:
                results = self.search_items(search_query, limit=20)
        except Exception:
            return None

        for item in results:
            existing_normalized = _normalize(item.get("title", ""))
            ratio = SequenceMatcher(None, normalized, existing_normalized).ratio()
            if ratio >= threshold:
                item["similarity"] = round(ratio, 3)
                item["match_type"] = "title_similarity"
                return item

        return None

    def find_duplicates(
        self,
        collection_key: str | None = None,
        limit: int = 100,
        title_threshold: float = 0.85,
    ) -> dict:
        """Scan library for duplicate items.

        Groups by exact DOI match, then clusters remaining items by
        normalized title similarity.

        Args:
            collection_key: Optional collection to scope the scan.
            limit: Max items to scan.
            title_threshold: Similarity ratio for title matching (0-1).

        Returns:
            Dict with duplicate_groups, total_groups, total_duplicate_items.
        """
        import re as _re
        from difflib import SequenceMatcher

        def _normalize(t: str) -> str:
            t = t.lower().strip()
            t = _re.sub(r"[^\w\s]", "", t)
            t = _re.sub(r"\s+", " ", t)
            return t

        # Fetch items
        if collection_key:
            if self._local:
                try:
                    items = self._local.get_collection_items(collection_key, limit)
                except RuntimeError:
                    items = self.get_collection_items(collection_key, limit)
            else:
                items = self.get_collection_items(collection_key, limit)
        else:
            if self._local:
                try:
                    items = self._local.search_items("", limit)
                except RuntimeError:
                    items = self.search_items("", limit)
            else:
                items = self.search_items("", limit)

        groups: list[dict] = []

        # Phase 1: Group by exact DOI
        doi_map: dict[str, list[dict]] = {}
        no_doi_items: list[dict] = []
        for item in items:
            doi = (item.get("DOI") or "").strip().lower()
            if doi:
                doi_map.setdefault(doi, []).append(item)
            else:
                no_doi_items.append(item)

        for doi, group_items in doi_map.items():
            if len(group_items) >= 2:
                groups.append(
                    {
                        "match_type": "doi",
                        "doi": doi,
                        "items": [
                            {
                                "key": i["key"],
                                "title": i["title"],
                                "date": i.get("date", ""),
                            }
                            for i in group_items
                        ],
                    }
                )

        # Phase 2: Cluster remaining items by title similarity
        used: set[str] = set()
        for i, item_a in enumerate(no_doi_items):
            if item_a["key"] in used:
                continue
            norm_a = _normalize(item_a.get("title", ""))
            if not norm_a:
                continue
            cluster = [item_a]
            first_ratio = 0.0
            for item_b in no_doi_items[i + 1 :]:
                if item_b["key"] in used:
                    continue
                norm_b = _normalize(item_b.get("title", ""))
                if not norm_b:
                    continue
                ratio = SequenceMatcher(None, norm_a, norm_b).ratio()
                if ratio >= title_threshold:
                    if not cluster[1:]:
                        first_ratio = ratio
                    cluster.append(item_b)
                    used.add(item_b["key"])
            if len(cluster) >= 2:
                used.add(item_a["key"])
                groups.append(
                    {
                        "match_type": "title_similarity",
                        "similarity": round(first_ratio, 3),
                        "items": [
                            {
                                "key": i["key"],
                                "title": i["title"],
                                "date": i.get("date", ""),
                            }
                            for i in cluster
                        ],
                    }
                )

        total_dup_items = sum(len(g["items"]) for g in groups)
        return {
            "duplicate_groups": groups,
            "total_groups": len(groups),
            "total_duplicate_items": total_dup_items,
        }

    def _extract_created_key(self, result: dict) -> str:
        """Extract item key from a Zotero Web API creation response."""
        successful = result.get("successful", result.get("success", {}))
        if not successful:
            raise RuntimeError(f"Failed to create item: {result.get('failed', result)}")
        val = list(successful.values())[0]
        if isinstance(val, dict):
            return val.get("key", val.get("data", {}).get("key", ""))
        return str(val)

    def _apply_collections_and_tags(
        self, metadata: dict, collection_keys: list[str] | None, tags: list[str] | None
    ) -> None:
        """Apply optional collections and tags to item metadata in place."""
        if collection_keys:
            metadata["collections"] = collection_keys
        if tags:
            existing_tags = metadata.get("tags", [])
            for t in tags:
                existing_tags.append({"tag": t})
            metadata["tags"] = existing_tags

    def create_item_from_identifier(
        self,
        identifier: str,
        collection_keys: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Resolve identifier via translation server, create item via Web API.

        Checks for duplicate DOI before creating. If found, returns existing
        item with duplicate=True flag.

        Args:
            identifier: PMID, DOI, or PubMed URL.
            collection_keys: Optional collection keys to assign.
            tags: Optional tag strings to add.

        Returns:
            Dict with "key", "title", and optionally "duplicate": True.

        Raises:
            RuntimeError: If translation server or Web API fails.
        """
        metadata = self._resolve_identifier(identifier)

        # Check for duplicate by DOI
        doi = metadata.get("DOI", "")
        existing = self._check_duplicate_doi(doi)
        if existing:
            return {
                "key": existing["key"],
                "title": existing["title"],
                "duplicate": True,
                "message": f"Item already exists in library with DOI {doi}",
            }

        self._apply_collections_and_tags(metadata, collection_keys, tags)

        # Create item via Web API
        resp = _retry_request(lambda: self._web_client.post("/items", json=[metadata]))
        resp.raise_for_status()

        key = self._extract_created_key(resp.json())
        logger.info("Created item %s from identifier %s", key, identifier)
        return {
            "key": key,
            "title": metadata.get("title", ""),
            "DOI": metadata.get("DOI", ""),
            "date": metadata.get("date", ""),
            "note": "Item created on Zotero web. Sync Zotero desktop to see it locally.",
        }

    def _resolve_identifier(self, identifier: str) -> dict:
        """Resolve PMID/DOI/URL to Zotero item metadata.

        Tries in order: Zotero translation server, PubMed efetch,
        CrossRef. Each fallback handles different identifier types
        and produces progressively less rich metadata.
        """
        # Try Zotero translation server first (handles everything)
        try:
            resp = self._translate_client.post(
                TRANSLATE_URL,
                content=identifier,
                headers={"Content-Type": "text/plain"},
            )
            resp.raise_for_status()
            items = resp.json()
            if items and len(items) > 0:
                return items[0]
        except (httpx.ConnectError, httpx.HTTPStatusError):
            logger.info("Translation server unavailable, trying fallbacks")
        except Exception:
            logger.info("Translation server error, trying fallbacks")

        # Fallback 1: PubMed efetch (biomedical content, includes preprints)
        metadata = self._resolve_via_pubmed(identifier)
        if metadata:
            return metadata

        # Fallback 2: CrossRef (all DOI-registered content — books, CS, etc.)
        metadata = self._resolve_via_crossref(identifier)
        if metadata:
            return metadata

        raise RuntimeError(
            f"No metadata found for identifier '{identifier}'. "
            f"The Zotero translation server may be down. "
            f"Try adding the item manually in Zotero."
        )

    def _resolve_via_pubmed(self, identifier: str) -> dict | None:
        """Resolve PMID or DOI via PubMed efetch XML.

        Uses efetch instead of esummary to get abstracts and publication
        types. Maps PubMed publication types to Zotero item types
        (e.g. preprints, conference papers).

        Returns:
            Zotero-compatible item metadata dict with abstractNote, or None.
        """
        pmid = None
        identifier = identifier.strip()

        if re.match(r"^\d+$", identifier):
            pmid = identifier
        elif "pubmed" in identifier.lower() or "ncbi.nlm.nih.gov" in identifier.lower():
            match = re.search(r"(\d{6,})", identifier)
            if match:
                pmid = match.group(1)
        elif identifier.startswith("10.") or "doi.org" in identifier:
            doi = identifier.replace("https://doi.org/", "").replace(
                "http://doi.org/", ""
            )
            try:
                search_resp = self._pubmed_client.get(
                    "/esearch.fcgi",
                    params={"db": "pubmed", "term": f"{doi}[doi]", "retmode": "json"},
                )
                search_resp.raise_for_status()
                ids = search_resp.json().get("esearchresult", {}).get("idlist", [])
                if ids:
                    pmid = ids[0]
            except Exception:
                return None

        if not pmid:
            return None

        try:
            resp = self._pubmed_client.get(
                "/efetch.fcgi",
                params={"db": "pubmed", "id": pmid, "rettype": "xml", "retmode": "xml"},
            )
            resp.raise_for_status()
            return self._parse_pubmed_xml(resp.text, pmid)
        except Exception as exc:
            logger.warning("PubMed efetch failed for PMID %s: %s", pmid, exc)
            return None

    @staticmethod
    def _parse_pubmed_xml(xml_text: str, pmid: str) -> dict | None:
        """Parse PubMed efetch XML into Zotero-compatible metadata.

        Args:
            xml_text: Raw XML from efetch.
            pmid: The PubMed ID (for the extra field).

        Returns:
            Zotero item dict with abstractNote, or None on parse failure.
        """
        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError:
            return None

        article = root.find(".//PubmedArticle")
        if article is None:
            return None

        medline = article.find("MedlineCitation")
        if medline is None:
            return None

        art = medline.find("Article")
        if art is None:
            return None

        # Title — itertext() captures text inside nested tags (e.g. <i>, <sub>)
        title_el = art.find("ArticleTitle")
        title = "".join(title_el.itertext()).rstrip(".") if title_el is not None else ""

        # Abstract — concatenate all AbstractText elements (handles
        # structured abstracts with labeled sections like BACKGROUND, METHODS)
        abstract_parts: list[str] = []
        abstract_el = art.find("Abstract")
        if abstract_el is not None:
            for at in abstract_el.findall("AbstractText"):
                label = at.get("Label", "")
                text = "".join(at.itertext()).strip()
                if label and text:
                    abstract_parts.append(f"{label}: {text}")
                elif text:
                    abstract_parts.append(text)
        abstract = "\n".join(abstract_parts)

        # Authors
        creators: list[dict] = []
        author_list = art.find("AuthorList")
        if author_list is not None:
            for author in author_list.findall("Author"):
                last = author.findtext("LastName", "")
                first = author.findtext("ForeName", "")
                if last:
                    creators.append(
                        {"creatorType": "author", "lastName": last, "firstName": first}
                    )

        # Journal
        journal_el = art.find("Journal")
        journal_title = ""
        volume = ""
        issue = ""
        date = ""
        issn = ""
        if journal_el is not None:
            journal_title = journal_el.findtext("Title", "")
            ji = journal_el.find("JournalIssue")
            if ji is not None:
                volume = ji.findtext("Volume", "")
                issue = ji.findtext("Issue", "")
                pub_date = ji.find("PubDate")
                if pub_date is not None:
                    year = pub_date.findtext("Year", "")
                    month = pub_date.findtext("Month", "")
                    day = pub_date.findtext("Day", "")
                    date = "-".join(p for p in [year, month, day] if p)
            issn_el = journal_el.find("ISSN")
            if issn_el is not None:
                issn = issn_el.text or ""

        pages = art.findtext("Pagination/MedlinePgn", "")

        # DOI
        doi = ""
        article_data = article.find("PubmedData")
        if article_data is not None:
            for aid in article_data.findall(".//ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = aid.text or ""
                    break

        # Publication types -> Zotero item type
        item_type = "journalArticle"
        pub_types: list[str] = []
        pub_type_list = art.find("PublicationTypeList")
        if pub_type_list is not None:
            pub_types = [
                (pt.text or "").lower()
                for pt in pub_type_list.findall("PublicationType")
            ]
        if "preprint" in pub_types:
            item_type = "preprint"
        elif any("congress" in pt for pt in pub_types):
            item_type = "conferencePaper"

        result: dict = {
            "itemType": item_type,
            "title": title,
            "creators": creators,
            "date": date,
            "DOI": doi,
            "publicationTitle": journal_title,
            "volume": volume,
            "issue": issue,
            "pages": pages,
            "ISSN": issn,
            "extra": f"PMID: {pmid}",
        }
        if abstract:
            result["abstractNote"] = abstract

        return result

    def _resolve_via_crossref(self, identifier: str) -> dict | None:
        """Resolve a DOI via CrossRef API.

        Covers all DOI-registered content: journal articles, book chapters,
        conference papers, preprints (arXiv, SSRN), datasets, etc.

        Returns:
            Zotero-compatible item metadata dict, or None.
        """
        identifier = identifier.strip()
        doi = ""
        if identifier.startswith("10."):
            doi = identifier
        elif "doi.org/" in identifier:
            doi = re.sub(r"^https?://doi\.org/", "", identifier)

        if not doi:
            return None

        try:
            resp = httpx.get(
                f"https://api.crossref.org/works/{doi}",
                headers={
                    "User-Agent": f"zotero-mcp/1.0 (mailto:{_get_polite_email()})"
                },
                timeout=TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            work = resp.json().get("message", {})
            if not work:
                return None
            return self._parse_crossref_work(work, doi)
        except Exception as exc:
            logger.warning("CrossRef resolve failed for %s: %s", identifier, exc)
            return None

    @staticmethod
    def _parse_crossref_work(work: dict, doi: str) -> dict | None:
        """Parse a CrossRef work object into Zotero-compatible metadata.

        Args:
            work: The "message" dict from CrossRef API response.
            doi: The DOI string.

        Returns:
            Zotero item dict, or None if essential fields are missing.
        """
        crossref_type_map: dict[str, str] = {
            "journal-article": "journalArticle",
            "book-chapter": "bookSection",
            "book": "book",
            "proceedings-article": "conferencePaper",
            "posted-content": "preprint",
            "report": "report",
            "dataset": "document",
            "monograph": "book",
            "edited-book": "book",
            "reference-book": "book",
            "dissertation": "thesis",
        }

        cr_type = work.get("type", "")
        item_type = crossref_type_map.get(cr_type, "journalArticle")

        titles = work.get("title", [])
        title = titles[0] if titles else ""
        if not title:
            return None

        creators: list[dict] = []
        for author in work.get("author", []):
            last = author.get("family", "")
            first = author.get("given", "")
            if last:
                creators.append(
                    {"creatorType": "author", "lastName": last, "firstName": first}
                )

        # Date -- prefer published-print, then published-online, then created
        date = ""
        for date_field in ["published-print", "published-online", "created"]:
            date_obj = work.get(date_field, {})
            parts = date_obj.get("date-parts", [[]])[0]
            if parts:
                date = "-".join(str(p) for p in parts)
                break

        container = work.get("container-title", [])
        publication_title = container[0] if container else ""

        # Abstract (CrossRef provides HTML, strip tags for plain text)
        abstract_html = work.get("abstract", "")
        abstract = (
            re.sub(r"<[^>]+>", "", abstract_html).strip() if abstract_html else ""
        )

        volume = work.get("volume", "")
        issue = work.get("issue", "")
        pages = work.get("page", "")
        publisher = work.get("publisher", "")
        issn_list = work.get("ISSN", [])
        issn = issn_list[0] if issn_list else ""
        isbn_list = work.get("ISBN", [])
        isbn = isbn_list[0] if isbn_list else ""

        result: dict = {
            "itemType": item_type,
            "title": title,
            "creators": creators,
            "date": date,
            "DOI": doi,
        }

        if publication_title:
            if item_type == "bookSection":
                result["bookTitle"] = publication_title
            elif item_type == "conferencePaper":
                result["proceedingsTitle"] = publication_title
            else:
                result["publicationTitle"] = publication_title
        if volume:
            result["volume"] = volume
        if issue:
            result["issue"] = issue
        if pages:
            result["pages"] = pages
        if publisher:
            result["publisher"] = publisher
        if issn:
            result["ISSN"] = issn
        if isbn:
            result["ISBN"] = isbn
        if abstract:
            result["abstractNote"] = abstract

        return result

    def create_item_from_url(
        self,
        url: str,
        title: str | None = None,
        collection_keys: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Create a Zotero item from a URL.

        Tries Zotero's translation server /web endpoint to scrape metadata.
        Falls back to identifier resolution if a DOI can be extracted from
        the URL, then to a basic webpage item.

        Args:
            url: Web URL (FDA page, preprint, dataset documentation, etc.).
            title: Optional title override. Used in fallback if scraping fails.
            collection_keys: Optional collection keys to assign.
            tags: Optional tag strings to add.

        Returns:
            Dict with "key" and "title".
        """
        from datetime import date

        _validate_url(url)

        metadata = None

        # Try translation server /web endpoint (scrapes the page)
        try:
            resp = self._translate_client.post(
                TRANSLATE_WEB_URL,
                content=url,
                headers={"Content-Type": "text/plain"},
            )
            resp.raise_for_status()
            items = resp.json()
            if items and len(items) > 0:
                metadata = items[0]
        except Exception as exc:
            logger.warning("Translation server /web failed for %s: %s", url, exc)

        # Fallback: try to extract a DOI from the URL and resolve it
        if not metadata:
            extracted_doi = self._extract_doi_from_url(url)
            if extracted_doi:
                try:
                    metadata = self._resolve_via_pubmed(extracted_doi)
                    if not metadata:
                        metadata = self._resolve_via_crossref(extracted_doi)
                except Exception:
                    pass

        # Final fallback: bare webpage item
        if not metadata:
            metadata = {
                "itemType": "webpage",
                "title": title or url,
                "url": url,
                "accessDate": date.today().isoformat(),
                "websiteTitle": "",
            }

        # Override title if provided
        if title:
            metadata["title"] = title

        # Check for duplicate by DOI
        doi = metadata.get("DOI", "")
        if doi:
            existing = self._check_duplicate_doi(doi)
            if existing:
                return {
                    "key": existing["key"],
                    "title": existing["title"],
                    "duplicate": True,
                    "match_type": "doi",
                    "message": f"Item already exists in library with DOI {doi}",
                }

        # Ensure URL is preserved on the item
        if "url" not in metadata:
            metadata["url"] = url

        self._apply_collections_and_tags(metadata, collection_keys, tags)

        resp = _retry_request(lambda: self._web_client.post("/items", json=[metadata]))
        resp.raise_for_status()

        key = self._extract_created_key(resp.json())
        logger.info("Created item %s from URL %s", key, url)
        return {
            "key": key,
            "title": metadata.get("title", ""),
            "item_type": metadata.get("itemType", "webpage"),
            "note": "Item created on Zotero web. Sync Zotero desktop to see it locally.",
        }

    @staticmethod
    def _extract_doi_from_url(url: str) -> str:
        """Extract a DOI from common URL patterns.

        Handles doi.org, arxiv.org, biorxiv.org, medrxiv.org, and
        other sites that embed DOIs in their URLs.

        Args:
            url: A web URL.

        Returns:
            DOI string, or empty string if none found.
        """
        # doi.org direct links
        match = re.search(r"doi\.org/(10\.\d{4,}/[^\s?#]+)", url)
        if match:
            return match.group(1)

        # arXiv abstract pages -> arXiv DOI
        match = re.search(r"arxiv\.org/abs/(\d{4}\.\d{4,})", url)
        if match:
            return f"10.48550/arXiv.{match.group(1)}"

        # bioRxiv / medRxiv
        match = re.search(r"(?:bio|med)rxiv\.org/content/(10\.1101/[^\s?#/]+)", url)
        if match:
            return match.group(1)

        return ""

    def create_item_manual(
        self,
        item_type: str,
        title: str,
        creators: list[dict] | None = None,
        date: str = "",
        url: str = "",
        doi: str = "",
        publication_title: str = "",
        volume: str = "",
        issue: str = "",
        pages: str = "",
        publisher: str = "",
        abstract: str = "",
        extra: str = "",
        collection_keys: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Create a Zotero item with manually provided metadata.

        Use when no identifier or URL can resolve the item automatically.
        Claude can populate fields from context (web search, chat, etc.).

        Args:
            item_type: Zotero item type (journalArticle, report, webpage,
                       document, statute, hearing, etc.).
            title: Item title.
            creators: List of {"creatorType": "author", "firstName": "J", "lastName": "Doe"}.
            date: Publication date (e.g. "2024", "2024-03-15", "March 2024").
            url: URL if applicable.
            doi: DOI if known.
            publication_title: Journal name, report series, etc.
            volume: Volume number.
            issue: Issue number.
            pages: Page range.
            publisher: Publisher or issuing organization.
            abstract: Abstract or summary.
            extra: Extra field (for PMID, document numbers, etc.).
            collection_keys: Optional collection keys to assign.
            tags: Optional tag strings to add.

        Returns:
            Dict with "key" and "title".
        """
        metadata: dict = {
            "itemType": item_type,
            "title": title,
            "creators": creators or [],
            "date": date,
        }

        # Add optional fields only if provided
        field_mapping = {
            "url": url,
            "DOI": doi,
            "publicationTitle": publication_title,
            "volume": volume,
            "issue": issue,
            "pages": pages,
            "publisher": publisher,
            "abstractNote": abstract,
            "extra": extra,
        }
        for zotero_field, value in field_mapping.items():
            if value:
                metadata[zotero_field] = value

        # Check for duplicates: DOI first, then title similarity
        if doi:
            existing = self._check_duplicate_doi(doi)
            if existing:
                return {
                    "key": existing["key"],
                    "title": existing["title"],
                    "duplicate": True,
                    "match_type": "doi",
                    "message": f"Item already exists in library with DOI {doi}",
                }

        if not doi and title:
            existing = self._check_duplicate_title(title)
            if existing:
                return {
                    "key": existing["key"],
                    "title": existing.get("title", ""),
                    "duplicate": True,
                    "match_type": existing.get("match_type", "title_similarity"),
                    "similarity": existing.get("similarity", 0),
                    "message": "Similar item already exists in library",
                }

        self._apply_collections_and_tags(metadata, collection_keys, tags)

        resp = _retry_request(lambda: self._web_client.post("/items", json=[metadata]))
        resp.raise_for_status()

        key = self._extract_created_key(resp.json())
        return {
            "key": key,
            "title": title,
            "item_type": item_type,
            "note": "Item created on Zotero web. Sync Zotero desktop to see it locally.",
        }

    def create_note(
        self,
        parent_key: str,
        content: str,
        tags: list[str] | None = None,
    ) -> dict:
        """Create a note attached to a parent item.

        Args:
            parent_key: Zotero item key to attach the note to.
            content: Note content (HTML). Plain text is also accepted.
            tags: Optional tag strings to add to the note.

        Returns:
            Dict with "key" and "parent_key".
        """
        note_data: dict = {
            "itemType": "note",
            "parentItem": parent_key,
            "note": content,
            "tags": [{"tag": t} for t in (tags or [])],
        }

        resp = _retry_request(lambda: self._web_client.post("/items", json=[note_data]))
        resp.raise_for_status()

        key = self._extract_created_key(resp.json())
        logger.info("Created note %s on item %s", key, parent_key)
        return {
            "key": key,
            "parent_key": parent_key,
            "note": "Note created on Zotero web. Sync Zotero desktop to see it locally.",
        }

    def batch_organize(
        self,
        item_keys: list[str],
        tags: list[str] | None = None,
        collection_key: str | None = None,
    ) -> dict:
        """Add tags and/or collection to multiple items in one operation.

        Reads each item locally, merges tags/collection, PATCHes via Web API.
        Uses parallel fetching for performance.

        Args:
            item_keys: List of Zotero item keys to organize.
            tags: Tags to add to all items (merged with existing tags).
            collection_key: Optional collection to add all items to.

        Returns:
            Dict with updated count, failed keys, and skipped keys.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: dict = {"updated": [], "failed": [], "skipped": []}

        # Parallel read from local API
        items: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(self._read_item, key): key for key in item_keys}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    items[key] = future.result()
                except Exception:
                    results["failed"].append(key)

        # Apply tags and collection to each item
        for key, item in items.items():
            version = item.get("version", 0)
            patch: dict = {}

            if tags:
                existing_tags = item.get("tags", [])
                existing_tag_names = {t.get("tag", "") for t in existing_tags}
                new_tags = [{"tag": t} for t in tags if t not in existing_tag_names]
                if new_tags:
                    patch["tags"] = existing_tags + new_tags

            if collection_key:
                existing_collections = item.get("collections", [])
                if collection_key not in existing_collections:
                    patch["collections"] = existing_collections + [collection_key]

            if not patch:
                results["skipped"].append(key)
                continue

            try:
                resp = self._web_client.patch(
                    f"/items/{key}",
                    headers={"If-Unmodified-Since-Version": str(version)},
                    json=patch,
                )
                if resp.status_code == 412:
                    # Version conflict — re-read and retry once
                    item = self._read_item(key)
                    version = item.get("version", 0)
                    new_patch: dict = {}
                    if tags:
                        existing_tags = item.get("tags", [])
                        existing_tag_names = {t.get("tag", "") for t in existing_tags}
                        new_tags = [
                            {"tag": t} for t in tags if t not in existing_tag_names
                        ]
                        if new_tags:
                            new_patch["tags"] = existing_tags + new_tags
                    if collection_key:
                        existing_collections = item.get("collections", [])
                        if collection_key not in existing_collections:
                            new_patch["collections"] = existing_collections + [
                                collection_key
                            ]
                    if new_patch:
                        resp = self._web_client.patch(
                            f"/items/{key}",
                            headers={"If-Unmodified-Since-Version": str(version)},
                            json=new_patch,
                        )
                        resp.raise_for_status()
                    results["updated"].append(key)
                elif resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    time.sleep(min(retry_after, 10))
                    resp = self._web_client.patch(
                        f"/items/{key}",
                        headers={"If-Unmodified-Since-Version": str(version)},
                        json=patch,
                    )
                    resp.raise_for_status()
                    results["updated"].append(key)
                else:
                    resp.raise_for_status()
                    results["updated"].append(key)
            except Exception:
                results["failed"].append(key)

        return {
            "updated_count": len(results["updated"]),
            "failed_count": len(results["failed"]),
            "skipped_count": len(results["skipped"]),
            "updated_keys": results["updated"],
            "failed_keys": results["failed"],
        }

    def create_collection(self, name: str, parent_key: str | None = None) -> dict:
        """Create a new collection (folder) in Zotero.

        Args:
            name: Collection name.
            parent_key: Optional parent collection key for nesting.

        Returns:
            Dict with "key", "name", and "parent_key".
        """
        payload = [{"name": name, "parentCollection": parent_key or False}]
        resp = _retry_request(
            lambda: self._web_client.post("/collections", json=payload)
        )
        resp.raise_for_status()

        key = self._extract_created_key(resp.json())
        return {
            "key": key,
            "name": name,
            "parent_key": parent_key or "",
            "note": "Collection created on Zotero web. Sync Zotero desktop to see it locally.",
        }

    def add_to_collection(self, item_key: str, collection_key: str) -> dict:
        """Add an existing item to a collection.

        Reads item from local API, appends collection, PATCHes via Web API.

        Args:
            item_key: Zotero item key.
            collection_key: Collection key to add the item to.

        Returns:
            Dict with item_key and updated collections list.
        """
        item = self._read_item(item_key)
        version = item.get("version", 0)
        collections = list(set(item.get("collections", []) + [collection_key]))

        resp = _retry_request(
            lambda: self._web_client.patch(
                f"/items/{item_key}",
                headers={"If-Unmodified-Since-Version": str(version)},
                json={"collections": collections},
            )
        )
        resp.raise_for_status()

        return {"item_key": item_key, "collections": collections}

    def update_item(self, item_key: str, fields: dict) -> dict:
        """Update metadata fields on an existing item.

        Uses read-modify-write with version for optimistic locking.

        Args:
            item_key: Zotero item key.
            fields: Dict of field names to new values.

        Returns:
            Dict with key and version.

        Raises:
            RuntimeError: On 412 version conflict.
        """
        item = self._read_item(item_key)
        version = item.get("version", 0)

        resp = _retry_request(
            lambda: self._web_client.patch(
                f"/items/{item_key}",
                headers={"If-Unmodified-Since-Version": str(version)},
                json=fields,
            )
        )

        if resp.status_code == 412:
            raise RuntimeError(
                f"Version conflict for item {item_key}. "
                "The item was modified since it was read. Please retry."
            )
        resp.raise_for_status()

        new_version = int(resp.headers.get("Last-Modified-Version", version))
        logger.info("Updated item %s to version %d", item_key, new_version)
        return {"key": item_key, "version": new_version}

    def trash_items(self, item_keys: list[str]) -> dict:
        """Move items to Zotero trash (reversible).

        Uses DELETE /items?itemKey=KEY1,KEY2 with version header.
        Chunks into batches of 50 (Zotero API limit).

        Args:
            item_keys: List of Zotero item keys to trash.

        Returns:
            Dict with trashed and failed key lists.
        """
        trashed: list[str] = []
        failed: list[str] = []

        # Get current library version
        resp = self._web_client.get("/items", params={"limit": 0})
        version = resp.headers.get("Last-Modified-Version", "0")

        # Chunk into batches of 50
        for i in range(0, len(item_keys), 50):
            batch = item_keys[i : i + 50]
            key_param = ",".join(k.strip() for k in batch)
            try:
                resp = self._web_client.delete(
                    "/items",
                    params={"itemKey": key_param},
                    headers={"If-Unmodified-Since-Version": str(version)},
                )
                resp.raise_for_status()
                version = resp.headers.get("Last-Modified-Version", version)
                trashed.extend(batch)
            except Exception:
                failed.extend(batch)

        return {"trashed": trashed, "failed": failed}

    def empty_trash(self) -> dict:
        """Permanently delete all items in Zotero trash.

        This is irreversible. The calling tool should confirm with the user
        before invoking this method.

        Returns:
            Dict with status.
        """
        # Get current library version
        resp = self._web_client.get("/items", params={"limit": 0})
        version = resp.headers.get("Last-Modified-Version", "0")

        resp = self._web_client.delete(
            "/items/trash",
            headers={"If-Unmodified-Since-Version": str(version)},
        )
        resp.raise_for_status()
        return {"status": "emptied"}

    def get_all_items_with_dois(self) -> list[dict]:
        """Fetch all library items that have a DOI, paginating through results.

        Uses the Zotero API's ``start`` and ``limit`` params with ``Total-Results``
        header for pagination. Excludes attachments and notes.

        Returns:
            List of dicts with keys: key, DOI, title (from _format_summary).
        """
        from zotero_mcp.local_client import _format_summary

        results: list[dict] = []
        start = 0
        page_size = 100  # Zotero API max per page
        total = None

        while True:
            params = {
                "limit": page_size,
                "start": start,
                "itemType": "-attachment || -note",
                "format": "json",
            }
            resp = self._web_client.get(
                "/items/top",
                params=params,
                timeout=SEARCH_TIMEOUT,
            )
            resp.raise_for_status()

            if total is None:
                total = int(resp.headers.get("Total-Results", "0"))

            for item in resp.json():
                summary = _format_summary(item)
                if summary.get("DOI"):
                    results.append(summary)

            start += page_size
            if start >= total:
                break

        return results

    def get_tags(self, prefix: str = "") -> list[str]:
        """Return all tags in the library, optionally filtered by prefix.

        Args:
            prefix: Only return tags starting with this string (case-sensitive).

        Returns:
            Sorted list of tag strings.
        """
        params: dict = {"limit": 100}
        if prefix:
            params["q"] = prefix
        tags: list[str] = []
        start = 0
        while True:
            params["start"] = start
            resp = self._web_client.get("/tags", params=params)
            resp.raise_for_status()
            page = resp.json()
            if not page:
                break
            tags.extend(t["tag"] for t in page)
            if len(page) < 100:
                break
            start += 100
        return sorted(tags)

    def remove_tag(self, tag: str) -> dict:
        """Remove a tag from every item in the library.

        Args:
            tag: Exact tag name to delete.

        Returns:
            Dict with "tag" and "status".
        """
        # Get current library version for the If-Unmodified-Since-Version header
        resp = self._web_client.get("/items", params={"limit": 0})
        version = resp.headers.get("Last-Modified-Version", "0")

        resp = self._web_client.delete(
            f"/tags/{tag}",
            headers={"If-Unmodified-Since-Version": str(version)},
        )
        resp.raise_for_status()
        logger.info("Removed tag %r from library", tag)
        return {"tag": tag, "status": "removed"}

    def rename_tag(self, old_tag: str, new_tag: str) -> dict:
        """Rename a tag across all items in the library.

        Fetches every item carrying old_tag, replaces the tag name in-place,
        and PATCHes each item. Uses parallel requests for speed.

        Args:
            old_tag: Current tag name.
            new_tag: Replacement tag name.

        Returns:
            Dict with updated count and failed keys.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Collect all items with this tag
        items: list[dict] = []
        start = 0
        while True:
            resp = self._web_client.get(
                "/items", params={"tag": old_tag, "limit": 100, "start": start}
            )
            resp.raise_for_status()
            page = resp.json()
            if not page:
                break
            items.extend(page)
            if len(page) < 100:
                break
            start += 100

        updated: list[str] = []
        failed: list[str] = []

        def _patch_item(item: dict) -> tuple[str, bool]:
            key = item["data"]["key"]
            version = item["data"].get("version", 0)
            tags = item["data"].get("tags", [])
            new_tags = [{"tag": new_tag} if t["tag"] == old_tag else t for t in tags]
            patch_resp = self._web_client.patch(
                f"/items/{key}",
                json={"tags": new_tags},
                headers={"If-Unmodified-Since-Version": str(version)},
            )
            return key, patch_resp.status_code in (200, 204)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_patch_item, item): item for item in items}
            for fut in as_completed(futures):
                key, ok = fut.result()
                (updated if ok else failed).append(key)

        logger.info(
            "Renamed tag %r → %r: %d updated, %d failed",
            old_tag,
            new_tag,
            len(updated),
            len(failed),
        )
        return {
            "old_tag": old_tag,
            "new_tag": new_tag,
            "updated": len(updated),
            "failed": failed,
        }

    def attach_pdf(
        self,
        parent_key: str,
        pdf_path: str | None = None,
        doi: str | None = None,
    ) -> dict:
        """Attach a PDF to an existing Zotero item.

        If pdf_path is provided, uploads that file. Otherwise, tries to find
        a free PDF online using the DOI (via Unpaywall, PMC, bioRxiv).

        Args:
            parent_key: Zotero item key to attach the PDF to.
            pdf_path: Local file path to a PDF. If None, tries auto-download.
            doi: DOI to search for a free PDF. If None and no pdf_path,
                 reads DOI from the parent item.

        Returns:
            Dict with attachment key, filename, and source.

        Raises:
            RuntimeError: If no PDF found and no path provided.
        """
        pdf_bytes: bytes | None = None
        filename: str = ""
        source: str = ""

        if pdf_path:
            # User-provided PDF
            path = Path(pdf_path)
            if not path.exists():
                raise RuntimeError(f"PDF file not found: {pdf_path}")
            pdf_bytes = path.read_bytes()
            filename = path.name
            source = "local_file"
        else:
            # Try to find a free PDF online
            if not doi and self._local:
                try:
                    item = self._local.get_item(parent_key)
                    if isinstance(item, dict):
                        doi = item.get("DOI", "")
                except Exception as exc:
                    logger.warning("Local DOI read failed for %s: %s", parent_key, exc)

            if doi:
                pdf_bytes, filename, source = self._download_free_pdf(doi)

        if not pdf_bytes:
            doi_url = f"https://doi.org/{doi}" if doi else ""
            return {
                "status": "not_found",
                "parent_key": parent_key,
                "doi_url": doi_url,
                "message": (
                    "No free PDF found. Try navigating to the paper in the "
                    "user's browser (they may have institutional access), "
                    "find the PDF download link, and ask the user to approve "
                    "the download. Then call attach_pdf again with the "
                    "downloaded file path. If no browser tools are available, "
                    "ask the user to provide the PDF file path."
                ),
            }

        # Step 1: Create attachment item
        attachment_data = [
            {
                "itemType": "attachment",
                "parentItem": parent_key,
                "linkMode": "imported_file",
                "title": filename,
                "contentType": "application/pdf",
                "filename": filename,
            }
        ]
        resp = self._web_client.post("/items", json=attachment_data)
        resp.raise_for_status()
        attach_key = self._extract_created_key(resp.json())

        try:
            # Step 2: Get upload authorization
            md5_hash = hashlib.md5(pdf_bytes).hexdigest()
            file_size = len(pdf_bytes)

            auth_resp = self._web_client.post(
                f"/items/{attach_key}/file",
                headers={
                    "If-None-Match": "*",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                content=urlencode(
                    {
                        "md5": md5_hash,
                        "filename": filename,
                        "filesize": file_size,
                        "mtime": int(time.time() * 1000),
                    }
                ),
            )
            auth_resp.raise_for_status()
            auth_data = auth_resp.json()

            if auth_data.get("exists"):
                return {
                    "status": "exists",
                    "attachment_key": attach_key,
                    "filename": filename,
                    "source": source,
                    "message": "File already exists in Zotero storage.",
                }

            # Step 3: Upload to Zotero storage
            upload_url = auth_data["url"]
            upload_prefix = auth_data.get("prefix", b"")
            upload_suffix = auth_data.get("suffix", b"")
            upload_content_type = auth_data.get("contentType", "application/pdf")

            if isinstance(upload_prefix, str):
                upload_prefix = upload_prefix.encode()
            if isinstance(upload_suffix, str):
                upload_suffix = upload_suffix.encode()

            upload_body = upload_prefix + pdf_bytes + upload_suffix

            upload_resp = httpx.post(
                upload_url,
                content=upload_body,
                headers={"Content-Type": upload_content_type},
                timeout=60.0,
            )
            upload_resp.raise_for_status()

            # Step 4: Register upload
            register_resp = self._web_client.post(
                f"/items/{attach_key}/file",
                headers={
                    "If-None-Match": "*",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                content=urlencode({"upload": auth_data["uploadKey"]}),
            )
            register_resp.raise_for_status()

        except Exception:
            # Clean up orphaned attachment item so it doesn't pollute the library
            try:
                ver_resp = self._web_client.get("/items", params={"limit": 0})
                version = ver_resp.headers.get("Last-Modified-Version", "0")
                self._web_client.delete(
                    "/items",
                    params={"itemKey": attach_key},
                    headers={"If-Unmodified-Since-Version": version},
                )
                logger.warning(
                    "Cleaned up orphan attachment %s after upload failure", attach_key
                )
            except Exception as cleanup_exc:
                logger.warning(
                    "Failed to clean up orphan attachment %s: %s",
                    attach_key,
                    cleanup_exc,
                )
            raise

        logger.info("Attached PDF %s to item %s (%s)", filename, parent_key, source)
        return {
            "status": "attached",
            "attachment_key": attach_key,
            "parent_key": parent_key,
            "filename": filename,
            "source": source,
            "size_bytes": file_size,
        }

    def _download_free_pdf(self, doi: str) -> tuple[bytes | None, str, str]:
        """Try to find and download a free PDF for a DOI.

        Tries in order: Unpaywall, PubMed Central, bioRxiv/medRxiv.

        Returns:
            Tuple of (pdf_bytes, filename, source) or (None, "", "") if not found.
        """
        doi = doi.strip()
        if not doi:
            return None, "", ""

        safe_doi = doi.replace("/", "_").replace(".", "_")

        # 1. Try Unpaywall (finds free legal PDFs for any DOI)
        try:
            resp = httpx.get(
                f"https://api.unpaywall.org/v2/{doi}",
                params={"email": _get_polite_email()},
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                best_oa = data.get("best_oa_location", {})
                pdf_url = best_oa.get("url_for_pdf") if best_oa else None
                if pdf_url:
                    _validate_url(pdf_url)
                    pdf_resp = httpx.get(pdf_url, timeout=30.0, follow_redirects=True)
                    if pdf_resp.status_code == 200 and _is_valid_pdf(pdf_resp.content):
                        return pdf_resp.content, f"{safe_doi}.pdf", "unpaywall"
        except Exception as exc:
            logger.warning("Unpaywall PDF download failed for %s: %s", doi, exc)

        # 2. Try PubMed Central
        if not _is_preprint_doi(doi):  # Skip bioRxiv/medRxiv DOIs for PMC
            try:
                id_resp = self._pubmed_client.get(
                    "/esearch.fcgi",
                    params={"db": "pmc", "term": f"{doi}[doi]", "retmode": "json"},
                )
                if id_resp.status_code == 200:
                    ids = id_resp.json().get("esearchresult", {}).get("idlist", [])
                    if ids:
                        pmc_id = ids[0]
                        pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc_id}/pdf/"
                        pdf_resp = httpx.get(
                            pdf_url, timeout=30.0, follow_redirects=True
                        )
                        if pdf_resp.status_code == 200 and _is_valid_pdf(
                            pdf_resp.content
                        ):
                            return pdf_resp.content, f"PMC{pmc_id}.pdf", "pmc"
            except Exception as exc:
                logger.warning("PMC PDF download failed for %s: %s", doi, exc)

        # 3. Try bioRxiv/medRxiv
        if _is_preprint_doi(doi):
            for server, host in (
                ("biorxiv", "www.biorxiv.org"),
                ("medrxiv", "www.medrxiv.org"),
            ):
                try:
                    # Detect the latest version via the bioRxiv/medRxiv API
                    api_resp = httpx.get(
                        f"https://api.biorxiv.org/details/{server}/{doi}",
                        timeout=10.0,
                    )
                    version = 1
                    if api_resp.status_code == 200:
                        collection = api_resp.json().get("collection", [])
                        if collection:
                            try:
                                version = int(collection[-1].get("version", 1))
                            except (ValueError, TypeError):
                                version = 1
                    pdf_url = f"https://{host}/content/{doi}v{version}.full.pdf"
                    pdf_resp = httpx.get(pdf_url, timeout=30.0, follow_redirects=True)
                    if pdf_resp.status_code == 200 and _is_valid_pdf(pdf_resp.content):
                        return pdf_resp.content, f"{safe_doi}.pdf", server
                except Exception as exc:
                    logger.warning(
                        "%s PDF download failed for %s: %s", server, doi, exc
                    )

        return None, "", ""
