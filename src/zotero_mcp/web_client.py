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
        resp = httpx.post(
            f"{self._base}/items",
            headers=self._headers(),
            json=[metadata],
            timeout=TIMEOUT,
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
            return {"key": key, "title": metadata.get("title", "")}

        raise RuntimeError(f"Failed to create item: {result.get('failed', result)}")

    def _resolve_identifier(self, identifier: str) -> dict:
        """Resolve PMID/DOI/URL via Zotero translation server."""
        try:
            resp = httpx.post(
                TRANSLATE_URL,
                content=identifier,
                headers={"Content-Type": "text/plain"},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            items = resp.json()
            if items and len(items) > 0:
                return items[0]
        except httpx.ConnectError:
            raise RuntimeError(
                "Translation server unavailable. Item cannot be created "
                "at this time. Add the item manually in Zotero using the "
                "browser connector."
            )
        except Exception as e:
            raise RuntimeError(f"Failed to resolve identifier '{identifier}': {e}")

        raise RuntimeError(f"No metadata found for identifier '{identifier}'")

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

        resp = httpx.patch(
            f"{self._base}/items/{item_key}",
            headers={
                **self._headers(),
                "If-Unmodified-Since-Version": str(version),
            },
            json={"collections": collections},
            timeout=TIMEOUT,
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

        resp = httpx.patch(
            f"{self._base}/items/{item_key}",
            headers={
                **self._headers(),
                "If-Unmodified-Since-Version": str(version),
            },
            json=fields,
            timeout=TIMEOUT,
        )

        if resp.status_code == 412:
            raise RuntimeError(
                f"Version conflict for item {item_key}. "
                "The item was modified since it was read. Please retry."
            )
        resp.raise_for_status()

        return {"key": item_key, "version": version}
