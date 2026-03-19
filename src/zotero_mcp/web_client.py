"""Writes to Zotero Web API + translation server for identifier resolution."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from zotero_mcp.local_client import LocalClient

logger = logging.getLogger(__name__)

WEB_BASE = "https://api.zotero.org"
TRANSLATE_URL = "https://translate.zotero.org/search"
TIMEOUT = 10.0


class WebClient:
    """Write client for Zotero Web API."""

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

    def _headers(self) -> dict[str, str]:
        return {
            "Zotero-API-Key": self._api_key,
            "Content-Type": "application/json",
        }

    def _read_item_local(self, item_key: str) -> dict:
        """Read item from local API for read-modify-write operations."""
        if self._local:
            return self._local.get_item(item_key)
        try:
            resp = httpx.get(
                f"http://localhost:23119/api/users/0/items/{item_key}",
                timeout=2.0,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            raise RuntimeError(
                "Zotero desktop must be running to read item data before updating."
            )

    def _check_duplicate_doi(self, doi: str) -> dict | None:
        """Check if a DOI already exists in the library. Returns item summary or None."""
        if not doi or not self._local:
            return None
        try:
            results = self._local.search_items(doi, limit=10)
            for item in results:
                if item.get("DOI", "").strip().lower() == doi.strip().lower():
                    return item
        except RuntimeError:
            pass  # Zotero not running — skip duplicate check
        return None

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

        # Apply optional collections and tags
        if collection_keys:
            metadata["collections"] = collection_keys
        if tags:
            existing_tags = metadata.get("tags", [])
            for t in tags:
                existing_tags.append({"tag": t})
            metadata["tags"] = existing_tags

        # Create item via Web API
        resp = self._web_client.post(
            "/items",
            json=[metadata],
        )
        resp.raise_for_status()

        result = resp.json()
        successful = result.get("successful", result.get("success", {}))
        if successful:
            val = list(successful.values())[0]
            if isinstance(val, dict):
                key = val.get("key", val.get("data", {}).get("key", ""))
            else:
                key = str(val)
            return {
                "key": key,
                "title": metadata.get("title", ""),
                "DOI": metadata.get("DOI", ""),
                "date": metadata.get("date", ""),
                "note": "Item created on Zotero web. Sync Zotero desktop to see it locally.",
            }

        raise RuntimeError(f"Failed to create item: {result.get('failed', result)}")

    def _verify_item_web(self, item_key: str) -> dict:
        """Read back a newly created item from the Web API to confirm it exists."""
        try:
            resp = httpx.get(
                f"{self._base}/items/{item_key}",
                headers=self._headers(),
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            creators = data.get("creators", [])
            author_parts = []
            for c in creators[:3]:
                name = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
                if name:
                    author_parts.append(name)
            author_str = "; ".join(author_parts)
            if len(creators) > 3:
                author_str += " et al."
            return {
                "title": data.get("title", ""),
                "creators": author_str,
                "DOI": data.get("DOI", ""),
                "date": data.get("date", ""),
            }
        except Exception:
            return {}

    def _resolve_identifier(self, identifier: str) -> dict:
        """Resolve PMID/DOI/URL to Zotero item metadata.

        Tries the Zotero translation server first. If unavailable, falls back
        to PubMed E-utilities for PMIDs and DOIs.
        """
        # Try Zotero translation server first
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
            logger.info("Translation server unavailable, trying PubMed fallback")
        except Exception:
            logger.info("Translation server error, trying PubMed fallback")

        # Fallback: resolve via PubMed E-utilities
        metadata = self._resolve_via_pubmed(identifier)
        if metadata:
            return metadata

        raise RuntimeError(
            f"No metadata found for identifier '{identifier}'. "
            f"The Zotero translation server may be down. "
            f"Try adding the item manually in Zotero."
        )

    def _resolve_via_pubmed(self, identifier: str) -> dict | None:
        """Resolve PMID or DOI via PubMed E-utilities as fallback.

        Returns Zotero-compatible item metadata dict, or None if not found.
        """
        import re

        pmid = None
        identifier = identifier.strip()

        # Detect identifier type
        if re.match(r"^\d+$", identifier):
            pmid = identifier
        elif "pubmed" in identifier.lower() or "ncbi.nlm.nih.gov" in identifier.lower():
            # Extract PMID from PubMed URL
            match = re.search(r"(\d{6,})", identifier)
            if match:
                pmid = match.group(1)
        elif identifier.startswith("10.") or "doi.org" in identifier:
            # DOI — search PubMed by DOI
            doi = identifier.replace("https://doi.org/", "").replace(
                "http://doi.org/", ""
            )
            try:
                search_resp = httpx.get(
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                    params={"db": "pubmed", "term": f"{doi}[doi]", "retmode": "json"},
                    timeout=TIMEOUT,
                )
                search_resp.raise_for_status()
                ids = search_resp.json().get("esearchresult", {}).get("idlist", [])
                if ids:
                    pmid = ids[0]
            except Exception:
                return None

        if not pmid:
            return None

        # Fetch full metadata from PubMed
        try:
            resp = httpx.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                params={"db": "pubmed", "id": pmid, "retmode": "json"},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json().get("result", {}).get(pmid, {})
            if not data or "error" in data:
                return None

            # Build Zotero-compatible item
            creators = []
            for author in data.get("authors", []):
                name = author.get("name", "")
                if name:
                    parts = name.split(" ", 1)
                    if len(parts) == 2:
                        creators.append(
                            {
                                "creatorType": "author",
                                "lastName": parts[0],
                                "firstName": parts[1],
                            }
                        )
                    else:
                        creators.append(
                            {
                                "creatorType": "author",
                                "lastName": name,
                                "firstName": "",
                            }
                        )

            doi = ""
            for aid in data.get("articleids", []):
                if aid.get("idtype") == "doi":
                    doi = aid.get("value", "")
                    break

            return {
                "itemType": "journalArticle",
                "title": data.get("title", "").rstrip("."),
                "creators": creators,
                "date": data.get("pubdate", ""),
                "DOI": doi,
                "publicationTitle": data.get("fulljournalname", data.get("source", "")),
                "volume": data.get("volume", ""),
                "issue": data.get("issue", ""),
                "pages": data.get("pages", ""),
                "ISSN": data.get("issn", ""),
                "extra": f"PMID: {pmid}",
            }
        except Exception:
            return None

    def add_to_collection(self, item_key: str, collection_key: str) -> dict:
        """Add an existing item to a collection.

        Reads item from local API, appends collection, PATCHes via Web API.

        Args:
            item_key: Zotero item key.
            collection_key: Collection key to add the item to.

        Returns:
            Dict with item_key and updated collections list.
        """
        item = self._read_item_local(item_key)
        version = item.get("version", item.get("data", {}).get("version", 0))
        collections = list(
            set(item.get("data", {}).get("collections", []) + [collection_key])
        )

        resp = self._web_client.patch(
            f"/items/{item_key}",
            headers={"If-Unmodified-Since-Version": str(version)},
            json={"collections": collections},
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
        item = self._read_item_local(item_key)
        version = item.get("version", item.get("data", {}).get("version", 0))

        resp = self._web_client.patch(
            f"/items/{item_key}",
            headers={"If-Unmodified-Since-Version": str(version)},
            json=fields,
        )

        if resp.status_code == 412:
            raise RuntimeError(
                f"Version conflict for item {item_key}. "
                "The item was modified since it was read. Please retry."
            )
        resp.raise_for_status()

        return {"key": item_key, "version": version}
