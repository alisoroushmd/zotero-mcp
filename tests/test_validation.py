"""Tests for input validation in server tool functions."""

import pytest

from zotero_mcp.server import _clamp_limit, _validate_key


def test_validate_key_rejects_empty():
    with pytest.raises(ValueError, match="must not be empty"):
        _validate_key("", "item_key")


def test_validate_key_rejects_whitespace():
    with pytest.raises(ValueError, match="must not be empty"):
        _validate_key("   ", "item_key")


def test_validate_key_rejects_special_chars():
    with pytest.raises(ValueError, match="must be alphanumeric"):
        _validate_key("ABC/123", "item_key")


def test_validate_key_rejects_spaces():
    with pytest.raises(ValueError, match="must be alphanumeric"):
        _validate_key("ABC 123", "item_key")


def test_validate_key_accepts_alphanumeric():
    _validate_key("ABC12345", "item_key")  # Should not raise


def test_validate_key_accepts_lowercase():
    _validate_key("abc12345", "item_key")  # Should not raise


def test_clamp_limit_normal():
    assert _clamp_limit(25) == 25


def test_clamp_limit_string():
    assert _clamp_limit("50") == 50


def test_clamp_limit_too_low():
    assert _clamp_limit(0) == 1


def test_clamp_limit_negative():
    assert _clamp_limit(-5) == 1


def test_clamp_limit_too_high():
    assert _clamp_limit(500) == 100
