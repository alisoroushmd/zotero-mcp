"""Zotero MCP Server — Web API primary, local API optional fast path."""

# Single-source the version from installed package metadata (ZOT-10) so the
# in-package __version__, the wheel, and manifest.json cannot drift. Falls back
# to a literal only when running from an uninstalled source tree.
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("zotero-mcp-plus")
except PackageNotFoundError:  # pragma: no cover — uninstalled source tree
    __version__ = "0.0.0+unknown"

# Opportunistically use the OS trust store for SSL verification. Fixes
# CERTIFICATE_VERIFY_FAILED for third-party hosts (Nature, PMC, bioRxiv,
# OpenAlex) on Python builds whose default cert bundle is stale — most
# commonly Homebrew Python 3.14 on macOS. Silently fall through if the
# `truststore` package is not installed; Python's default verification path
# still applies, so this is a pure upgrade.
try:  # pragma: no cover — environment-dependent
    import truststore  # type: ignore[import-not-found]

    truststore.inject_into_ssl()
except ImportError:
    pass
