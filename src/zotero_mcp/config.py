"""Centralized configuration for Zotero MCP server.

Reads all environment variables once and exposes them as typed attributes.
Import this module instead of calling os.environ.get() in individual modules.
"""

from __future__ import annotations

import os
import sys
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

    # Optional: NCBI eutils API key — raises the rate limit from 3 to 10 req/s
    # and is passed on all PubMed efetch/esearch/elink calls when set (ZOT-28).
    ncbi_api_key: str = ""

    # Optional: polite email for external APIs (CrossRef, OpenAlex)
    polite_email: str = "zotero-mcp@example.com"

    # Optional: override graph database path
    graph_db_path: str = ""

    # Optional: Zotero desktop data directory (default: ~/Zotero)
    zotero_data_dir: str = ""

    # Optional: where attach_pdf writes PDFs for linked-file attachments
    # (default: <zotero_data_dir>/linked-attachments)
    linked_attachment_dir: str = ""

    # How attach_pdf stores files. "linked" (default) writes the PDF to
    # linked_attachment_dir and creates a linked_file attachment: the file
    # stays on local disk and consumes NO Zotero cloud storage quota.
    # "imported" restores the legacy behavior of uploading into Zotero
    # storage, which syncs across devices but counts against the quota and
    # fails with HTTP 413 once it is full.
    attachment_mode: str = "linked"

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
        """Default path for the graph SQLite database (OS-native, ZOT-31).

        Honors ``XDG_DATA_HOME`` if set. Otherwise uses the platform's
        conventional per-user data directory: ``~/Library/Application Support``
        on macOS, ``%LOCALAPPDATA%`` on Windows, ``~/.local/share`` elsewhere.
        Avoids a hard dependency on ``platformdirs`` while still being correct
        on each platform.
        """
        # Backward compatibility: if a DB already exists at the historical XDG
        # location, keep using it so existing users don't silently lose their
        # built graph when upgrading.
        legacy = os.path.join(os.path.expanduser("~/.local/share"), "zotero-mcp", "graph.sqlite")
        if not self.xdg_data_home and os.path.exists(legacy):
            return legacy

        if self.xdg_data_home:
            data_home = self.xdg_data_home
        elif sys.platform == "darwin":
            data_home = os.path.expanduser("~/Library/Application Support")
        elif sys.platform == "win32":
            data_home = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        else:
            data_home = os.path.expanduser("~/.local/share")
        return os.path.join(data_home, "zotero-mcp", "graph.sqlite")

    @property
    def effective_graph_db_path(self) -> str:
        """Resolved graph database path (explicit override or default)."""
        return self.graph_db_path or self.default_graph_db_path

    @property
    def effective_zotero_data_dir(self) -> str:
        """Resolved Zotero desktop data directory (explicit override or default)."""
        return self.zotero_data_dir or os.path.expanduser("~/Zotero")

    @property
    def effective_linked_attachment_dir(self) -> str:
        """Resolved directory for linked-file PDF attachments.

        Defaults to ``<zotero_data_dir>/linked-attachments``. Kept out of
        Zotero's own ``storage/`` tree, which Zotero manages and prunes for
        imported files only.
        """
        explicit = os.path.expanduser(self.linked_attachment_dir)
        if explicit:
            return explicit
        return os.path.join(self.effective_zotero_data_dir, "linked-attachments")

    @property
    def uses_linked_attachments(self) -> bool:
        """True when attach_pdf should create local linked_file attachments.

        Default. Set ``ZOTERO_ATTACHMENT_MODE=imported`` to upload into
        Zotero cloud storage instead (legacy behavior, consumes quota).
        """
        return self.attachment_mode.strip().lower() != "imported"


def load_config() -> Config:
    """Load configuration from environment variables."""
    return Config(
        zotero_api_key=os.environ.get("ZOTERO_API_KEY", ""),
        zotero_user_id=os.environ.get("ZOTERO_USER_ID", ""),
        openalex_api_key=os.environ.get("OPENALEX_API_KEY", ""),
        semantic_scholar_api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY", ""),
        ncbi_api_key=os.environ.get("NCBI_API_KEY", ""),
        polite_email=os.environ.get("ZOTERO_MCP_EMAIL", "zotero-mcp@example.com"),
        graph_db_path=os.environ.get("ZOTERO_MCP_GRAPH_DB", ""),
        zotero_data_dir=os.environ.get("ZOTERO_DATA_DIR", ""),
        linked_attachment_dir=os.environ.get("ZOTERO_LINKED_ATTACHMENT_DIR", ""),
        attachment_mode=os.environ.get("ZOTERO_ATTACHMENT_MODE", "linked"),
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
