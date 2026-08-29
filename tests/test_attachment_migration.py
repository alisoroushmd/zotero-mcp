"""Tests for imported_file -> linked_file attachment migration."""

import hashlib
import json
import os

import httpx
import pytest
import respx

from zotero_mcp.attachment_migration import (
    DEFAULT_MIGRATION_MODES,
    MIGRATABLE_MODES,
    AttachmentRecord,
    LibraryAttachments,
    MigrationAbort,
    build_plan,
    check_trash_is_exactly,
    empty_recorded_trash,
    empty_trash_guarded,
    inventory,
    list_trash,
    main,
    migrate,
    render_plan,
    run_migration,
)
from zotero_mcp.config import _reset_config
from zotero_mcp.web_client import WEB_BASE, WebClient

USER_ID = "12345"
API_KEY = "testapikey"
BASE = f"{WEB_BASE}/users/{USER_ID}"

PDF_BYTES = b"%PDF-1.4 pretend this is a real paper" * 4
PDF_MD5 = hashlib.md5(PDF_BYTES).hexdigest()  # noqa: S324 — mirrors Zotero's checksum
HTML_BYTES = b"<html><body>snapshot</body></html>"


def _make_client() -> WebClient:
    return WebClient(api_key=API_KEY, user_id=USER_ID)


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Point the config at a scratch Zotero data dir for every test."""
    data_dir = tmp_path / "Zotero"
    (data_dir / "storage").mkdir(parents=True)
    monkeypatch.setenv("ZOTERO_DATA_DIR", str(data_dir))
    monkeypatch.delenv("ZOTERO_LINKED_ATTACHMENT_DIR", raising=False)
    _reset_config()
    yield data_dir
    _reset_config()


def _attachment(key, mode, *, parent="PARENT01", filename="paper.pdf", md5=None, ctype=None):
    """Build a Web API attachment item envelope."""
    return {
        "key": key,
        "data": {
            "key": key,
            "version": 7,
            "itemType": "attachment",
            "parentItem": parent,
            "linkMode": mode,
            "title": filename,
            "filename": filename,
            "contentType": ctype
            or ("application/pdf" if filename.endswith(".pdf") else "text/html"),
            "md5": md5,
            "mtime": 1700000000000,
        },
    }


def _linked(key, path, *, parent="PARENT01"):
    item = _attachment(key, "linked_file", parent=parent, filename=os.path.basename(path))
    item["data"]["path"] = path
    return item


def _mock_items_page(items, total=None):
    """Mock GET /items so a single page satisfies the paginating inventory.

    Also carries ``Last-Modified-Version``: ``trash_items`` probes the same
    endpoint for the library version before issuing its DELETE.
    """
    respx.get(f"{BASE}/items").mock(
        return_value=httpx.Response(
            200,
            json=items,
            headers={
                "Total-Results": str(total if total is not None else len(items)),
                "Last-Modified-Version": "9",
            },
        )
    )


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------


@respx.mock
def test_inventory_classifies_cloud_only_vs_local(_isolated_config):
    """An attachment with no local file but a server MD5 is 'cloud'; on disk it is 'local'."""
    storage = _isolated_config / "storage"
    (storage / "LOCAL001").mkdir()
    (storage / "LOCAL001" / "here.pdf").write_bytes(PDF_BYTES)

    _mock_items_page(
        [
            _attachment("CLOUD001", "imported_file", filename="gone.pdf", md5=PDF_MD5),
            _attachment("LOCAL001", "imported_file", filename="here.pdf", md5=PDF_MD5),
        ]
    )

    sweep = inventory(_make_client())
    by_key = {r.key: r for r in sweep.imported}

    assert by_key["CLOUD001"].source == "cloud"
    assert by_key["CLOUD001"].local_path == ""
    assert by_key["LOCAL001"].source == "local"
    assert by_key["LOCAL001"].local_size == len(PDF_BYTES)


@respx.mock
def test_inventory_marks_unavailable_when_no_bytes_anywhere(_isolated_config):
    """No local file and no server MD5 means the bytes are simply gone."""
    _mock_items_page([_attachment("GHOST001", "imported_file", filename="x.pdf", md5=None)])
    sweep = inventory(_make_client())
    assert sweep.imported[0].source == "unavailable"
    assert sweep.imported[0].holds_cloud_quota is False


@respx.mock
def test_inventory_records_existing_linked_paths(_isolated_config):
    """linked_file attachments are indexed by parent so re-runs stay idempotent."""
    _mock_items_page(
        [
            _linked("LNK00001", "/tmp/linked/paper.pdf", parent="PARENT01"),
            _attachment("IMP00001", "imported_file", md5=PDF_MD5),
        ]
    )
    sweep = inventory(_make_client())
    assert sweep.linked_paths_by_parent["PARENT01"] == {"/tmp/linked/paper.pdf"}
    assert len(sweep.imported) == 1


@respx.mock
def test_inventory_ignores_linked_url_attachments(_isolated_config):
    """linked_url holds no bytes and is not migratable."""
    _mock_items_page([_attachment("URL00001", "linked_url", filename="")])
    sweep = inventory(_make_client())
    assert sweep.imported == []


@respx.mock
def test_inventory_paginates(_isolated_config):
    """Inventory follows Total-Results across pages."""
    page1 = [_attachment(f"K{i:07d}", "imported_file", md5=PDF_MD5) for i in range(100)]
    page2 = [_attachment("LAST0001", "imported_file", md5=PDF_MD5)]
    respx.get(f"{BASE}/items").mock(
        side_effect=[
            httpx.Response(200, json=page1, headers={"Total-Results": "101"}),
            httpx.Response(200, json=page2, headers={"Total-Results": "101"}),
        ]
    )
    sweep = inventory(_make_client())
    assert len(sweep.imported) == 101


@respx.mock
def test_inventory_rejects_storage_traversal_and_symlink_sources(_isolated_config, tmp_path):
    storage = _isolated_config / "storage"
    (storage / "SAFE0001").mkdir()
    (storage / "outside.pdf").write_bytes(PDF_BYTES)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(PDF_BYTES)
    (storage / "LINK0001").mkdir()
    (storage / "LINK0001" / "paper.pdf").symlink_to(outside)
    escape = _isolated_config / "ESCAPE01"
    escape.mkdir()
    (escape / "paper.pdf").write_bytes(PDF_BYTES)

    _mock_items_page(
        [
            _attachment("SAFE0001", "imported_file", filename="../outside.pdf", md5=PDF_MD5),
            _attachment("LINK0001", "imported_file", filename="paper.pdf", md5=PDF_MD5),
            _attachment("../ESCAPE01", "imported_file", filename="paper.pdf", md5=PDF_MD5),
        ]
    )

    sweep = inventory(_make_client())
    assert all(record.local_path == "" for record in sweep.imported)
    assert all(record.source == "cloud" for record in sweep.imported)


@respx.mock
def test_inventory_validates_only_single_file_imported_url_snapshots(_isolated_config):
    storage = _isolated_config / "storage"
    for key in ("SNAPSAFE", "SNAPMULT"):
        (storage / key).mkdir()
        (storage / key / "snapshot.html").write_bytes(HTML_BYTES)
    (storage / "SNAPMULT" / "image.png").write_bytes(b"png")

    _mock_items_page(
        [
            _attachment("SNAPSAFE", "imported_url", filename="snapshot.html", md5=PDF_MD5),
            _attachment("SNAPMULT", "imported_url", filename="snapshot.html", md5=PDF_MD5),
        ]
    )

    sweep = inventory(_make_client())
    by_key = {record.key: record for record in sweep.imported}
    assert by_key["SNAPSAFE"].single_file_snapshot is True
    assert by_key["SNAPMULT"].single_file_snapshot is False


@respx.mock
def test_inventory_and_trash_reads_use_webclient_retry(monkeypatch):
    monkeypatch.setattr("zotero_mcp.web_client.time.sleep", lambda _seconds: None)
    items_route = respx.get(f"{BASE}/items").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=[], headers={"Total-Results": "0"}),
        ]
    )
    trash_route = respx.get(f"{BASE}/items/trash").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=[], headers={"Total-Results": "0"}),
        ]
    )
    web = _make_client()

    assert inventory(web).imported == []
    assert list_trash(web) == []
    assert items_route.call_count == 2
    assert trash_route.call_count == 2


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def _sweep(records, linked=None):
    return LibraryAttachments(
        imported=records,
        linked_paths_by_parent=linked or {},
        total_attachments=len(records),
    )


def _rec(key, **kw):
    base = dict(
        key=key,
        parent_key="PARENT01",
        link_mode="imported_file",
        filename="paper.pdf",
        content_type="application/pdf",
        title="Paper",
        version=1,
        cloud_md5=PDF_MD5,
        local_path="",
        local_size=0,
    )
    base.update(kw)
    return AttachmentRecord(**base)


def test_plan_marks_download_for_cloud_and_copy_for_local(tmp_path):
    local_file = tmp_path / "on-disk.pdf"
    local_file.write_bytes(PDF_BYTES)
    plan = build_plan(
        _sweep(
            [
                _rec("CLOUD001"),
                _rec(
                    "LOCAL001",
                    filename="on-disk.pdf",
                    local_path=str(local_file),
                    local_size=len(PDF_BYTES),
                ),
            ]
        ),
        dest_dir=str(tmp_path / "linked"),
    )
    actions = {m.record.key: m.action for m in plan.moves}
    assert actions == {"CLOUD001": "download", "LOCAL001": "copy"}
    assert plan.download_count == 1
    assert plan.copy_count == 1
    assert plan.known_bytes == len(PDF_BYTES)


def test_plan_skips_unavailable_attachments(tmp_path):
    plan = build_plan(_sweep([_rec("GHOST001", cloud_md5="")]), dest_dir=str(tmp_path / "l"))
    assert plan.moves == []
    assert "no bytes available" in plan.skipped[0].reason


def test_plan_skips_local_only_unless_requested(tmp_path):
    """An attachment the server holds no file for frees no quota, so it is skipped."""
    f = tmp_path / "snap.html"
    f.write_bytes(HTML_BYTES)
    rec = _rec(
        "LOCALONLY",
        link_mode="imported_file",
        filename="snap.html",
        cloud_md5="",
        local_path=str(f),
        local_size=len(HTML_BYTES),
    )
    quota_only = build_plan(_sweep([rec]), dest_dir=str(tmp_path / "l"))
    assert quota_only.moves == []
    assert "holds no cloud storage" in quota_only.skipped[0].reason

    including = build_plan(_sweep([rec]), dest_dir=str(tmp_path / "l"), quota_only=False)
    assert len(including.moves) == 1


def test_imported_url_requires_explicit_opt_in_and_single_local_file(tmp_path):
    local = tmp_path / "snapshot.html"
    local.write_bytes(HTML_BYTES)
    safe = _rec(
        "SNAP0001",
        link_mode="imported_url",
        filename="snapshot.html",
        local_path=str(local),
        local_size=len(HTML_BYTES),
        single_file_snapshot=True,
    )

    default_plan = build_plan(_sweep([safe]), dest_dir=str(tmp_path / "default"))
    assert default_plan.moves == []
    assert "not selected" in default_plan.skipped[0].reason

    explicit = build_plan(
        _sweep([safe]),
        dest_dir=str(tmp_path / "explicit"),
        modes=("imported_url",),
    )
    assert [move.record.key for move in explicit.moves] == ["SNAP0001"]


@pytest.mark.parametrize("source", ["cloud", "multi_resource"])
def test_imported_url_fails_closed_when_snapshot_shape_is_not_validated(tmp_path, source):
    local_path = ""
    local_size = 0
    if source == "multi_resource":
        local = tmp_path / "snapshot.html"
        local.write_bytes(HTML_BYTES)
        local_path = str(local)
        local_size = len(HTML_BYTES)
    record = _rec(
        "SNAP0001",
        link_mode="imported_url",
        filename="snapshot.html",
        local_path=local_path,
        local_size=local_size,
        single_file_snapshot=False,
    )
    plan = build_plan(
        _sweep([record]),
        dest_dir=str(tmp_path / "linked"),
        modes=("imported_url",),
    )
    assert plan.moves == []
    assert "companion resources could be lost" in plan.skipped[0].reason


def test_plan_respects_mode_filter(tmp_path):
    recs = [
        _rec("PDF00001", link_mode="imported_file"),
        _rec("SNAP0001", link_mode="imported_url", filename="s.html"),
    ]
    plan = build_plan(_sweep(recs), dest_dir=str(tmp_path / "l"), modes=("imported_file",))
    assert [m.record.key for m in plan.moves] == ["PDF00001"]
    assert "not selected" in plan.skipped[0].reason


def test_plan_skips_when_parent_already_linked(tmp_path):
    dest = tmp_path / "linked"
    plan = build_plan(
        _sweep([_rec("IMP00001")], linked={"PARENT01": {str(dest / "paper.pdf")}}),
        dest_dir=str(dest),
    )
    assert plan.moves == []
    assert "already has a linked_file" in plan.skipped[0].reason


def test_plan_disambiguates_same_filename(tmp_path):
    """Two attachments named paper.pdf must not plan to write the same path."""
    plan = build_plan(
        _sweep([_rec("AAAA0001"), _rec("BBBB0001")]),
        dest_dir=str(tmp_path / "linked"),
    )
    paths = [m.dest_path for m in plan.moves]
    assert len(set(paths)) == 2
    assert any(p.endswith("paper-BBBB0001.pdf") for p in paths)


def test_plan_skips_standalone_attachments(tmp_path):
    plan = build_plan(_sweep([_rec("ORPHAN01", parent_key="")]), dest_dir=str(tmp_path / "l"))
    assert plan.moves == []
    assert "no parent item" in plan.skipped[0].reason


def test_plan_honors_limit(tmp_path):
    recs = [_rec(f"K{i:07d}", filename=f"p{i}.pdf") for i in range(5)]
    plan = build_plan(_sweep(recs), dest_dir=str(tmp_path / "l"), limit=2)
    assert len(plan.moves) == 2
    assert sum("--limit" in s.reason for s in plan.skipped) == 3


def test_plan_refuses_destination_inside_zotero_storage(_isolated_config):
    storage = _isolated_config / "storage"
    with pytest.raises(MigrationAbort, match="inside Zotero's storage"):
        build_plan(_sweep([_rec("K0000001")]), dest_dir=str(storage / "linked"))


def test_render_plan_is_readable(tmp_path):
    plan = build_plan(_sweep([_rec("K0000001")]), dest_dir=str(tmp_path / "l"))
    text = render_plan(plan)
    assert "DRY RUN PLAN" in text
    assert "Nothing has been written" in text
    assert "K0000001" in text


# --------------------------------------------------------------------------
# Execution — dry run
# --------------------------------------------------------------------------


@respx.mock
def test_dry_run_writes_nothing_and_calls_nothing(tmp_path):
    """A dry run must not create files, create items, or trash anything."""
    dest = tmp_path / "linked"
    plan = build_plan(_sweep([_rec("CLOUD001")]), dest_dir=str(dest))
    post = respx.post(f"{BASE}/items").mock(return_value=httpx.Response(200, json={}))
    delete = respx.delete(f"{BASE}/items").mock(return_value=httpx.Response(204))

    result = migrate(plan, _make_client(), dry_run=True)

    assert result.dry_run is True
    assert [o.status for o in result.outcomes] == ["would_migrate"]
    assert not dest.exists()
    assert post.call_count == 0
    assert delete.call_count == 0
    assert result.trashed == []


# --------------------------------------------------------------------------
# Execution — apply
# --------------------------------------------------------------------------


def _mock_create_linked(key="NEWLINK1"):
    return respx.post(f"{BASE}/items").mock(
        return_value=httpx.Response(
            200, json={"successful": {"0": {"key": key, "data": {"key": key, "version": 2}}}}
        )
    )


def _mock_trash_ok():
    respx.get(f"{BASE}/items").mock(
        return_value=httpx.Response(200, json=[], headers={"Last-Modified-Version": "9"})
    )
    return respx.delete(f"{BASE}/items").mock(
        return_value=httpx.Response(204, headers={"Last-Modified-Version": "10"})
    )


@respx.mock
def test_apply_copies_local_file_and_creates_link(tmp_path):
    src = tmp_path / "src.pdf"
    src.write_bytes(PDF_BYTES)
    dest = tmp_path / "linked"
    rec = _rec("LOCAL001", filename="src.pdf", local_path=str(src), local_size=len(PDF_BYTES))
    plan = build_plan(_sweep([rec]), dest_dir=str(dest))

    create = _mock_create_linked()
    delete = _mock_trash_ok()

    result = migrate(plan, _make_client(), dry_run=False)

    written = dest / "src.pdf"
    assert written.read_bytes() == PDF_BYTES
    assert src.exists(), "source file must not be removed by the migration"
    assert result.outcomes[0].status == "migrated"
    assert result.outcomes[0].new_attachment_key == "NEWLINK1"
    assert result.outcomes[0].sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
    assert result.trashed == ["LOCAL001"]
    assert create.call_count == 1
    assert delete.call_count == 1

    body = json.loads(create.calls[0].request.content)[0]
    assert body["linkMode"] == "linked_file"
    assert body["path"] == str(written)
    assert body["parentItem"] == "PARENT01"


@respx.mock
def test_apply_downloads_cloud_only_attachment_via_redirect(tmp_path):
    """The 302 to S3 is followed without forwarding the Zotero API key."""
    dest = tmp_path / "linked"
    plan = build_plan(_sweep([_rec("CLOUD001")]), dest_dir=str(dest))

    respx.get(f"{BASE}/items/CLOUD001/file").mock(
        return_value=httpx.Response(302, headers={"Location": "https://files.example.com/blob"})
    )
    s3 = respx.get("https://files.example.com/blob").mock(
        return_value=httpx.Response(200, content=PDF_BYTES)
    )
    _mock_create_linked()
    _mock_trash_ok()

    result = migrate(plan, _make_client(), dry_run=False)

    assert (dest / "paper.pdf").read_bytes() == PDF_BYTES
    assert result.outcomes[0].status == "migrated"
    assert "Zotero-API-Key" not in s3.calls[0].request.headers


@respx.mock
def test_apply_aborts_item_when_download_fails_md5(tmp_path):
    """Corrupt bytes must not produce a link, and the original must not be trashed."""
    dest = tmp_path / "linked"
    plan = build_plan(_sweep([_rec("CLOUD001")]), dest_dir=str(dest))

    respx.get(f"{BASE}/items/CLOUD001/file").mock(
        return_value=httpx.Response(302, headers={"Location": "https://files.example.com/blob"})
    )
    respx.get("https://files.example.com/blob").mock(
        return_value=httpx.Response(200, content=b"corrupted")
    )
    create = _mock_create_linked()

    result = migrate(plan, _make_client(), dry_run=False)

    assert result.outcomes[0].status == "failed"
    assert "server MD5" in result.outcomes[0].error
    assert create.call_count == 0
    assert result.trashed == []
    assert not (dest / "paper.pdf").exists()


@respx.mock
def test_download_failure_redacts_presigned_url_query(tmp_path, caplog):
    secret = "super-secret-signature"
    presigned = f"https://files.example.com/blob?X-Amz-Signature={secret}&token=private"
    plan = build_plan(_sweep([_rec("CLOUD001")]), dest_dir=str(tmp_path / "linked"))
    respx.get(f"{BASE}/items/CLOUD001/file").mock(
        return_value=httpx.Response(302, headers={"Location": presigned})
    )
    respx.get(presigned).mock(return_value=httpx.Response(403, text="denied"))

    result = migrate(plan, _make_client(), dry_run=False)

    assert result.failed
    assert "HTTP 403" in result.failed[0].error
    assert secret not in result.failed[0].error
    assert "X-Amz-Signature" not in result.failed[0].error
    assert secret not in caplog.text
    assert "X-Amz-Signature" not in caplog.text


@respx.mock
def test_apply_does_not_trash_when_link_creation_fails(tmp_path):
    """If the replacement attachment cannot be created, the original stays put."""
    src = tmp_path / "src.pdf"
    src.write_bytes(PDF_BYTES)
    dest = tmp_path / "linked"
    rec = _rec("LOCAL001", filename="src.pdf", local_path=str(src), local_size=len(PDF_BYTES))
    plan = build_plan(_sweep([rec]), dest_dir=str(dest))

    respx.post(f"{BASE}/items").mock(return_value=httpx.Response(500, text="boom"))
    delete = respx.delete(f"{BASE}/items").mock(return_value=httpx.Response(204))

    result = migrate(plan, _make_client(), dry_run=False)

    assert result.outcomes[0].status == "failed"
    assert result.trashed == []
    assert delete.call_count == 0
    assert src.read_bytes() == PDF_BYTES


@respx.mock
def test_apply_does_not_trash_when_creation_response_has_no_replacement_key(tmp_path):
    src = tmp_path / "src.pdf"
    src.write_bytes(PDF_BYTES)
    rec = _rec("LOCAL001", filename="src.pdf", local_path=str(src), local_size=len(PDF_BYTES))
    plan = build_plan(_sweep([rec]), dest_dir=str(tmp_path / "linked"))
    respx.post(f"{BASE}/items").mock(
        return_value=httpx.Response(200, json={"successful": {"0": {"data": {}}}})
    )
    delete = respx.delete(f"{BASE}/items").mock(return_value=httpx.Response(204))

    result = migrate(plan, _make_client(), dry_run=False)

    assert result.failed
    assert "no identifiable replacement attachment" in result.failed[0].error
    assert result.trashed == []
    assert delete.call_count == 0


@respx.mock
def test_apply_continues_past_one_failure(tmp_path):
    """A single bad attachment must not abort the whole run."""
    good = tmp_path / "good.pdf"
    good.write_bytes(PDF_BYTES)
    dest = tmp_path / "linked"
    recs = [
        _rec("BAD00001", filename="bad.pdf"),
        _rec("GOOD0001", filename="good.pdf", local_path=str(good), local_size=len(PDF_BYTES)),
    ]
    plan = build_plan(_sweep(recs), dest_dir=str(dest))

    respx.get(f"{BASE}/items/BAD00001/file").mock(return_value=httpx.Response(404))
    _mock_create_linked()
    _mock_trash_ok()

    result = migrate(plan, _make_client(), dry_run=False)

    statuses = {o.key: o.status for o in result.outcomes}
    assert statuses == {"BAD00001": "failed", "GOOD0001": "migrated"}
    assert result.trashed == ["GOOD0001"]
    assert any("failed" in n for n in result.notes)


@respx.mock
def test_apply_with_no_trash_leaves_originals(tmp_path):
    src = tmp_path / "src.pdf"
    src.write_bytes(PDF_BYTES)
    dest = tmp_path / "linked"
    rec = _rec("LOCAL001", filename="src.pdf", local_path=str(src), local_size=len(PDF_BYTES))
    plan = build_plan(_sweep([rec]), dest_dir=str(dest))
    _mock_create_linked()
    delete = respx.delete(f"{BASE}/items").mock(return_value=httpx.Response(204))

    result = migrate(plan, _make_client(), dry_run=False, trash=False)

    assert result.outcomes[0].status == "migrated"
    assert result.trashed == []
    assert delete.call_count == 0
    assert any("left in place" in n for n in result.notes)


def test_write_and_verify_detects_disk_corruption(tmp_path, monkeypatch):
    """If the bytes on disk differ from the source, migration must refuse."""
    from zotero_mcp import attachment_migration as am

    dest = tmp_path / "out.pdf"

    original = am.Path.read_bytes

    def fake_read(self):
        if self == dest:
            return b"not what we wrote"
        return original(self)

    monkeypatch.setattr(am.Path, "read_bytes", fake_read)
    with pytest.raises(MigrationAbort, match="Hash mismatch"):
        am._write_and_verify(PDF_BYTES, dest)


# --------------------------------------------------------------------------
# Trash guard
# --------------------------------------------------------------------------


@respx.mock
def test_list_trash_returns_items():
    respx.get(f"{BASE}/items/trash").mock(
        return_value=httpx.Response(
            200,
            json=[_attachment("TRASH001", "imported_file")],
            headers={"Total-Results": "1"},
        )
    )
    items = list_trash(_make_client())
    assert items[0]["key"] == "TRASH001"


@respx.mock
def test_check_trash_is_exactly_flags_unexpected_items():
    respx.get(f"{BASE}/items/trash").mock(
        return_value=httpx.Response(
            200,
            json=[
                _attachment("MINE0001", "imported_file"),
                _attachment("THEIRS01", "imported_file", filename="someone-elses.pdf"),
            ],
            headers={"Total-Results": "2"},
        )
    )
    check = check_trash_is_exactly(_make_client(), ["MINE0001"])
    assert check["safe"] is False
    assert check["unexpected"] == ["THEIRS01"]


@respx.mock
def test_empty_trash_guarded_refuses_when_trash_has_other_items():
    """The global DELETE /items/trash must never fire on a trash we don't own."""
    respx.get(f"{BASE}/items/trash").mock(
        return_value=httpx.Response(
            200,
            json=[_attachment("THEIRS01", "imported_file")],
            headers={"Total-Results": "1"},
        )
    )
    purge = respx.delete(f"{BASE}/items/trash").mock(return_value=httpx.Response(204))

    with pytest.raises(MigrationAbort, match="Refusing to empty the trash"):
        empty_trash_guarded(_make_client(), ["MINE0001"], dry_run=False)
    assert purge.call_count == 0


@respx.mock
def test_empty_trash_guarded_dry_run_does_not_delete():
    respx.get(f"{BASE}/items/trash").mock(
        return_value=httpx.Response(
            200, json=[_attachment("MINE0001", "imported_file")], headers={"Total-Results": "1"}
        )
    )
    purge = respx.delete(f"{BASE}/items/trash").mock(return_value=httpx.Response(204))

    report = empty_trash_guarded(_make_client(), ["MINE0001"], dry_run=True)

    assert report["status"] == "would_empty"
    assert report["safe"] is True
    assert purge.call_count == 0


@respx.mock
def test_empty_trash_guarded_applies_when_trash_matches():
    respx.get(f"{BASE}/items/trash").mock(
        return_value=httpx.Response(
            200, json=[_attachment("MINE0001", "imported_file")], headers={"Total-Results": "1"}
        )
    )
    respx.get(f"{BASE}/items").mock(
        return_value=httpx.Response(200, json=[], headers={"Last-Modified-Version": "12"})
    )
    purge = respx.delete(f"{BASE}/items/trash").mock(return_value=httpx.Response(204))

    report = empty_trash_guarded(_make_client(), ["MINE0001"], dry_run=False)

    assert report["status"] == "emptied"
    assert purge.call_count == 1


@respx.mock
def test_empty_trash_tolerates_already_missing_expected_key():
    """A key restored from the trash by the user is reported, not fatal."""
    respx.get(f"{BASE}/items/trash").mock(
        return_value=httpx.Response(200, json=[], headers={"Total-Results": "0"})
    )
    check = check_trash_is_exactly(_make_client(), ["MINE0001"])
    assert check["safe"] is True
    assert check["missing"] == ["MINE0001"]


# --------------------------------------------------------------------------
# run_migration orchestration
# --------------------------------------------------------------------------


@respx.mock
def test_run_migration_dry_run_skips_trash_entirely(_isolated_config):
    _mock_items_page([_attachment("CLOUD001", "imported_file", md5=PDF_MD5)])
    purge = respx.delete(f"{BASE}/items/trash").mock(return_value=httpx.Response(204))

    plan, result, trash_report = run_migration(_make_client(), dry_run=True, empty_trash=True)

    assert len(plan.moves) == 1
    assert result.dry_run is True
    assert trash_report["status"] == "skipped"
    assert purge.call_count == 0


@respx.mock
def test_run_migration_refuses_to_empty_trash_after_partial_failure(_isolated_config):
    """A half-migrated library must never have its trash purged."""
    _mock_items_page(
        [
            _attachment("CLOUD001", "imported_file", filename="a.pdf", md5=PDF_MD5),
            _attachment("CLOUD002", "imported_file", filename="b.pdf", md5=PDF_MD5),
        ]
    )
    respx.get(f"{BASE}/items/CLOUD001/file").mock(
        return_value=httpx.Response(302, headers={"Location": "https://files.example.com/a"})
    )
    respx.get("https://files.example.com/a").mock(
        return_value=httpx.Response(200, content=PDF_BYTES)
    )
    respx.get(f"{BASE}/items/CLOUD002/file").mock(return_value=httpx.Response(500))
    _mock_create_linked()
    respx.delete(f"{BASE}/items").mock(
        return_value=httpx.Response(204, headers={"Last-Modified-Version": "11"})
    )
    purge = respx.delete(f"{BASE}/items/trash").mock(return_value=httpx.Response(204))

    _plan, result, trash_report = run_migration(_make_client(), dry_run=False, empty_trash=True)

    assert len(result.failed) == 1
    assert trash_report["status"] == "skipped"
    assert "refusing to empty the trash" in trash_report["message"]
    assert purge.call_count == 0


@respx.mock
def test_cli_empty_trash_uses_persisted_keys_across_two_invocations(_isolated_config, monkeypatch):
    """A later --empty-trash invocation owns only keys journaled by migration."""
    monkeypatch.setenv("ZOTERO_API_KEY", API_KEY)
    monkeypatch.setenv("ZOTERO_USER_ID", USER_ID)
    _reset_config()
    storage = _isolated_config / "storage"
    (storage / "LOCAL001").mkdir()
    (storage / "LOCAL001" / "paper.pdf").write_bytes(PDF_BYTES)
    _mock_items_page([_attachment("LOCAL001", "imported_file", filename="paper.pdf", md5=PDF_MD5)])
    create = _mock_create_linked()
    trash = respx.delete(f"{BASE}/items").mock(
        return_value=httpx.Response(204, headers={"Last-Modified-Version": "10"})
    )

    assert main(["--apply"]) == 0
    state_path = _isolated_config / "linked-attachments" / ".zotero-attachment-migration-trash.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["library_user_id"] == USER_ID
    assert state["trashed_attachment_keys"] == ["LOCAL001"]
    assert create.call_count == 1
    assert trash.call_count == 1

    respx.get(f"{BASE}/items/trash").mock(
        return_value=httpx.Response(
            200,
            json=[_attachment("LOCAL001", "imported_file")],
            headers={"Total-Results": "1"},
        )
    )
    purge = respx.delete(f"{BASE}/items/trash").mock(return_value=httpx.Response(204))

    assert main(["--apply", "--empty-trash"]) == 0
    assert purge.call_count == 1
    assert not state_path.exists()


@respx.mock
def test_empty_recorded_trash_refuses_state_from_another_library(tmp_path):
    state_path = tmp_path / ".zotero-attachment-migration-trash.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "library_user_id": "someone-else",
                "trashed_attachment_keys": ["MINE0001"],
            }
        )
    )
    purge = respx.delete(f"{BASE}/items/trash").mock(return_value=httpx.Response(204))

    with pytest.raises(MigrationAbort, match="different Zotero library"):
        empty_recorded_trash(_make_client(), str(tmp_path), dry_run=False)
    assert purge.call_count == 0


def test_migratable_modes_are_the_quota_holding_ones():
    assert MIGRATABLE_MODES == ("imported_file", "imported_url")
    assert DEFAULT_MIGRATION_MODES == ("imported_file",)
