"""Zotero MCP Server — Web API primary, local API optional fast path."""

__version__ = "0.8.0"

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
