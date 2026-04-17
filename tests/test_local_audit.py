"""Tests for the local-key audit module."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from zotero_mcp.local_audit import (
    INVALID_KEY_CHARS,
    _bad_chars,
    audit_local_keys,
    audit_summary,
)


def test_invalid_chars_are_zero_one_o_only():
    """Zotero's key alphabet excludes 0, 1, and O. 'I' and 'L' are valid."""
    assert frozenset("01O") == INVALID_KEY_CHARS
    assert "I" not in INVALID_KEY_CHARS
    assert "L" not in INVALID_KEY_CHARS


@pytest.mark.parametrize(
    "key,expected",
    [
        ("ABCDEFGH", ""),  # clean
        ("ABCD0EFG", "0"),  # contains zero
        ("ABCD1EFG", "1"),  # contains one
        ("ABCDOEFG", "O"),  # contains capital O
        ("0O1ABCDE", "0,1,O"),  # all three
        ("ABCIDEFG", ""),  # 'I' is valid
        ("ABCLDEFG", ""),  # 'L' is valid
    ],
)
def test_bad_chars_detects_forbidden_only(key, expected):
    assert _bad_chars(key) == expected


@pytest.fixture
def fake_zotero_db(tmp_path: Path) -> Path:
    """Build a minimal zotero.sqlite with a mix of valid and invalid keys."""
    data_dir = tmp_path / "Zotero"
    data_dir.mkdir()
    db = data_dir / "zotero.sqlite"

    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE collections (
            collectionID INTEGER PRIMARY KEY,
            collectionName TEXT NOT NULL,
            key TEXT NOT NULL,
            synced INT NOT NULL DEFAULT 0,
            version INT NOT NULL DEFAULT 0
        );
        CREATE TABLE items (
            itemID INTEGER PRIMARY KEY,
            key TEXT NOT NULL,
            synced INT NOT NULL DEFAULT 0,
            version INT NOT NULL DEFAULT 0
        );
        CREATE TABLE itemData (
            itemID INT NOT NULL,
            fieldID INT NOT NULL,
            valueID INT NOT NULL
        );
        CREATE TABLE itemDataValues (
            valueID INTEGER PRIMARY KEY,
            value TEXT
        );
        INSERT INTO collections (collectionID, collectionName, key, synced, version) VALUES
            (1, 'Good_Collection',  'ABCDEFGH', 1, 5),
            (2, 'Has_I_valid',      'ABCDEFGI', 1, 5),
            (3, 'Has_zero',         'ABCDE0GH', 0, 0),
            (4, 'Has_oh',           'ABCDEOGH', 0, 0),
            (5, 'Has_one',          'ABCDE1GH', 0, 0);
        INSERT INTO items (itemID, key, synced, version) VALUES
            (1, 'JKLMNPQR', 1, 3),
            (2, 'JKLMN0QR', 0, 0),
            (3, 'JKLMNOQR', 0, 0);
        INSERT INTO itemDataValues (valueID, value) VALUES
            (1, 'Clean paper title'),
            (2, 'Dirty paper title');
        INSERT INTO itemData (itemID, fieldID, valueID) VALUES
            (1, 1, 1),
            (2, 1, 2);
        """
    )
    conn.commit()
    conn.close()
    return data_dir


def test_audit_finds_three_invalid_collections_and_two_invalid_items(fake_zotero_db):
    findings = audit_local_keys(data_dir=str(fake_zotero_db), include_items=True)
    by_type = {"collection": 0, "item": 0}
    for f in findings:
        by_type[f.object_type] += 1
    assert by_type == {"collection": 3, "item": 2}

    # Clean ones must NOT appear
    keys = {f.key for f in findings}
    assert "ABCDEFGH" not in keys
    assert "ABCDEFGI" not in keys, "'I' should be treated as valid"
    assert "JKLMNPQR" not in keys

    # Offenders must appear
    assert {"ABCDE0GH", "ABCDEOGH", "ABCDE1GH", "JKLMN0QR", "JKLMNOQR"} <= keys


def test_audit_can_skip_items(fake_zotero_db):
    findings = audit_local_keys(data_dir=str(fake_zotero_db), include_items=False)
    assert all(f.object_type == "collection" for f in findings)
    assert len(findings) == 3


def test_audit_summary_structure(fake_zotero_db):
    findings = audit_local_keys(data_dir=str(fake_zotero_db), include_items=True)
    summary = audit_summary(findings)
    assert summary["total_invalid"] == 5
    assert summary["collections"] == 3
    assert summary["items"] == 2
    assert len(summary["offenders"]) == 5
    assert "note" in summary
    # Each offender has the canonical fields
    fields = {"type", "key", "name", "synced", "version", "bad_chars"}
    for o in summary["offenders"]:
        assert fields <= set(o.keys())


def test_audit_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        audit_local_keys(data_dir=str(tmp_path / "nonexistent"))


def test_audit_clean_library_returns_empty(tmp_path):
    """A library with only valid keys should return zero findings."""
    data_dir = tmp_path / "Zotero"
    data_dir.mkdir()
    db = data_dir / "zotero.sqlite"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE collections (
            collectionID INTEGER PRIMARY KEY,
            collectionName TEXT NOT NULL,
            key TEXT NOT NULL,
            synced INT NOT NULL DEFAULT 0,
            version INT NOT NULL DEFAULT 0
        );
        CREATE TABLE items (
            itemID INTEGER PRIMARY KEY,
            key TEXT NOT NULL,
            synced INT NOT NULL DEFAULT 0,
            version INT NOT NULL DEFAULT 0
        );
        CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT);
        CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
        INSERT INTO collections (collectionID, collectionName, key, synced, version)
            VALUES (1, 'Clean', 'ABCDEFGH', 1, 1);
        INSERT INTO items (itemID, key, synced, version) VALUES (1, 'JKLMNPQR', 1, 1);
        """
    )
    conn.commit()
    conn.close()
    assert audit_local_keys(data_dir=str(data_dir), include_items=True) == []
