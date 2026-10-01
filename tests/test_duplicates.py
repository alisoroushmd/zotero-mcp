"""Tests for duplicate detection — title similarity and audit tool."""

import unittest.mock as mock

import httpx
import respx

from zotero_mcp.web_client import WEB_BASE, WebClient

USER_ID = "12345"
API_KEY = "testapikey"
BASE = f"{WEB_BASE}/users/{USER_ID}"


def _make_client() -> WebClient:
    return WebClient(api_key=API_KEY, user_id=USER_ID)


def _pager(summaries: list[dict]):
    """Serve summary-style fixtures as raw /items/top pages (start/limit honored)."""
    raw = [
        {
            "key": s["key"],
            "data": {
                "key": s["key"],
                "title": s.get("title", ""),
                "DOI": s.get("DOI", ""),
                "date": s.get("date", ""),
                "itemType": s.get("item_type", "journalArticle"),
            },
        }
        for s in summaries
    ]

    def page(collection_key, start, limit):
        return raw[start : start + limit], len(raw)

    return page


def test_check_duplicate_title_finds_match():
    """Title similarity catches case/punctuation variants."""
    client = _make_client()
    existing = [
        {
            "key": "ABC123",
            "title": "Gastric Intestinal Metaplasia Detection: A Systematic Review",
            "DOI": "",
            "creators": "",
            "date": "2024",
            "item_type": "journalArticle",
            "collections": [],
            "tags": [],
            "version": 1,
        }
    ]
    with mock.patch.object(client, "search_items", return_value=existing):
        result = client._check_duplicate_title(
            "Gastric intestinal metaplasia detection: a systematic review"
        )
    assert result is not None
    assert result["key"] == "ABC123"


def test_check_duplicate_title_rejects_dissimilar():
    """Title similarity rejects clearly different papers."""
    client = _make_client()
    existing = [
        {
            "key": "ABC123",
            "title": "Machine Learning for Drug Discovery",
            "DOI": "",
            "creators": "",
            "date": "2024",
            "item_type": "journalArticle",
            "collections": [],
            "tags": [],
            "version": 1,
        }
    ]
    with mock.patch.object(client, "search_items", return_value=existing):
        result = client._check_duplicate_title(
            "Gastric intestinal metaplasia detection: a systematic review"
        )
    assert result is None


@respx.mock
def test_create_item_from_url_detects_duplicate_doi():
    """create_item_from_url checks DOI after URL resolution."""
    # Mock translation server to return item with DOI
    respx.post("https://translate.zotero.org/web").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "itemType": "journalArticle",
                    "title": "Test Paper",
                    "DOI": "10.1234/existing",
                }
            ],
        )
    )

    client = _make_client()
    existing = {"key": "EXIST1", "title": "Test Paper", "DOI": "10.1234/existing"}
    with mock.patch.object(client, "_check_duplicate_doi", return_value=existing):
        result = client.create_item_from_url("https://example.com/paper")

    assert result.get("duplicate") is True
    assert result["key"] == "EXIST1"


@respx.mock
def test_create_item_manual_detects_duplicate_title():
    """create_item_manual checks title similarity when no DOI provided."""
    client = _make_client()
    existing = {
        "key": "EXIST2",
        "title": "Gastric Intestinal Metaplasia Detection",
        "similarity": 0.95,
        "match_type": "title_similarity",
    }
    with (
        mock.patch.object(client, "_check_duplicate_doi", return_value=None),
        mock.patch.object(client, "_check_duplicate_title", return_value=existing),
    ):
        result = client.create_item_manual(
            item_type="journalArticle",
            title="Gastric intestinal metaplasia detection",
        )

    assert result.get("duplicate") is True
    assert result["key"] == "EXIST2"
    assert result["match_type"] == "title_similarity"


@respx.mock
def test_create_item_manual_surfaces_dedup_check_failure():
    """When the dedup check fails transiently, the result flags it (ZOT-26)."""
    client = _make_client()
    respx.post(f"{BASE}/items").mock(
        return_value=httpx.Response(
            200, json={"successful": {"0": {"key": "NEWKEY", "data": {"key": "NEWKEY"}}}}
        )
    )
    # Simulate the duplicate search erroring (timeout/429): the helper sets the
    # flag and returns None (no duplicate found).
    with mock.patch.object(client, "search_items", side_effect=httpx.ReadTimeout("boom")):
        result = client.create_item_manual(
            item_type="journalArticle",
            title="A Brand New Paper Title",
        )

    assert result["key"] == "NEWKEY"
    assert result.get("dedup_check_failed") is True
    assert "duplicate" in result.get("dedup_warning", "").lower()


@respx.mock
def test_create_item_manual_checks_doi_first():
    """create_item_manual checks DOI before title similarity."""
    client = _make_client()
    existing = {"key": "EXIST3", "title": "Test", "DOI": "10.1234/test"}
    with mock.patch.object(client, "_check_duplicate_doi", return_value=existing):
        result = client.create_item_manual(
            item_type="journalArticle",
            title="Different Title Entirely",
            doi="10.1234/test",
        )

    assert result.get("duplicate") is True
    assert result["key"] == "EXIST3"


def test_find_duplicates_groups_by_doi():
    """find_duplicates groups items with identical DOIs."""
    client = _make_client()
    items = [
        {
            "key": "A1",
            "title": "Paper One",
            "DOI": "10.1234/same",
            "date": "2024",
            "item_type": "journalArticle",
            "creators": "",
            "collections": [],
            "tags": [],
            "version": 1,
        },
        {
            "key": "A2",
            "title": "Paper One (copy)",
            "DOI": "10.1234/same",
            "date": "2024",
            "item_type": "journalArticle",
            "creators": "",
            "collections": [],
            "tags": [],
            "version": 2,
        },
        {
            "key": "B1",
            "title": "Unique Paper",
            "DOI": "10.5678/unique",
            "date": "2024",
            "item_type": "journalArticle",
            "creators": "",
            "collections": [],
            "tags": [],
            "version": 3,
        },
    ]
    import unittest.mock as mock

    with mock.patch.object(client, "_top_items_page", side_effect=_pager(items)):
        result = client.find_duplicates(limit=100)

    assert result["total_groups"] >= 1
    doi_groups = [g for g in result["duplicate_groups"] if g["match_type"] == "doi"]
    assert len(doi_groups) == 1
    assert doi_groups[0]["doi"] == "10.1234/same"
    assert len(doi_groups[0]["items"]) == 2


def test_find_duplicates_groups_by_title_similarity():
    """find_duplicates groups items with similar titles (no DOI)."""
    client = _make_client()
    items = [
        {
            "key": "C1",
            "title": "Gastric Intestinal Metaplasia Detection",
            "DOI": "",
            "date": "2024",
            "item_type": "journalArticle",
            "creators": "",
            "collections": [],
            "tags": [],
            "version": 1,
        },
        {
            "key": "C2",
            "title": "Gastric intestinal metaplasia detection: a review",
            "DOI": "",
            "date": "2024",
            "item_type": "journalArticle",
            "creators": "",
            "collections": [],
            "tags": [],
            "version": 2,
        },
        {
            "key": "D1",
            "title": "Completely Different Topic",
            "DOI": "",
            "date": "2024",
            "item_type": "journalArticle",
            "creators": "",
            "collections": [],
            "tags": [],
            "version": 3,
        },
    ]
    import unittest.mock as mock

    with mock.patch.object(client, "_top_items_page", side_effect=_pager(items)):
        result = client.find_duplicates(limit=100)

    title_groups = [g for g in result["duplicate_groups"] if g["match_type"] == "title_similarity"]
    assert len(title_groups) == 1
    assert len(title_groups[0]["items"]) == 2


def _rec(key, title, item_type="journalArticle", doi=""):
    return {
        "key": key,
        "title": title,
        "DOI": doi,
        "date": "2026",
        "item_type": item_type,
        "creators": "",
        "collections": [],
        "tags": [],
        "version": 1,
    }


def test_find_duplicates_excludes_child_attachments_and_notes():
    """Child attachments/notes must never form duplicate groups.

    Regression for the 2026-08-30 hygiene audit: generic attachment titles
    ("Snapshot", "arXiv.org Snapshot", "PubMed entry") are byte-identical, so
    they trivially clear the 0.85 similarity threshold and produced 3 groups /
    50 items that crowded out a real DOI duplicate.
    """
    client = _make_client()
    items = [
        _rec("A1", "Snapshot", "attachment"),
        _rec("A2", "Snapshot", "attachment"),
        _rec("A3", "arXiv.org Snapshot", "attachment"),
        _rec("A4", "arXiv.org Snapshot", "attachment"),
        _rec("N1", "PubMed entry", "note"),
        _rec("N2", "PubMed entry", "note"),
        _rec("J1", "A Unique Paper About Gastric Metaplasia", "journalArticle"),
    ]
    with mock.patch.object(client, "_top_items_page", side_effect=_pager(items)):
        result = client.find_duplicates(limit=100)

    assert result["total_groups"] == 0, result["duplicate_groups"]
    assert result["total_duplicate_items"] == 0


def test_find_duplicates_still_reports_real_doi_dupes_amid_attachments():
    """A real DOI duplicate must survive alongside attachment noise."""
    client = _make_client()
    items = [_rec(f"A{i}", "Snapshot", "attachment") for i in range(20)]
    items += [
        _rec("R1", "Use of LLMs to Determine the Surveillance Interval", doi="10.1/abc"),
        _rec("R2", "Use of LLMs to Determine the Surveillance Interval", doi="10.1/abc"),
    ]
    with mock.patch.object(client, "_top_items_page", side_effect=_pager(items)):
        result = client.find_duplicates(limit=100)

    doi_groups = [g for g in result["duplicate_groups"] if g["match_type"] == "doi"]
    assert len(doi_groups) == 1
    assert {i["key"] for i in doi_groups[0]["items"]} == {"R1", "R2"}


def test_read_item_never_uses_local_version():
    """_read_item must read via the Web API, not the local database.

    The returned version is sent as If-Unmodified-Since-Version on the next
    PATCH. The local DB keeps its own counter (0/1/2) unrelated to the cloud
    version, so a local read made every read-modify-write fail with HTTP 412
    whenever Zotero desktop was running.
    """
    client = _make_client()
    local = mock.Mock()
    local.get_item.return_value = {"key": "K1", "version": 2, "collections": []}
    client._local = local

    with mock.patch.object(
        client, "get_item", return_value={"key": "K1", "version": 8880, "collections": []}
    ) as web:
        item = client._read_item("K1")

    assert item["version"] == 8880, "must use the cloud version, not the local one"
    local.get_item.assert_not_called()
    web.assert_called_once_with("K1")


# -- find_duplicates regressions (2026-10-01 live false negative) --
#
# Live evidence: 1,474 parent items and 1,636 attachments; IWQ94IDC and
# K8US74GH share DOI 10.1097/MCG.0000000000001890, yet find_duplicates
# returned 0 groups at limit=200 and limit=3000. Causes: a single <=100-row
# fetch (no pagination, server clamp of 100), non-bibliographic rows spending
# the budget before being filtered, and title matching restricted to items
# with no DOI.

USMSTF_TITLE = (
    "Recommendations for Follow-Up After Colonoscopy and Polypectomy: A Consensus "
    "Update by the US Multi-Society Task Force on Colorectal Cancer"
)


def _raw(key, title, item_type="journalArticle", doi="", **extra):
    data = {"key": key, "title": title, "itemType": item_type, "DOI": doi, "date": "2020"}
    data.update(extra)
    return {"key": key, "data": data}


def _filler_parents(n: int, prefix: str = "P") -> list[dict]:
    # Random-hex titles so filler never clusters by title.
    import hashlib

    return [
        _raw(f"{prefix}{i:05d}", f"Filler study {hashlib.md5(f'{prefix}{i}'.encode()).hexdigest()}")
        for i in range(n)
    ]


def _mock_items_top(raw_items: list[dict]):
    """respx side effect emulating GET /items/top with itemType=-attachment."""
    calls: list[dict] = []

    def handler(request):
        params = dict(request.url.params)
        calls.append(params)
        assert params.get("itemType") == "-attachment"
        visible = [r for r in raw_items if r["data"]["itemType"] != "attachment"]
        start, limit = int(params.get("start", 0)), int(params.get("limit", 25))
        return httpx.Response(
            200,
            json=visible[start : start + limit],
            headers={"Total-Results": str(len(visible))},
        )

    return handler, calls


@respx.mock
def test_find_duplicates_same_doi_pair_hidden_behind_notes_and_attachments():
    """A same-DOI pair deep in the library is found: limit bounds parent items.

    320 standalone notes/annotations precede 250 parents; the DOI pair sits at
    parents 180 and 240 with differently formatted DOIs. The old single-page
    fetch saw only the first 100 rows (all notes) and reported nothing.
    """
    noise = [_raw(f"N{i:04d}", "PubMed entry", "note") for i in range(300)]
    noise += [_raw(f"X{i:04d}", "Highlight", "annotation") for i in range(20)]
    noise += [_raw(f"T{i:04d}", "Snapshot", "attachment") for i in range(200)]
    parents = _filler_parents(250)
    parents[180] = _raw(
        "IWQ94IDC", "Use of LLMs in surveillance intervals", doi="10.1097/MCG.0000000000001890"
    )
    parents[240] = _raw(
        "K8US74GH",
        "Use of LLMs in surveillance intervals (accepted manuscript)",
        doi="https://doi.org/10.1097/mcg.0000000000001890",
    )
    handler, calls = _mock_items_top(noise + parents)
    respx.get(f"{BASE}/items/top").mock(side_effect=handler)

    result = _make_client().find_duplicates(limit=250)

    doi_groups = [g for g in result["duplicate_groups"] if g["match_type"] == "doi"]
    assert len(doi_groups) == 1
    assert doi_groups[0]["doi"] == "10.1097/mcg.0000000000001890"
    assert {i["key"] for i in doi_groups[0]["items"]} == {"IWQ94IDC", "K8US74GH"}
    assert result["scanned_items"] == 250
    assert result["truncated"] is False
    assert result["source"] == "web"
    assert len(calls) == 6  # 570 visible rows / 100 per page


@respx.mock
def test_find_duplicates_limit_counts_parent_records_and_reports_truncation():
    """limit=100 scans 100 *parent* records even after 300 notes, and says so."""
    noise = [_raw(f"N{i:04d}", "PubMed entry", "note") for i in range(300)]
    handler, _ = _mock_items_top(noise + _filler_parents(150))
    respx.get(f"{BASE}/items/top").mock(side_effect=handler)

    result = _make_client().find_duplicates(limit=100)

    assert result["scanned_items"] == 100
    assert result["truncated"] is True
    assert result["total_available"] == 450


@respx.mock
def test_find_duplicates_skips_trashed_and_child_rows():
    """Defensive client-side filter: deleted or parented rows never group."""
    rows = [
        _raw("D1", USMSTF_TITLE, doi="10.1016/j.gie.2020.01.014", deleted=1),
        _raw("C1", USMSTF_TITLE, doi="10.1016/j.gie.2020.01.014", parentItem="ZZZ"),
        _raw("R1", USMSTF_TITLE, doi="10.1016/j.gie.2020.01.014"),
    ]
    handler, _ = _mock_items_top(rows)
    respx.get(f"{BASE}/items/top").mock(side_effect=handler)

    result = _make_client().find_duplicates()

    assert result["total_groups"] == 0
    assert result["scanned_items"] == 1


def test_find_duplicates_flags_same_title_two_dois_as_possible_dual_publication():
    """USMSTF polypectomy follow-up guideline: GIE and Gastroenterology DOIs."""
    client = _make_client()
    items = [
        {"key": "GIE00001", "title": USMSTF_TITLE, "DOI": "10.1016/j.gie.2020.01.014"},
        {"key": "GAS00001", "title": USMSTF_TITLE, "DOI": "10.1053/J.GASTRO.2019.10.026"},
        {"key": "OTHER001", "title": "Gastric intestinal metaplasia surveillance in the US"},
    ]
    with mock.patch.object(client, "_top_items_page", side_effect=_pager(items)):
        result = client.find_duplicates()

    title_groups = [g for g in result["duplicate_groups"] if g["match_type"] == "title_similarity"]
    assert len(title_groups) == 1
    group = title_groups[0]
    assert {i["key"] for i in group["items"]} == {"GIE00001", "GAS00001"}
    assert group["confidence"] == "possible_dual_publication"
    assert group["dois"] == ["10.1016/j.gie.2020.01.014", "10.1053/j.gastro.2019.10.026"]
    assert result["possible_dual_publication_groups"] == 1
    assert not [g for g in result["duplicate_groups"] if g["match_type"] == "doi"]


def test_find_duplicates_same_title_one_doi_one_missing_is_likely_duplicate():
    """A DOI-less copy of a DOI-bearing record is a likely duplicate."""
    client = _make_client()
    items = [
        {"key": "GIE00001", "title": USMSTF_TITLE, "DOI": "10.1016/j.gie.2020.01.014"},
        {"key": "NODOI001", "title": USMSTF_TITLE.lower()},
    ]
    with mock.patch.object(client, "_top_items_page", side_effect=_pager(items)):
        result = client.find_duplicates()

    assert result["total_groups"] == 1
    assert result["duplicate_groups"][0]["confidence"] == "likely_duplicate"


def test_find_duplicates_does_not_group_titles_differing_only_by_year():
    """Negative control: GLOBOCAN 2020 and GLOBOCAN 2022 are distinct works."""
    tail = (
        ": GLOBOCAN Estimates of Incidence and Mortality Worldwide for 36 Cancers in 185 Countries"
    )
    for with_dois in (True, False):
        client = _make_client()
        items = [
            {
                "key": "GLOB2020",
                "title": "Global Cancer Statistics 2020" + tail,
                "DOI": "10.3322/caac.21660" if with_dois else "",
            },
            {
                "key": "GLOB2022",
                "title": "Global cancer statistics 2022" + tail.lower(),
                "DOI": "10.3322/caac.21834" if with_dois else "",
            },
        ]
        with mock.patch.object(client, "_top_items_page", side_effect=_pager(items)):
            result = client.find_duplicates()
        assert result["total_groups"] == 0, (with_dois, result["duplicate_groups"])


def test_find_duplicates_falls_back_to_web_when_local_scan_fails():
    """A failing local desktop page restarts the scan on the Web API."""
    client = _make_client()
    local = mock.Mock()
    local.top_items_page.side_effect = RuntimeError("desktop not running")
    client._local = local
    items = [
        {"key": "A1", "title": "Paper", "DOI": "10.1/X"},
        {"key": "A2", "title": "Paper copy", "DOI": "10.1/x"},
    ]
    with mock.patch.object(client, "_top_items_page", side_effect=_pager(items)):
        result = client.find_duplicates()

    assert result["source"] == "web"
    assert result["total_groups"] == 1


def test_find_duplicates_reads_doi_from_extra_for_types_without_doi_field():
    """bookSection keeps its DOI in Extra; it must still match by DOI."""
    from zotero_mcp.web_client import _dedup_record

    rec = _dedup_record(
        {"key": "B1", "itemType": "bookSection", "extra": "Citation Key: x\nDOI: 10.1/ABC"}
    )
    assert rec["doi"] == "10.1/abc"


def test_title_clustering_stays_fast_on_5000_items():
    """Blocking keeps a 5,000-record library well under a few seconds."""
    import random
    import time as _time

    from zotero_mcp.web_client import _cluster_by_title

    rng = random.Random(7)
    vocab = [f"term{i}" for i in range(400)]
    records = [
        {
            "key": f"K{i:05d}",
            "title": " ".join(rng.choice(vocab) for _ in range(rng.randint(6, 14))),
            "date": "",
            "doi": f"10.1/{i}",
        }
        for i in range(5000)
    ]
    records.append(dict(records[10], key="DUPKEY01", doi="10.1/other"))

    t0 = _time.perf_counter()
    groups = _cluster_by_title(records, 0.85)
    elapsed = _time.perf_counter() - t0

    assert any({i["key"] for i in g["items"]} >= {"K00010", "DUPKEY01"} for g in groups)
    assert elapsed < 10.0, f"title clustering took {elapsed:.1f}s"
