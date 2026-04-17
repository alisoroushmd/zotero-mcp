"""Centralized configuration for Zotero MCP server.

Reads all environment variables once and exposes them as typed attributes.
Import this module instead of calling os.environ.get() in individual modules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Immutable server configuration read from environment variables."""

    # Required for Web API (primary path)
    zotero_api_key: str = ""
    zotero_user_id: str = ""

    # Required for knowledge graph, citation graph, retraction checks
    openalex_api_key: str = ""

    # Optional: improves rate limits for find_related_papers
    semantic_scholar_api_key: str = ""

    # Optional: polite email for external APIs (CrossRef, OpenAlex)
    polite_email: str = "zotero-mcp@example.com"

    # Optional: override graph database path
    graph_db_path: str = ""

    # Optional: Zotero desktop data directory (default: ~/Zotero)
    zotero_data_dir: str = ""

    # Derived: XDG data directory for graph store default
    xdg_data_home: str = ""

    @property
    def has_web_api(self) -> bool:
        """True if Zotero Web API credentials are configured."""
        return bool(self.zotero_api_key and self.zotero_user_id)

    @property
    def missing_web_vars(self) -> list[str]:
        """List of missing required Web API environment variables."""
        missing = []
        if not self.zotero_api_key:
            missing.append("ZOTERO_API_KEY")
        if not self.zotero_user_id:
            missing.append("ZOTERO_USER_ID")
        return missing

    @property
    def has_openalex(self) -> bool:
        """True if OpenAlex API key is configured."""
        return bool(self.openalex_api_key)

    @property
    def default_graph_db_path(self) -> str:
        """Default path for the graph SQLite database."""
        data_home = self.xdg_data_home or os.path.expanduser("~/.local/share")
        return os.path.join(data_home, "zotero-mcp", "graph.sqlite")

    @property
    def effective_graph_db_path(self) -> str:
        """Resolved graph database path (explicit override or default)."""
        return self.graph_db_path or self.default_graph_db_path

    @property
    def effective_zotero_data_dir(self) -> str:
        """Resolved Zotero desktop data directory (explicit override or default)."""
        return self.zotero_data_dir or os.path.expanduser("~/Zotero")


def load_config() -> Config:
    """Load configuration from environment variables."""
    return Config(
        zotero_api_key=os.environ.get("ZOTERO_API_KEY", ""),
        zotero_user_id=os.environ.get("ZOTERO_USER_ID", ""),
        openalex_api_key=os.environ.get("OPENALEX_API_KEY", ""),
        semantic_scholar_api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY", ""),
        polite_email=os.environ.get("ZOTERO_MCP_EMAIL", "zotero-mcp@example.com"),
        graph_db_path=os.environ.get("ZOTERO_MCP_GRAPH_DB", ""),
        zotero_data_dir=os.environ.get("ZOTERO_DATA_DIR", ""),
        xdg_data_home=os.environ.get("XDG_DATA_HOME", ""),
    )


# Module-level singleton — imported by other modules.
_config: Config | None = None


def get_config() -> Config:
    """Return the singleton config, loading from env on first call."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _reset_config() -> None:
    """Reset config singleton (for testing)."""
    global _config
    _config = None
