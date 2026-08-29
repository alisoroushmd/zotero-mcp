"""Migrate Zotero ``imported_*`` attachments to ``linked_file`` attachments.

Imported attachments live in Zotero's cloud storage and count against the
(300 MB, on the free tier) sync quota. Once the quota is full, every new
upload fails with HTTP 413 and ``attach_pdf`` cannot store anything. Linked
files keep the bytes on local disk and consume no quota at all: only the item
metadata syncs.

This module converts the former into the latter without losing a file. The
design is deliberately paranoid, because the last step is irreversible:

1. **Inventory** every attachment in the library via the Web API (the
   authoritative view of cloud state — the local SQLite DB can be stale) and
   cross-reference each one against the local ``storage/<key>/<filename>``
   tree.
2. **Plan** the migration. Each attachment is classified by where its bytes
   actually are: ``local`` (copy from disk), ``cloud`` (must be downloaded
   first), or ``unavailable`` (no bytes anywhere — never migrated, never
   trashed). Nothing is written during planning.
3. **Materialize** the bytes, write them into the linked-attachment
   directory, and **hash-verify** by re-reading the written file from disk.
   A download is additionally checked against the server-side MD5.
4. **Create** the ``linked_file`` attachment on the same parent item.
5. **Trash** the old imported attachment — and only ever an attachment whose
   replacement was confirmed created in step 4.
6. **Empty the trash** only in a separate invocation, using the durable list
   of migration-owned keys recorded before step 5 and only behind
   :func:`check_trash_is_exactly`, because ``DELETE /items/trash`` is global.

Every entry point defaults to ``dry_run=True``. Callers must pass
``dry_run=False`` explicitly to change anything.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

    from zotero_mcp.web_client import WebClient

logger = logging.getLogger(__name__)

# Link modes that occupy Zotero cloud storage and can be converted.
# imported_file  — a file the user (or attach_pdf) added directly.
# imported_url   — a web-page snapshot. Zotero has no "linked snapshot" mode;
#                  explicit opt-in is accepted only for a validated local
#                  single-file snapshot, never for a resource directory.
MIGRATABLE_MODES: tuple[str, ...] = ("imported_file", "imported_url")
# Web snapshots may contain a directory tree even when Zotero reports one
# attachment item. Converting only the top-level HTML file would silently lose
# the companion resources, so snapshots require explicit selection and a
# local, single-file validation before they are eligible.
DEFAULT_MIGRATION_MODES: tuple[str, ...] = ("imported_file",)

# Zotero regenerates this per-attachment full-text index itself; it is not
# part of the attachment's file and must not be copied or hashed.
_ZOTERO_SIDECAR_NAMES = frozenset({".zotero-ft-cache", ".zotero-ft-info", ".DS_Store"})

DOWNLOAD_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
_TRASH_STATE_FILENAME = ".zotero-attachment-migration-trash.json"
_TRASH_STATE_VERSION = 1

Source = Literal["local", "cloud", "unavailable"]
Action = Literal["copy", "download"]


class MigrationAbort(RuntimeError):
    """Raised when a safety precondition fails and the run must not continue."""


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AttachmentRecord:
    """One imported attachment, with where its bytes actually live."""

    key: str
    parent_key: str
    link_mode: str
    filename: str
    content_type: str
    title: str
    version: int
    # Server-side MD5. Empty when Zotero holds no file for this attachment —
    # which means it occupies no cloud quota, however large the local copy is.
    cloud_md5: str = ""
    # Absolute path under <data_dir>/storage/<key>/, or "" when absent locally.
    local_path: str = ""
    local_size: int = 0
    # imported_url is safe to flatten to linked_file only when its local
    # storage directory contains exactly one regular, non-symlink payload.
    single_file_snapshot: bool = False

    @property
    def holds_cloud_quota(self) -> bool:
        """True when Zotero's servers actually store bytes for this attachment."""
        return bool(self.cloud_md5)

    @property
    def source(self) -> Source:
        """Where the bytes can be obtained from."""
        if self.local_path:
            return "local"
        if self.cloud_md5:
            return "cloud"
        return "unavailable"


@dataclass(frozen=True)
class LibraryAttachments:
    """Result of a full attachment sweep."""

    imported: list[AttachmentRecord]
    # Absolute linked-file paths already in use, keyed by parent item. Used to
    # make the migration idempotent: a parent that already links the same file
    # is skipped rather than given a duplicate attachment.
    linked_paths_by_parent: dict[str, set[str]]
    total_attachments: int

    @property
    def quota_holding(self) -> list[AttachmentRecord]:
        return [r for r in self.imported if r.holds_cloud_quota]


def _safe_storage_base(storage_dir: str, key: str) -> Path | None:
    """Return a resolved attachment directory only when it is safely contained."""
    if not key or Path(key).name != key or key in {".", ".."}:
        return None
    storage = Path(storage_dir).expanduser().resolve(strict=False)
    raw_base = storage / key
    if raw_base.is_symlink():
        return None
    base = raw_base.resolve(strict=False)
    if base.parent != storage:
        return None
    return base


def _storage_path(storage_dir: str, key: str, filename: str) -> str:
    """Resolve <storage_dir>/<key>/<filename>, returning "" if it is not there.

    Zotero stores ``path`` as ``storage:<filename>`` for imported attachments;
    the caller passes the already-stripped filename. When ``filename`` is
    empty (some legacy rows), fall back to the single non-sidecar file in the
    attachment's directory.
    """
    base = _safe_storage_base(storage_dir, key)
    if base is None:
        return ""
    if filename:
        if Path(filename).name != filename or filename in {".", ".."}:
            return ""
        candidate = base / filename
        if candidate.is_symlink() or not candidate.is_file():
            return ""
        resolved = candidate.resolve(strict=True)
        return str(resolved) if resolved.parent == base else ""
    if not base.is_dir():
        return ""
    entries = [e for e in base.iterdir() if e.name not in _ZOTERO_SIDECAR_NAMES]
    if len(entries) == 1:
        candidate = entries[0]
        if candidate.is_symlink() or not candidate.is_file():
            return ""
        resolved = candidate.resolve(strict=True)
        return str(resolved) if resolved.parent == base else ""
    return ""


def _is_single_file_snapshot(storage_dir: str, key: str, local_path: str) -> bool:
    """Return True only for one self-contained, regular snapshot payload."""
    if not local_path:
        return False
    base = _safe_storage_base(storage_dir, key)
    if base is None or not base.is_dir():
        return False
    entries = [e for e in base.iterdir() if e.name not in _ZOTERO_SIDECAR_NAMES]
    if len(entries) != 1 or entries[0].is_symlink() or not entries[0].is_file():
        return False
    return entries[0].resolve(strict=True) == Path(local_path).resolve(strict=True)


def _strip_storage_prefix(path: str) -> str:
    """Turn Zotero's ``storage:foo.pdf`` path form into ``foo.pdf``."""
    return path[len("storage:") :] if path.startswith("storage:") else path


def inventory(
    web: WebClient,
    *,
    storage_dir: str | None = None,
    page_size: int = 100,
) -> LibraryAttachments:
    """Enumerate every attachment in the library and locate its bytes.

    Reads exclusively through the Web API, which is authoritative for cloud
    state (``md5`` is set only when the server actually holds the file). The
    local SQLite DB is not consulted; only the filesystem is, to check whether
    each imported file has already been downloaded to this machine.

    Args:
        web: Authenticated :class:`~zotero_mcp.web_client.WebClient`.
        storage_dir: Zotero ``storage/`` directory. Defaults to
            ``<effective_zotero_data_dir>/storage``.
        page_size: Web API page size (max 100).

    Returns:
        A :class:`LibraryAttachments` sweep.
    """
    from zotero_mcp.config import get_config

    if storage_dir is None:
        storage_dir = os.path.join(get_config().effective_zotero_data_dir, "storage")

    imported: list[AttachmentRecord] = []
    linked_by_parent: dict[str, set[str]] = {}
    start = 0
    total: int | None = None

    while True:
        resp = web._read_get(
            "/items",
            params={"itemType": "attachment", "limit": page_size, "start": start},
        )
        resp.raise_for_status()
        if total is None:
            total = int(resp.headers.get("Total-Results", "0"))
        batch = resp.json()
        if not batch:
            break

        for item in batch:
            data = item.get("data", {})
            mode = data.get("linkMode", "")
            parent = data.get("parentItem", "")

            if mode == "linked_file":
                path = data.get("path", "")
                if path:
                    linked_by_parent.setdefault(parent, set()).add(
                        os.path.abspath(os.path.expanduser(path))
                    )
                continue

            if mode not in MIGRATABLE_MODES:
                continue

            key = data.get("key", "")
            filename = _strip_storage_prefix(data.get("filename", "") or data.get("path", ""))
            local_path = _storage_path(storage_dir, key, filename)
            imported.append(
                AttachmentRecord(
                    key=key,
                    parent_key=parent,
                    link_mode=mode,
                    filename=filename,
                    content_type=data.get("contentType", ""),
                    title=data.get("title", ""),
                    version=int(data.get("version", 0) or 0),
                    cloud_md5=data.get("md5") or "",
                    local_path=local_path,
                    local_size=os.path.getsize(local_path) if local_path else 0,
                    single_file_snapshot=(
                        mode == "imported_url"
                        and _is_single_file_snapshot(storage_dir, key, local_path)
                    ),
                )
            )

        start += page_size
        if total is not None and start >= total:
            break

    return LibraryAttachments(
        imported=imported,
        linked_paths_by_parent=linked_by_parent,
        total_attachments=total or 0,
    )


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedMove:
    """One attachment that will be converted."""

    record: AttachmentRecord
    dest_path: str
    action: Action

    @property
    def needs_download(self) -> bool:
        return self.action == "download"


@dataclass(frozen=True)
class SkippedItem:
    """One attachment the plan deliberately leaves alone, and why."""

    record: AttachmentRecord
    reason: str


@dataclass(frozen=True)
class MigrationPlan:
    """An immutable description of what a migration run would do."""

    moves: list[PlannedMove]
    skipped: list[SkippedItem]
    dest_dir: str
    modes: tuple[str, ...]
    total_attachments: int = 0

    @property
    def copy_count(self) -> int:
        return sum(1 for m in self.moves if m.action == "copy")

    @property
    def download_count(self) -> int:
        return sum(1 for m in self.moves if m.action == "download")

    @property
    def known_bytes(self) -> int:
        """Bytes we can size up front (local sources only)."""
        return sum(m.record.local_size for m in self.moves if m.action == "copy")

    @property
    def quota_freeing_count(self) -> int:
        """Moves that will actually return cloud storage to the quota."""
        return sum(1 for m in self.moves if m.record.holds_cloud_quota)


def _validate_dest_dir(dest_dir: str, storage_dir: str) -> str:
    """Reject a destination that Zotero itself manages.

    Writing linked files inside ``storage/`` would let Zotero's own
    housekeeping delete them: it prunes that tree for imported attachments and
    knows nothing about our links.
    """
    dest = str(Path(dest_dir).expanduser().resolve(strict=False))
    storage = str(Path(storage_dir).expanduser().resolve(strict=False))
    if Path(dest) == Path(storage) or Path(dest).is_relative_to(Path(storage)):
        raise MigrationAbort(
            f"Destination {dest!r} is inside Zotero's storage/ directory, which "
            "Zotero prunes on its own. Choose a directory outside it "
            "(default: <data_dir>/linked-attachments)."
        )
    return dest


def build_plan(
    sweep: LibraryAttachments,
    *,
    dest_dir: str | None = None,
    storage_dir: str | None = None,
    modes: tuple[str, ...] = DEFAULT_MIGRATION_MODES,
    quota_only: bool = True,
    limit: int | None = None,
) -> MigrationPlan:
    """Decide what to migrate. Pure: touches neither disk nor network.

    Args:
        sweep: Output of :func:`inventory`.
        dest_dir: Where linked files will be written. Defaults to the
            configured ``effective_linked_attachment_dir``.
        storage_dir: Zotero storage dir, used only to reject a dangerous
            ``dest_dir``.
        modes: Which link modes to convert. Narrow this to run one class at a
            time, e.g. ``("imported_file",)`` for PDFs only.
        quota_only: Skip attachments the server holds no file for. Those cost
            no quota, so converting them frees nothing.
        limit: Cap the number of moves — useful for a cautious first batch.

    Returns:
        A :class:`MigrationPlan`.
    """
    from zotero_mcp.config import get_config

    cfg = get_config()
    if dest_dir is None:
        dest_dir = cfg.effective_linked_attachment_dir
    if storage_dir is None:
        storage_dir = os.path.join(cfg.effective_zotero_data_dir, "storage")
    dest = _validate_dest_dir(dest_dir, storage_dir)

    moves: list[PlannedMove] = []
    skipped: list[SkippedItem] = []
    # Names claimed by earlier moves in this same plan, so two attachments
    # with the same filename don't both plan to write the same path.
    claimed: set[str] = set()

    for rec in sweep.imported:
        if rec.link_mode not in modes:
            skipped.append(SkippedItem(rec, f"link mode {rec.link_mode} not selected"))
            continue
        if rec.link_mode == "imported_url" and not rec.single_file_snapshot:
            skipped.append(
                SkippedItem(
                    rec,
                    "imported_url snapshot is not a validated local single file; "
                    "companion resources could be lost",
                )
            )
            continue
        if not rec.parent_key:
            skipped.append(SkippedItem(rec, "standalone attachment (no parent item)"))
            continue
        if rec.source == "unavailable":
            skipped.append(
                SkippedItem(rec, "no bytes available — absent locally and not stored in the cloud")
            )
            continue
        if quota_only and not rec.holds_cloud_quota:
            skipped.append(
                SkippedItem(rec, "holds no cloud storage (local-only) — nothing to reclaim")
            )
            continue

        dest_path = _plan_destination(Path(dest), rec, claimed)
        already = sweep.linked_paths_by_parent.get(rec.parent_key, set())
        if os.path.abspath(dest_path) in already:
            skipped.append(SkippedItem(rec, "parent already has a linked_file at this path"))
            continue

        if limit is not None and len(moves) >= limit:
            skipped.append(SkippedItem(rec, f"beyond --limit {limit}"))
            continue

        claimed.add(os.path.abspath(dest_path))
        moves.append(
            PlannedMove(
                record=rec,
                dest_path=dest_path,
                action="copy" if rec.source == "local" else "download",
            )
        )

    return MigrationPlan(
        moves=moves,
        skipped=skipped,
        dest_dir=dest,
        modes=tuple(modes),
        total_attachments=sweep.total_attachments,
    )


def _plan_destination(base: Path, rec: AttachmentRecord, claimed: set[str]) -> str:
    """Choose a collision-free destination path for one attachment.

    Mirrors ``WebClient._resolve_link_destination`` but works without the
    bytes in hand, so it can run during a dry run. Collisions against files
    that already exist on disk and against other moves in the same plan are
    both resolved by appending the attachment key, which is unique per item.
    """
    safe_name = Path(rec.filename).name or f"{rec.key}.bin"
    candidate = base / safe_name
    if not candidate.exists() and str(candidate.absolute()) not in claimed:
        return str(candidate)
    stem, suffix = candidate.stem, candidate.suffix
    return str(base / f"{stem}-{rec.key}{suffix}")


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def render_plan(plan: MigrationPlan, *, show: int = 20) -> str:
    """Render a plan as a human-readable report."""
    lines: list[str] = []
    lines.append("Zotero attachment migration — DRY RUN PLAN")
    lines.append(f"  destination      : {plan.dest_dir}")
    lines.append(f"  link modes       : {', '.join(plan.modes)}")
    lines.append(f"  library attachments: {plan.total_attachments}")
    lines.append("")
    lines.append(f"  to migrate       : {len(plan.moves)}")
    lines.append(f"    copy from disk : {plan.copy_count}  ({_mb(plan.known_bytes)} known)")
    lines.append(f"    download first : {plan.download_count}  (size unknown until fetched)")
    lines.append(f"    frees quota    : {plan.quota_freeing_count}")
    lines.append(f"  skipped          : {len(plan.skipped)}")

    by_reason: dict[str, int] = {}
    for s in plan.skipped:
        by_reason[s.reason] = by_reason.get(s.reason, 0) + 1
    for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {count:5d}  {reason}")

    if plan.moves:
        lines.append("")
        lines.append(f"  first {min(show, len(plan.moves))} moves:")
        for m in plan.moves[:show]:
            size = _mb(m.record.local_size) if m.action == "copy" else "?"
            lines.append(
                f"    [{m.action:8s}] {m.record.key}  {m.record.link_mode:13s} "
                f"{size:>9s}  {m.record.filename[:52]}"
            )
        if len(plan.moves) > show:
            lines.append(f"    ... and {len(plan.moves) - show} more")

    lines.append("")
    lines.append("  Nothing has been written. Re-run with dry_run=False to apply.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


@dataclass
class MoveOutcome:
    """What happened to one planned move."""

    key: str
    parent_key: str
    filename: str
    action: str
    status: str  # "migrated" | "failed" | "would_migrate"
    dest_path: str = ""
    size_bytes: int = 0
    sha256: str = ""
    new_attachment_key: str = ""
    error: str = ""


@dataclass
class MigrationResult:
    """Aggregate outcome of an execution run."""

    dry_run: bool
    outcomes: list[MoveOutcome] = field(default_factory=list)
    trashed: list[str] = field(default_factory=list)
    trash_failed: list[str] = field(default_factory=list)
    trash_emptied: bool = False
    trash_state_path: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def migrated(self) -> list[MoveOutcome]:
        return [o for o in self.outcomes if o.status in ("migrated", "would_migrate")]

    @property
    def failed(self) -> list[MoveOutcome]:
        return [o for o in self.outcomes if o.status == "failed"]

    @property
    def bytes_written(self) -> int:
        return sum(o.size_bytes for o in self.migrated)

    def summary(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "migrated": len(self.migrated),
            "failed": len(self.failed),
            "bytes": self.bytes_written,
            "trashed": len(self.trashed),
            "trash_failed": len(self.trash_failed),
            "trash_emptied": self.trash_emptied,
            "trash_state_path": self.trash_state_path,
            "notes": self.notes,
        }


def _download_attachment(web: WebClient, key: str) -> bytes:
    """Fetch an attachment's bytes from Zotero cloud storage.

    ``GET /items/<key>/file`` answers 302 with a presigned S3 URL. The
    redirect is followed manually with a *clean* client so the Zotero API key
    is never forwarded to the storage host.
    """
    from zotero_mcp.web_client import _validate_url

    resp = web._read_get(f"/items/{key}/file")
    if resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("Location", "")
        if not location:
            raise MigrationAbort(f"{key}: redirect with no Location header")
        try:
            _validate_url(location)
            with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as clean:
                file_resp = clean.get(location)
                file_resp.raise_for_status()
                return file_resp.content
        except httpx.HTTPStatusError as exc:
            # httpx includes the complete request URL in its exception text.
            # Presigned storage URLs carry credentials/signatures in the query,
            # so expose only the status and attachment key.
            raise MigrationAbort(
                f"{key}: storage download failed with HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError as exc:
            raise MigrationAbort(
                f"{key}: storage download failed ({exc.__class__.__name__})"
            ) from None
    resp.raise_for_status()
    return resp.content


def _materialize(web: WebClient, move: PlannedMove) -> bytes:
    """Obtain the attachment's bytes from wherever they live."""
    if move.action == "copy":
        return Path(move.record.local_path).read_bytes()
    return _download_attachment(web, move.record.key)


def _write_and_verify(data: bytes, dest: Path) -> tuple[int, str]:
    """Write ``data`` to ``dest`` atomically, then verify by re-reading it.

    The hash is computed over what is actually on disk afterwards, not over
    the in-memory buffer, so a short write or a full filesystem is caught
    before the source attachment is trashed.

    Returns:
        ``(size_bytes, sha256_hex)``.

    Raises:
        MigrationAbort: If the file on disk does not match the source bytes.
    """
    expected = hashlib.sha256(data).hexdigest()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".partial")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    actual_bytes = dest.read_bytes()
    actual = hashlib.sha256(actual_bytes).hexdigest()
    if actual != expected:
        raise MigrationAbort(
            f"Hash mismatch after writing {dest}: expected {expected[:12]}…, "
            f"read back {actual[:12]}…. The file was NOT trusted; source left intact."
        )
    if len(actual_bytes) != len(data):
        raise MigrationAbort(
            f"Size mismatch after writing {dest}: {len(actual_bytes)} != {len(data)}"
        )
    return len(actual_bytes), actual


def _verify_cloud_md5(record: AttachmentRecord, data: bytes) -> None:
    """Check downloaded bytes against Zotero's server-side MD5, when known."""
    if not record.cloud_md5:
        return
    actual = hashlib.md5(data).hexdigest()  # noqa: S324 — Zotero's checksum, not security
    if actual != record.cloud_md5:
        raise MigrationAbort(
            f"{record.key}: downloaded bytes do not match the server MD5 "
            f"(expected {record.cloud_md5[:12]}…, got {actual[:12]}…)."
        )


def _create_linked_attachment(web: WebClient, move: PlannedMove, dest: Path) -> str:
    """Create the replacement ``linked_file`` attachment. Returns its key."""
    from zotero_mcp.web_client import _retry_request

    payload = [
        {
            "itemType": "attachment",
            "parentItem": move.record.parent_key,
            "linkMode": "linked_file",
            "title": move.record.title or dest.name,
            "contentType": move.record.content_type or "application/octet-stream",
            "path": str(dest),
        }
    ]
    resp = _retry_request(lambda: web._web_client.post("/items", json=payload))
    resp.raise_for_status()
    key = web._extract_created_key(resp.json()).strip()
    if not key:
        raise MigrationAbort(
            f"{move.record.key}: Zotero created no identifiable replacement attachment; "
            "the original was not trashed"
        )
    return key


def _trash_state_path(dest_dir: str) -> Path:
    """Return the migration-owned trash journal path for a destination."""
    return Path(dest_dir).resolve(strict=False) / _TRASH_STATE_FILENAME


def _load_trash_state(web: WebClient, dest_dir: str) -> list[str]:
    """Load persisted migration-owned trash keys for this Zotero library."""
    path = _trash_state_path(dest_dir)
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise MigrationAbort(f"Unsafe migration trash state path: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationAbort(f"Cannot read migration trash state {path}: {exc}") from None
    if not isinstance(payload, dict) or payload.get("version") != _TRASH_STATE_VERSION:
        raise MigrationAbort(f"Unsupported migration trash state format in {path}")
    library_user_id = str(payload.get("library_user_id", ""))
    if not library_user_id or library_user_id != str(web._user_id):
        raise MigrationAbort(f"Migration trash state {path} belongs to a different Zotero library")
    raw_keys = payload.get("trashed_attachment_keys")
    if not isinstance(raw_keys, list) or not all(isinstance(key, str) and key for key in raw_keys):
        raise MigrationAbort(f"Invalid migration-owned key list in {path}")
    return sorted(set(raw_keys))


def _write_trash_state(web: WebClient, dest_dir: str, keys: list[str]) -> Path:
    """Atomically persist the complete migration-owned trash-key set."""
    path = _trash_state_path(dest_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise MigrationAbort(f"Unsafe migration trash state symlink: {path}")
    tmp = path.with_name(path.name + ".partial")
    if tmp.is_symlink():
        raise MigrationAbort(f"Unsafe migration trash state symlink: {tmp}")
    payload = {
        "version": _TRASH_STATE_VERSION,
        "library_user_id": str(web._user_id),
        "trashed_attachment_keys": sorted(set(keys)),
    }
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise MigrationAbort(f"Cannot persist migration trash ownership at {path}: {exc}") from exc
    finally:
        if tmp.exists() and not tmp.is_symlink():
            tmp.unlink(missing_ok=True)
    return path


def _record_trash_intent(web: WebClient, dest_dir: str, keys: list[str]) -> Path:
    """Persist keys before DELETE so a crash cannot erase ownership evidence."""
    existing = _load_trash_state(web, dest_dir)
    return _write_trash_state(web, dest_dir, [*existing, *keys])


def _retain_missing_trash_keys(web: WebClient, dest_dir: str, keys: list[str]) -> None:
    """After a purge, retain ownership only for expected keys not in trash."""
    path = _trash_state_path(dest_dir)
    if keys:
        _write_trash_state(web, dest_dir, keys)
    elif path.exists():
        if path.is_symlink() or not path.is_file():
            raise MigrationAbort(f"Unsafe migration trash state path: {path}")
        path.unlink()


def migrate(
    plan: MigrationPlan,
    web: WebClient,
    *,
    dry_run: bool = True,
    trash: bool = True,
    on_progress: Callable[[int, int, PlannedMove], None] | None = None,
) -> MigrationResult:
    """Execute a plan.

    Order is chosen so that a crash at any point leaves the library
    recoverable: bytes are secured on local disk and verified, the
    replacement attachment is created, and only then is the original moved to
    the trash. An attachment whose replacement failed is never trashed.

    This function never empties the trash. Call :func:`empty_trash_guarded`
    separately, after reviewing what is in there.

    Args:
        plan: Output of :func:`build_plan`.
        web: Authenticated client.
        dry_run: When True (the default) nothing is written, downloaded, or
            trashed; every move is reported as ``would_migrate``.
        trash: Move successfully replaced originals to the trash. Set False to
            create the links first and trash in a later, separate pass.
        on_progress: Optional ``(index, total, move)`` callback.

    Returns:
        A :class:`MigrationResult`.
    """
    result = MigrationResult(dry_run=dry_run)
    total = len(plan.moves)
    to_trash: list[str] = []

    for idx, move in enumerate(plan.moves, start=1):
        if on_progress:
            on_progress(idx, total, move)

        rec = move.record
        outcome = MoveOutcome(
            key=rec.key,
            parent_key=rec.parent_key,
            filename=rec.filename,
            action=move.action,
            status="would_migrate" if dry_run else "failed",
            dest_path=move.dest_path,
            size_bytes=rec.local_size if move.action == "copy" else 0,
        )

        if dry_run:
            result.outcomes.append(outcome)
            continue

        try:
            data = _materialize(web, move)
            _verify_cloud_md5(rec, data)
            size, digest = _write_and_verify(data, Path(move.dest_path))
            outcome.size_bytes = size
            outcome.sha256 = digest
            outcome.new_attachment_key = _create_linked_attachment(web, move, Path(move.dest_path))
            outcome.status = "migrated"
            to_trash.append(rec.key)
            logger.info(
                "Migrated %s (%s) -> %s [%s]",
                rec.key,
                rec.link_mode,
                move.dest_path,
                outcome.new_attachment_key,
            )
        except Exception as exc:  # noqa: BLE001 — one bad item must not stop the run
            outcome.error = f"{exc.__class__.__name__}: {exc}"
            logger.warning("Migration failed for %s: %s", rec.key, outcome.error)

        result.outcomes.append(outcome)

    if dry_run:
        result.notes.append(
            f"Dry run — {total} attachment(s) would be migrated to {plan.dest_dir}. "
            "Nothing was written, downloaded, or trashed."
        )
        return result

    if trash and to_trash:
        try:
            state_path = _record_trash_intent(web, plan.dest_dir, to_trash)
            result.trash_state_path = str(state_path)
        except MigrationAbort as exc:
            # The replacement links already exist, but without durable ownership
            # evidence a later invocation cannot distinguish these originals from
            # unrelated trash. Fail closed and leave every original in place.
            result.trash_failed = list(to_trash)
            result.notes.append(
                f"{len(to_trash)} attachment(s) were replaced but not trashed because "
                f"migration trash ownership could not be persisted: {exc}"
            )
        else:
            trash_result = web.trash_items(to_trash)
            result.trashed = trash_result.get("trashed", [])
            result.trash_failed = trash_result.get("failed", [])
            if result.trash_failed:
                result.notes.append(
                    f"{len(result.trash_failed)} attachment(s) were replaced but could not be "
                    "trashed. Their linked copies exist, so the originals are now duplicates; "
                    "trash them manually."
                )
    elif to_trash:
        result.notes.append(
            f"{len(to_trash)} original(s) left in place (trash=False). "
            "Quota is not reclaimed until they are trashed and the trash is emptied."
        )

    if result.failed:
        result.notes.append(
            f"{len(result.failed)} attachment(s) failed and were left untouched — "
            "their originals are intact."
        )
    return result


# --------------------------------------------------------------------------
# Trash handling — the irreversible step
# --------------------------------------------------------------------------


def list_trash(web: WebClient, *, page_size: int = 100) -> list[dict]:
    """Return a compact listing of every item currently in the Zotero trash."""
    items: list[dict] = []
    start = 0
    total: int | None = None
    while True:
        resp = web._read_get(
            "/items/trash",
            params={"limit": page_size, "start": start},
        )
        resp.raise_for_status()
        if total is None:
            total = int(resp.headers.get("Total-Results", "0"))
        batch = resp.json()
        if not batch:
            break
        for item in batch:
            data = item.get("data", {})
            items.append(
                {
                    "key": data.get("key", ""),
                    "itemType": data.get("itemType", ""),
                    "linkMode": data.get("linkMode", ""),
                    "title": data.get("title", "") or data.get("filename", ""),
                    "parentItem": data.get("parentItem", ""),
                }
            )
        start += page_size
        if total is not None and start >= total:
            break
    return items


def check_trash_is_exactly(web: WebClient, expected_keys: list[str]) -> dict:
    """Verify the trash holds exactly ``expected_keys`` and nothing else.

    ``DELETE /items/trash`` is global — it destroys everything in the trash,
    not just what this migration put there. This is the guard that makes
    emptying it safe: if the user has unrelated items in the trash (or a
    concurrent Zotero session added some), the caller must stop and let the
    user decide.

    Returns:
        Dict with ``safe`` (bool), ``expected``, ``unexpected`` (keys in the
        trash that the migration did not put there), ``missing`` (expected
        keys not found), and ``trash_count``.
    """
    trash = list_trash(web)
    present = {i["key"] for i in trash}
    expected = set(expected_keys)
    unexpected = sorted(present - expected)
    missing = sorted(expected - present)
    return {
        "safe": not unexpected,
        "trash_count": len(trash),
        "expected": len(expected),
        "unexpected": unexpected,
        "unexpected_items": [i for i in trash if i["key"] in set(unexpected)][:25],
        "missing": missing,
    }


def empty_trash_guarded(
    web: WebClient,
    expected_keys: list[str],
    *,
    dry_run: bool = True,
) -> dict:
    """Empty the Zotero trash, but only if it holds exactly ``expected_keys``.

    Args:
        web: Authenticated client.
        expected_keys: The attachment keys this migration trashed.
        dry_run: When True (default), report what would happen and stop.

    Returns:
        Dict describing the check and, when applied, the result.

    Raises:
        MigrationAbort: If the trash contains anything the migration did not
            put there. Nothing is deleted in that case.
    """
    expected_keys = sorted(set(expected_keys))
    if not expected_keys:
        raise MigrationAbort(
            "Refusing to empty the global Zotero trash without any recorded "
            "migration-owned attachment keys"
        )
    check = check_trash_is_exactly(web, expected_keys)
    if not check["safe"]:
        raise MigrationAbort(
            f"Refusing to empty the trash: it holds {len(check['unexpected'])} item(s) "
            "this migration did not put there, and DELETE /items/trash is global. "
            f"Unexpected keys: {', '.join(check['unexpected'][:10])}"
            + ("…" if len(check["unexpected"]) > 10 else "")
            + " — restore or permanently delete them in Zotero first, then re-run."
        )
    if dry_run:
        return {
            **check,
            "status": "would_empty",
            "message": (
                f"Trash holds exactly the {check['trash_count']} migrated attachment(s). "
                "Re-run with dry_run=False to permanently delete them."
            ),
        }
    result = web.empty_trash()
    return {**check, "status": result.get("status", "emptied")}


def empty_recorded_trash(
    web: WebClient,
    dest_dir: str,
    *,
    dry_run: bool = True,
) -> dict:
    """Empty only trash proven migration-owned by the persisted journal.

    This is the cross-invocation entry point used by ``--empty-trash``. The
    migration writes its intended keys before trashing originals, so a crash
    after Zotero accepts the DELETE cannot lose the ownership evidence.
    """
    expected = _load_trash_state(web, dest_dir)
    if not expected:
        return {
            "status": "skipped",
            "message": "No persisted migration-owned trash keys were found; nothing deleted.",
        }
    report = empty_trash_guarded(web, expected, dry_run=dry_run)
    if not dry_run and report.get("status") == "emptied":
        _retain_missing_trash_keys(web, dest_dir, report.get("missing", []))
    return report


# --------------------------------------------------------------------------
# One-shot convenience wrapper
# --------------------------------------------------------------------------


def run_migration(
    web: WebClient,
    *,
    dry_run: bool = True,
    modes: tuple[str, ...] = DEFAULT_MIGRATION_MODES,
    dest_dir: str | None = None,
    quota_only: bool = True,
    limit: int | None = None,
    trash: bool = True,
    empty_trash: bool = False,
    on_progress: Callable[[int, int, PlannedMove], None] | None = None,
) -> tuple[MigrationPlan, MigrationResult, dict | None]:
    """Inventory → plan → migrate → (optionally) empty trash, in one call.

    ``empty_trash`` is honored only when the migration itself ran for real,
    every move succeeded, and the trash check passes. Any failure short-
    circuits it: a half-migrated library must never have its trash purged.

    Returns:
        ``(plan, result, trash_report_or_None)``.
    """
    sweep = inventory(web)
    plan = build_plan(
        sweep,
        dest_dir=dest_dir,
        modes=modes,
        quota_only=quota_only,
        limit=limit,
    )
    result = migrate(plan, web, dry_run=dry_run, trash=trash, on_progress=on_progress)

    trash_report: dict | None = None
    if empty_trash:
        if dry_run:
            trash_report = {
                "status": "skipped",
                "message": "Dry run — trash not inspected for emptying.",
            }
        elif result.failed or result.trash_failed:
            failure_count = len(result.failed) + len(result.trash_failed)
            trash_report = {
                "status": "skipped",
                "message": (
                    f"{failure_count} move/trash operation(s) failed; refusing to empty the trash "
                    "while the migration is incomplete."
                ),
            }
        else:
            trash_report = empty_recorded_trash(web, plan.dest_dir, dry_run=False)
            result.trash_emptied = trash_report.get("status") == "emptied"

    return plan, result, trash_report


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="zotero-migrate-attachments",
        description=(
            "Convert Zotero imported attachments to linked files, freeing cloud "
            "storage quota while keeping every file on local disk. Dry run by default."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the migration. Without this flag nothing is written.",
    )
    parser.add_argument(
        "--modes",
        default=",".join(DEFAULT_MIGRATION_MODES),
        help=(
            "Comma-separated link modes to convert "
            f"(default: {','.join(DEFAULT_MIGRATION_MODES)}). imported_url requires "
            "explicit opt-in and is migrated only when locally validated as one file."
        ),
    )
    parser.add_argument(
        "--dest",
        default=None,
        help="Directory for linked files (default: <zotero data dir>/linked-attachments).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Migrate at most N attachments — use for a small first batch.",
    )
    parser.add_argument(
        "--include-local-only",
        action="store_true",
        help=(
            "Also convert imported attachments the server holds no file for. "
            "These cost no quota, so this frees nothing; off by default."
        ),
    )
    parser.add_argument(
        "--no-trash",
        action="store_true",
        help="Create the linked files but leave the originals in place.",
    )
    parser.add_argument(
        "--empty-trash",
        action="store_true",
        help=(
            "Purge-only workflow: permanently delete trash recorded by an earlier "
            "migration invocation. Refuses if the trash holds anything else. IRREVERSIBLE."
        ),
    )
    parser.add_argument(
        "--show",
        type=int,
        default=20,
        help="How many planned moves to list in the report (default 20).",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Log each move.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. Returns a process exit code."""
    import sys

    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    from zotero_mcp.config import get_config
    from zotero_mcp.web_client import WebClient

    cfg = get_config()
    if not cfg.has_web_api:
        print(
            f"Missing {', '.join(cfg.missing_web_vars)}. "
            "Get an API key at https://www.zotero.org/settings/keys",
            file=sys.stderr,
        )
        return 2

    modes = tuple(m.strip() for m in args.modes.split(",") if m.strip())
    unknown = [m for m in modes if m not in MIGRATABLE_MODES]
    if unknown:
        print(
            f"Unknown link mode(s): {', '.join(unknown)}. Valid: {', '.join(MIGRATABLE_MODES)}",
            file=sys.stderr,
        )
        return 2

    def progress(idx: int, total: int, move: PlannedMove) -> None:
        print(f"  [{idx}/{total}] {move.action} {move.record.key} {move.record.filename[:60]}")

    with WebClient(cfg.zotero_api_key, cfg.zotero_user_id) as web:
        try:
            if args.empty_trash:
                storage_dir = os.path.join(cfg.effective_zotero_data_dir, "storage")
                dest_dir = _validate_dest_dir(
                    args.dest or cfg.effective_linked_attachment_dir,
                    storage_dir,
                )
                trash_report = empty_recorded_trash(
                    web,
                    dest_dir,
                    dry_run=not args.apply,
                )
                print("Zotero attachment migration — RECORDED TRASH")
                print(f"  status          : {trash_report.get('status')}")
                if trash_report.get("message"):
                    print(f"  {trash_report['message']}")
                if trash_report.get("unexpected"):
                    print(f"  unexpected keys : {', '.join(trash_report['unexpected'][:10])}")
                return 0
            plan, result, trash_report = run_migration(
                web,
                dry_run=not args.apply,
                modes=modes,
                dest_dir=args.dest,
                quota_only=not args.include_local_only,
                limit=args.limit,
                trash=not args.no_trash,
                empty_trash=False,
                on_progress=progress if args.apply else None,
            )
        except MigrationAbort as exc:
            print(f"ABORTED: {exc}", file=sys.stderr)
            return 1

    print(render_plan(plan, show=args.show) if not args.apply else "")

    if args.apply:
        print("Zotero attachment migration — APPLIED")
        print(f"  migrated       : {len(result.migrated)}")
        print(f"  failed         : {len(result.failed)}")
        print(f"  bytes written  : {_mb(result.bytes_written)}")
        print(f"  trashed        : {len(result.trashed)}")
        for outcome in result.failed:
            print(f"    FAILED {outcome.key} {outcome.filename[:50]}: {outcome.error}")
        for note in result.notes:
            print(f"  note: {note}")
        if trash_report:
            print(f"  trash          : {trash_report.get('status')}")
            if trash_report.get("message"):
                print(f"    {trash_report['message']}")
        if result.trashed:
            print(
                "\n  Quota is not reclaimed until the trash is emptied. Review it in "
                "Zotero, then re-run with --apply --empty-trash (and the same --dest, "
                "if customized), or empty it from the Zotero UI."
            )
        return 1 if result.failed or result.trash_failed else 0

    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
