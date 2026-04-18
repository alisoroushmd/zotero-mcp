"""Build an MCPB (Model Context Protocol Bundle) from this repo.

An MCPB is a zip with `manifest.json` at the root plus enough of the
source tree for `uv run zotero-mcp` to launch after extraction. Claude
Desktop / Claude Cowork extracts the bundle, prompts the user for the
`user_config` values declared in the manifest, and runs the
`mcp_config.command` with those values injected into `env`.

Usage:
    python mcpb/build.py            # build dist/mcpb/zotero-mcp.mcpb
    python mcpb/build.py validate   # lint manifest only

Or via the Makefile:
    make mcpb

No third-party deps on purpose: this script runs in CI before the
project is installed, so we get by with stdlib `tomllib` / `zipfile` /
`json`. The manifest lives at the repo root (the MCPB spec convention)
as the single source of truth for display metadata and launch args;
only `version` is stamped from `pyproject.toml` so the two never drift
at release time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any

# `mcpb/build.py` → `ROOT` is the repo root (parent of the `mcpb/` folder).
ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "mcpb"
MANIFEST_PATH = ROOT / "manifest.json"
PYPROJECT_PATH = ROOT / "pyproject.toml"

# Things we never want inside the bundle. `.venv` would balloon the zip
# past 500 MB; `tests/` and `docs/` inflate it without helping at
# runtime. Kept as a basename list (not globs) so `os.walk` can filter
# dirs in place.
_EXCLUDE_DIR_NAMES = {
    ".venv",
    ".git",
    ".github",
    ".vscode",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "__pycache__",
    "dist",
    "build",
    "node_modules",
    "tests",
    "docs",
}

_EXCLUDE_FILE_SUFFIXES = {".pyc", ".pyo", ".egg-info"}

# Repo-relative sources to include. Dirs are walked (with exclusions);
# files are added verbatim. `uv.lock` ships so deps resolve to the same
# versions we tested with; `pyproject.toml` + `src/` are what `uv run`
# actually needs to launch.
_BUNDLE_SOURCES = [
    "src",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
]


def load_pyproject() -> dict[str, Any]:
    with PYPROJECT_PATH.open("rb") as f:
        return tomllib.load(f)


def load_manifest_template() -> dict[str, Any]:
    with MANIFEST_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def render_manifest() -> dict[str, Any]:
    """Stamp the pyproject version onto the checked-in manifest."""
    manifest = load_manifest_template()
    pyproj = load_pyproject()
    manifest["version"] = pyproj["project"]["version"]
    return manifest


def _iter_bundle_files() -> list[tuple[Path, str]]:
    """Yield (abs_path, archive_path) pairs, using POSIX archive paths
    so bundles unzip identically on Windows and macOS."""
    collected: list[tuple[Path, str]] = []
    for rel in _BUNDLE_SOURCES:
        source = ROOT / rel
        if not source.exists():
            continue
        if source.is_file():
            collected.append((source, source.relative_to(ROOT).as_posix()))
            continue
        for abs_root, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIR_NAMES]
            for name in files:
                if any(name.endswith(s) for s in _EXCLUDE_FILE_SUFFIXES):
                    continue
                abs_file = Path(abs_root) / name
                collected.append(
                    (abs_file, abs_file.relative_to(ROOT).as_posix())
                )
    return collected


def _assert_manifest_shape(m: dict[str, Any]) -> None:
    """Minimal structural lint — catch common regressions without a
    full JSON schema dep."""
    required_top = {
        "manifest_version",
        "name",
        "display_name",
        "version",
        "description",
        "server",
        "user_config",
    }
    missing = required_top - m.keys()
    if missing:
        raise AssertionError(f"manifest missing keys: {sorted(missing)}")
    if "mcp_config" not in m["server"]:
        raise AssertionError("server.mcp_config absent")
    if not isinstance(m["user_config"], dict):
        raise AssertionError("user_config must be a dict")


def build() -> Path:
    manifest = render_manifest()
    _assert_manifest_shape(manifest)

    DIST.mkdir(parents=True, exist_ok=True)
    out_path = DIST / "zotero-mcp.mcpb"
    if out_path.exists():
        out_path.unlink()

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for abs_file, arc_name in _iter_bundle_files():
            zf.write(abs_file, arc_name)

    return out_path


def validate() -> int:
    try:
        manifest = render_manifest()
        _assert_manifest_shape(manifest)
    except Exception as exc:  # noqa: BLE001 — surface the message verbatim
        print(f"[fail] {type(exc).__name__}: {exc}")
        return 1
    print(f"[ok] manifest v{manifest['version']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="build",
        help="'build' (default) or 'validate'",
    )
    args = parser.parse_args()

    if args.target == "validate":
        return validate()
    if args.target == "build":
        out = build()
        size_kb = out.stat().st_size / 1024
        print(f"Built {out.relative_to(ROOT)}  ({size_kb:,.1f} KiB)")
        return 0
    print(f"Unknown target {args.target!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
