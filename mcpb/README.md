# MCPB packaging

Tooling for packaging `zotero-mcp` as an **MCPB** (Model Context Protocol
Bundle) — a single `.mcpb` zip that Claude Desktop / Claude Cowork users
can drag-drop for one-click install. The host app extracts the bundle,
prompts the user for the values declared in `manifest.json` →
`user_config`, then launches the MCP server with those values exposed as
env vars.

## Layout

| Path | Purpose |
| --- | --- |
| `../manifest.json` | MCPB manifest — lives at the repo root per the MCPB spec. Source of truth for display metadata, `user_config`, and launch args. |
| `build.py` | Stdlib-only builder. Reads `../manifest.json`, stamps `version` from `../pyproject.toml`, and zips the package source + `uv.lock` + `pyproject.toml` + `README.md` + `LICENSE`. |
| `../.github/workflows/release.yml` | CI. Fires on `v*` tag push, runs `build.py`, and attaches the resulting `.mcpb` to a GitHub Release. |

## Build locally

```bash
uv sync
uv run python mcpb/build.py
```

Or via the Makefile:

```bash
make mcpb
```

Output lands in `dist/mcpb/zotero-mcp.mcpb` (~100 KiB).

`validate` lints the manifest shape without writing the archive:

```bash
uv run python mcpb/build.py validate
```

## Cut a release

1. Bump `version` in `pyproject.toml`.
2. Commit + push.
3. Tag: `git tag v0.9.0 && git push --tags`.

The `release` workflow verifies the tag matches the pyproject version
(fail-fast on drift), builds the bundle, and publishes the GitHub
Release with the `.mcpb` attached.

## Install the bundle

End users don't run `build.py`. They download the `.mcpb` from the
[Releases page](https://github.com/alisoroushmd/zotero-mcp/releases)
and drag it into Claude Desktop or Claude Cowork. The app handles
unpacking and prompting for Zotero credentials.
