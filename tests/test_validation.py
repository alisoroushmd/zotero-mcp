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


# -- _validate_path tests --


def test_validate_path_rejects_traversal():
    """_validate_path raises ValueError for absolute paths outside allowed roots."""
    from zotero_mcp.server import _validate_path

    with pytest.raises(ValueError, match="must be within"):
        _validate_path("/etc/passwd", "path")


def test_validate_path_rejects_etc_shadow():
    """_validate_path raises ValueError for /etc/shadow."""
    from zotero_mcp.server import _validate_path

    with pytest.raises(ValueError, match="must be within"):
        _validate_path("/etc/shadow", "path")


def test_validate_path_accepts_home():
    """_validate_path accepts a path inside the user home directory."""
    import pathlib

    from zotero_mcp.server import _validate_path

    home = str(pathlib.Path.home() / "test_output.docx")
    result = _validate_path(home, "path")
    assert result.startswith(str(pathlib.Path.home()))


def test_validate_path_accepts_tmp():
    """_validate_path accepts a path inside the system temp directory."""
    import pathlib
    import tempfile

    from zotero_mcp.server import _validate_path

    tmp = str(pathlib.Path(tempfile.gettempdir()) / "test.html")
    result = _validate_path(tmp, "path")
    # Resolve both sides to handle macOS /var -> /private/var symlink
    resolved_tmp_root = str(pathlib.Path(tempfile.gettempdir()).resolve())
    assert result.startswith(resolved_tmp_root)
