"""Entry point for python -m zotero_mcp."""

import logging
import sys

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    stream=sys.stderr,
)


def main() -> None:
    # Self-reap when launcher chain (Claude.app disclaimer / uvx / uv) exits
    # without propagating stdin-close. See zotero_mcp._orphan_watchdog.
    from zotero_mcp._orphan_watchdog import install as _install_watchdog

    _install_watchdog()

    from zotero_mcp.server import mcp

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
