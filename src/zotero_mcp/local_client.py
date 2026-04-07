"""Reads from Zotero local API at localhost:23119."""

import logging

import httpx

logger = logging.getLogger(__name__)

LOCAL_BASE = "http://localhost:23119/api"
TIMEOUT = 5.0


class LocalClient:
    """Read-only client for Zotero's local HTTP API."""

    def __init__(self, base_url: str = LOCAL_BASE, *, probe: bool = True) -> None:
        self._base = base_url
        self._client = httpx.Client(base_url=base_url, timeout=TIMEOUT)
        if probe:
            # Probe connectivity immediately so _get_local() fails fast
            # when Zotero desktop is not running (avoids 5s timeout per call)
            try:
                self._client.get("/users/0/items", params={"limit": 0})
            except httpx.ConnectError:
                raise RuntimeError(
                    "Local Read mode requires Zotero desktop running at localhost:23119. "
                    "Start Zotero and enable: Settings > Advanced > General > "
                    "'Allow other applications on this computer to communicate with Zotero'. "
                    "Call server_status to check which modes are available."
                )

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        """GET request to local API with connection error handling."""
        try:
            resp = self._client.get(path, params=params)
            resp.raise_for_status()
            return resp
        except httpx.ConnectError:
            raise RuntimeError(
                "Local Read mode requires Zotero desktop running at localhost:23119. "
                "Start Zotero and enable: Settings > Advanced > General > "
                "'Allow other applications on this computer to communicate with Zotero'. "
                "Call server_status to check which modes are available."
            )

    def search_items(
        self,
        query: str,
        limit: int = 25,
        item_type: str | None = None,
        tag: str | None = None,
    ) -> list[dict]:
        """Keyword search across the library. Excludes attachments and notes.

        Args:
            query: Keyword search string.
            limit: Max results (1–100).
            item_type: Filter by Zotero item type, e.g. "journalArticle".
            tag: Filter by tag name (exact match).

        Returns:
            List of item summary dicts.
        """
        params: dict = {
            "q": query,
            "limit": limit,
            "itemType": item_type if item_type else "-attachment || -note",
        }
        if tag:
            params["tag"] = tag
        resp = self._get("/users/0/items", params=params)
        return [_format_summary(item) for item in resp.json()]

    def get_item(self, item_key: str, fmt: str = "json") -> dict | str:
        """Get full metadata for a single item by its key.

        Args:
            item_key: Zotero item key.
            fmt: "json" for dict, "bibtex" for raw BibTeX string.
                 Named 'fmt' to avoid shadowing Python's built-in 'format'.

        Returns:
            Dict of item data, or raw BibTeX string.
        """
        params = {}
        if fmt == "bibtex":
            params["format"] = "bibtex"
        resp = self._get(f"/users/0/items/{item_key}", params=params)
        if fmt == "bibtex":
            return resp.text
        data = resp.json()
        return data.get("data", data)

    def get_collections(self) -> list[dict]:
        """List all collections with parent info and item counts."""
        resp = self._get("/users/0/collections")
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
        """Get items in a specific collection."""
        resp = self._get(
            f"/users/0/collections/{collection_key}/items",
            params={
                "limit": limit,
                "itemType": "-attachment || -note",
            },
        )
        return [_format_summary(item) for item in resp.json()]

    def get_children(self, parent_key: str, item_type: str | None = None) -> list[dict]:
        """Get child items for a parent item.

        Args:
            parent_key: Zotero item key of the parent item.
            item_type: Optional filter (e.g. "note", "attachment").

        Returns:
            List of raw data dicts for each child item.
        """
        params = {}
        if item_type:
            params["itemType"] = item_type
        resp = self._get(f"/users/0/items/{parent_key}/children", params=params or None)
        return [item.get("data", item) for item in resp.json()]

    def get_notes(self, parent_key: str) -> list[dict]:
        """Get child notes for a parent item.

        Args:
            parent_key: Zotero item key of the parent item.

        Returns:
            List of dicts with key, note (HTML content), tags, and dateModified.
        """
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

    def get_attachment_path(self, attachment_key: str) -> str | None:
        """Get local file path for an attachment.

        Args:
            attachment_key: Zotero key of the attachment item.

        Returns:
            Local file path string, or None if the attachment has no local file
            (e.g. linked_url attachments or imported files not yet synced locally).
        """
        resp = self._get(f"/users/0/items/{attachment_key}")
        data = resp.json().get("data", resp.json())
        link_mode = data.get("linkMode", "")
        if link_mode in ("imported_file", "imported_url", "linked_file"):
            return data.get("path", None)
        return None


def _format_summary(item: dict) -> dict:
    """Extract key fields from a Zotero item for display."""
    data = item.get("data", item)
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
        "key": data.get("key", ""),
        "title": data.get("title", ""),
        "creators": author_str,
        "date": data.get("date", ""),
        "item_type": data.get("itemType", ""),
        "DOI": data.get("DOI", ""),
        "collections": data.get("collections", []),
        "tags": [t["tag"] for t in data.get("tags", [])],
        "version": data.get("version", 0),
    }
