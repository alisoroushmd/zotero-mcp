"""Audit the local Zotero SQLite DB for invalid keys.

Zotero collection and item keys are 8-character strings drawn from the
33-char alphabet ``23456789ABCDEFGHIJKLMNPQRSTUVWXYZ``, which excludes the
visually ambiguous characters ``0``, ``1``, and ``O``. Keys containing any
of those characters are rejected by the Zotero sync server with
``ZoteroObjectUploadError: '<KEY>' is not a valid collection key``, which
halts sync with ``Made no progress during upload -- stopping``.

Invalid keys cannot be produced by the Zotero Web API (the server always
emits valid keys), but they can appear in the local database if an extension
or an ad-hoc script wrote directly to ``zotero.sqlite`` using naive key
generation (for example ``random.choices(string.ascii_uppercase + string.digits)``).

This module scans ``collections.key`` and ``items.key`` for offending
characters so the problem can be surfaced before the next sync attempt.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from zotero_mcp.config import get_config

# Zotero key alphabet: 23456789ABCDEFGHIJKLMNPQRSTUVWXYZ (33 chars).
# Excludes 0, 1, O to avoid visual ambiguity. Note: 'I' IS valid (unlike some
# base32 variants); only zero, one, and capital-O are forbidden.
INVALID_KEY_CHARS = frozenset("01O")


@dataclass(frozen=True)
class InvalidKeyRecord:
    """A single row flagged by the audit."""

    object_type: str  # "collection" or "item"
    key: str
    name: str  # collectionName or item title
    synced: int
    version: int
    bad_chars: str  # e.g. "0,O"


def _resolve_db_path(data_dir: str | None = None) -> str:
    """Return the absolute path to ``zotero.sqlite``.

    Args:
        data_dir: Override for the Zotero data directory. If ``None``, uses
            ``config.effective_zotero_data_dir`` (env ``ZOTERO_DATA_DIR`` or
            ``~/Zotero``).

    Raises:
        FileNotFoundError: If the resolved path does not exist.
    """
    base = data_dir or get_config().effective_zotero_data_dir
    db_path = os.path.join(base, "zotero.sqlite")
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Zotero database not found at {db_path}. "
            "Set ZOTERO_DATA_DIR env var to the directory containing zotero.sqlite."
        )
    return db_path


def _bad_chars(key: str) -> str:
    """Return the forbidden characters present in ``key`` (comma-separated)."""
    return ",".join(sorted({c for c in key if c in INVALID_KEY_CHARS}))


def audit_local_keys(
    data_dir: str | None = None,
    *,
    include_items: bool = True,
) -> list[InvalidKeyRecord]:
    """Scan the local Zotero SQLite DB for collection/item keys with invalid chars.

    Opens the DB read-only so this is safe to call while Zotero desktop is
    running (no write lock contention).

    Args:
        data_dir: Zotero data directory. Defaults to config/env/~/Zotero.
        include_items: Scan ``items.key`` in addition to ``collections.key``.
            Item scans can be slow on large libraries; set ``False`` to skip.

    Returns:
        List of :class:`InvalidKeyRecord`. Empty list if the library is clean.

    Raises:
        FileNotFoundError: If ``zotero.sqlite`` cannot be located.
        sqlite3.OperationalError: If the DB is locked or schema is unexpected.
    """
    db_path = _resolve_db_path(data_dir)
    # Open with immutable=1 so SQLite skips all locking — required when Zotero
    # desktop is running and holds the WAL lock. Safe for a point-in-time scan:
    # we never write, and the audit doesn't need the absolute latest state.
    conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        findings: list[InvalidKeyRecord] = []

        for row in conn.execute("SELECT key, collectionName, synced, version FROM collections"):
            key, name, synced, version = row
            bc = _bad_chars(key)
            if bc:
                findings.append(
                    InvalidKeyRecord(
                        object_type="collection",
                        key=key,
                        name=name or "",
                        synced=int(synced or 0),
                        version=int(version or 0),
                        bad_chars=bc,
                    )
                )

        if include_items:
            # items.itemID joins to itemDataValues via itemData for the title.
            # Title is fieldID 1 in Zotero's schema; we left-join so items
            # without a title (attachments, notes) still appear.
            query = """
                SELECT i.key,
                       COALESCE(idv.value, '') AS title,
                       i.synced,
                       i.version
                FROM items i
                LEFT JOIN itemData id
                    ON id.itemID = i.itemID AND id.fieldID = 1
                LEFT JOIN itemDataValues idv
                    ON idv.valueID = id.valueID
            """
            for row in conn.execute(query):
                key, title, synced, version = row
                bc = _bad_chars(key)
                if bc:
                    findings.append(
                        InvalidKeyRecord(
                            object_type="item",
                            key=key,
                            name=str(title)[:120],
                            synced=int(synced or 0),
                            version=int(version or 0),
                            bad_chars=bc,
                        )
                    )

        return findings
    finally:
        conn.close()


def audit_summary(findings: list[InvalidKeyRecord]) -> dict:
    """Produce a JSON-serializable summary of the audit results."""
    return {
        "total_invalid": len(findings),
        "collections": sum(1 for f in findings if f.object_type == "collection"),
        "items": sum(1 for f in findings if f.object_type == "item"),
        "offenders": [
            {
                "type": f.object_type,
                "key": f.key,
                "name": f.name,
                "synced": f.synced,
                "version": f.version,
                "bad_chars": f.bad_chars,
            }
            for f in findings
        ],
        "note": (
            "Keys containing 0, 1, or O are outside Zotero's 33-char key "
            "alphabet (23456789ABCDEFGHIJKLMNPQRSTUVWXYZ) and will be "
            "rejected by the sync server with 'not a valid collection key' "
            "or 'not a valid item key'. Fix by updating the key in the local "
            "SQLite to a valid 8-char string from the allowed alphabet. "
            "Unsynced records (synced=0, version=0) can be rekeyed safely; "
            "synced records should not appear here but if they do, contact "
            "Zotero support."
        ),
    }
