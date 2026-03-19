"""Reads from Zotero local API at localhost:23119."""

import logging

import httpx

logger = logging.getLogger(__name__)

LOCAL_BASE = "http://localhost:23119/api"
TIMEOUT = 2.0


class LocalClient:
    """Read-only client for Zotero's local HTTP API."""

    def __init__(self, base_url: str = LOCAL_BASE) -> None:
        self._base = base_url

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        """GET request to local API with connection error handling."""
        try:
            resp = httpx.get(
                f"{self._base}{path}",
                params=params,
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            return resp
        except httpx.ConnectError:
            raise RuntimeError(
                "Zotero desktop must be running for read operations. "
                "Enable 'Allow other applications on this computer to "
                "communicate with Zotero' in Zotero settings > Advanced."
            )

    def search_items(self, query: str, limit: int = 25) -> list[dict]:
        """Keyword search across the library. Excludes attachments and notes."""
        resp = self._get(
            "/users/0/items",
            params={
                "q": query,
                "limit": limit,
                "itemType": "-attachment || note",
            },
        )
        return [_format_summary(item) for item in resp.json()]


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
