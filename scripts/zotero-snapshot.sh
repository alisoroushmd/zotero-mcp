#!/usr/bin/env bash
# Zotero SQLite snapshot for the Claude Code PreToolUse hook.
#
# Usage: zotero-snapshot.sh [reason]
#   reason: short tag (e.g. the MCP tool name) recorded in the filename.
# Env overrides: ZOTERO_DB, SNAPSHOT_DIR, RETENTION_DAYS, SQLITE3.
#
# Exit codes (Claude Code hook semantics):
#   0  verified, non-empty snapshot written; path printed on stderr
#   2  snapshot could NOT be taken or verified; the tool call is BLOCKED.
#      (Exit 1 is a *non-blocking* error to Claude Code, which is why the
#      previous version of this script let 909 permanent deletes through on
#      2026-09-16 while leaving 0-byte "snapshots" behind.)
#
# Why not just `sqlite3 src ".backup dest"`?
#   Zotero desktop opens its database with locking_mode=EXCLUSIVE. While it is
#   running, every other connection (including the Backup API and VACUUM INTO)
#   fails with "database is locked", but sqlite3 has already created the empty
#   destination file, and `PRAGMA integrity_check` on a 0-byte file prints "ok".
#   Opening with ?immutable=1 avoids the lock but ignores the WAL, so the copy
#   is stale by everything not yet checkpointed (observed 147 attachments and
#   147 items behind on 2026-09-16).
#
# Strategy:
#   1. Try the Backup API (correct and cheap when Zotero is not running).
#   2. If locked, copy the main file + WAL to a private temp dir, let SQLite
#      replay the WAL on the copy (it rebuilds the -shm index itself), then
#      checkpoint into a single self-contained file. The copy is retried if the
#      source files changed underneath us.
#   3. Verify: file non-empty, valid SQLite header, integrity_check == ok,
#      items table readable and non-empty. Anything else -> cleanup, exit 2.
set -uo pipefail

ZOTERO_DB="${ZOTERO_DB:-$HOME/Zotero/zotero.sqlite}"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-$HOME/Zotero/_snapshots}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
SQLITE3="${SQLITE3:-}"
REASON="${1:-manual}"

fail() {
  echo "zotero-snapshot: BLOCKED: $*" >&2
  exit 2
}

REASON_SAFE=$(printf '%s' "$REASON" | tr -c 'a-zA-Z0-9_-' '_' | cut -c1-64)
[[ -z "$REASON_SAFE" ]] && REASON_SAFE=manual

# Locate sqlite3 even under a minimal hook PATH.
if [[ -z "$SQLITE3" ]]; then
  for cand in "$(command -v sqlite3 2>/dev/null || true)" /opt/homebrew/opt/sqlite/bin/sqlite3 /opt/homebrew/bin/sqlite3 /usr/local/bin/sqlite3 /usr/bin/sqlite3; do
    if [[ -n "$cand" && -x "$cand" ]]; then SQLITE3="$cand"; break; fi
  done
fi
[[ -n "$SQLITE3" && -x "$SQLITE3" ]] || fail "sqlite3 binary not found (PATH=$PATH)"

[[ -f "$ZOTERO_DB" ]] || fail "source DB not found: $ZOTERO_DB"
mkdir -p "$SNAPSHOT_DIR" || fail "cannot create $SNAPSHOT_DIR"

TS=$(date +%Y%m%d-%H%M%S)
DEST="$SNAPSHOT_DIR/${TS}_${REASON_SAFE}.sqlite"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/zotero-snapshot.XXXXXX") || fail "cannot create temp dir"

cleanup() {
  rm -rf "$WORK"
}
abort() {
  rm -f "$DEST" "$DEST-wal" "$DEST-shm"
  cleanup
  fail "$@"
}
trap cleanup EXIT

# --- Step 1: Backup API (works when nothing holds an exclusive lock). --------
method=""
if err=$("$SQLITE3" "$ZOTERO_DB" ".backup '$DEST'" 2>&1) && [[ -s "$DEST" ]]; then
  method="backup-api"
else
  rm -f "$DEST"
  case "$err" in
    *"database is locked"*|*"locked"*|"") : ;;  # expected while Zotero desktop runs
    *) echo "zotero-snapshot: .backup failed ($err); falling back to file copy" >&2 ;;
  esac

  # --- Step 2: copy main file + WAL, replay on the copy. --------------------
  WAL="$ZOTERO_DB-wal"
  copied=0
  for attempt in 1 2 3; do
    before=$(stat -f '%z:%m' "$ZOTERO_DB" 2>/dev/null; [[ -f "$WAL" ]] && stat -f '%z:%m' "$WAL")
    cp "$ZOTERO_DB" "$WORK/db.sqlite" || abort "cp of main database failed"
    if [[ -f "$WAL" ]]; then
      cp "$WAL" "$WORK/db.sqlite-wal" || abort "cp of WAL failed"
    fi
    after=$(stat -f '%z:%m' "$ZOTERO_DB" 2>/dev/null; [[ -f "$WAL" ]] && stat -f '%z:%m' "$WAL")
    if [[ "$before" == "$after" ]]; then copied=1; break; fi
    echo "zotero-snapshot: source changed during copy (attempt $attempt), retrying" >&2
    rm -f "$WORK/db.sqlite" "$WORK/db.sqlite-wal" "$WORK/db.sqlite-shm"
    sleep 0.5
  done
  [[ $copied -eq 1 ]] || abort "source database kept changing during copy"

  # Replay WAL into the copy, then write a single-file snapshot.
  if ! out=$("$SQLITE3" "$WORK/db.sqlite" "PRAGMA wal_checkpoint(TRUNCATE);" 2>&1); then
    abort "WAL replay on copy failed: $out"
  fi
  if ! out=$("$SQLITE3" "$WORK/db.sqlite" ".backup '$DEST'" 2>&1); then
    abort "backup from replayed copy failed: $out"
  fi
  method="copy+wal-replay"
fi

# --- Step 3: verify. ---------------------------------------------------------
[[ -s "$DEST" ]] || abort "snapshot file is empty: $DEST"
# The backup inherits WAL mode from its source; make the snapshot a single
# self-contained file so it can be copied or opened read-only anywhere.
"$SQLITE3" "$DEST" "PRAGMA journal_mode=DELETE;" >/dev/null 2>&1 || abort "could not switch snapshot to rollback journal mode"
rm -f "$DEST-wal" "$DEST-shm"
size=$(stat -f '%z' "$DEST")
[[ "$size" -ge 4096 ]] || abort "snapshot implausibly small ($size bytes): $DEST"
header=$(head -c 15 "$DEST" 2>/dev/null)
[[ "$header" == "SQLite format 3" ]] || abort "snapshot lacks SQLite header: $DEST"

integrity=$("$SQLITE3" "$DEST" "PRAGMA integrity_check;" 2>&1) || abort "integrity_check errored: $integrity"
[[ "$integrity" == "ok" ]] || abort "integrity_check failed: ${integrity:0:200}"

items=$("$SQLITE3" "$DEST" "SELECT count(*) FROM items;" 2>&1) || abort "cannot read items table: $items"
[[ "$items" =~ ^[0-9]+$ && "$items" -gt 0 ]] || abort "snapshot has no items (count=$items)"

# Prune snapshots older than RETENTION_DAYS days, and any stale 0-byte leftovers.
find "$SNAPSHOT_DIR" -maxdepth 1 -name '*.sqlite' -type f -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true
find "$SNAPSHOT_DIR" -maxdepth 1 -name '*.sqlite' -type f -size 0 -delete 2>/dev/null || true

echo "zotero-snapshot: OK ($method, $items items, $size bytes): $DEST" >&2
exit 0
