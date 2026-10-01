"""Tests for resolver metadata normalization (ZOT-44).

Covers preprint repository inference, CrossRef/PubMed date completeness,
publication-title canonicalization, and cross-source enrichment.
"""

from __future__ import annotations

import pytest

from zotero_mcp.web_client import (
    WebClient,
    _normalize_publication_title,
    _parse_medline_date,
)

# ---------------------------------------------------------------------------
# 1. Preprint repository
# ---------------------------------------------------------------------------


def _posted_content(**extra) -> dict:
    work = {
        "type": "posted-content",
        "title": ["A preprint about gastric cancer risk"],
        "author": [{"family": "Soroush", "given": "Ali"}],
        "issued": {"date-parts": [[2026, 7, 1]]},
    }
    work.update(extra)
    return work


def test_repository_from_crossref_institution_medrxiv():
    """The medRxiv server name comes from `institution`, not `group-title`."""
    work = _posted_content(
        institution=[{"name": "medRxiv"}],
        **{"group-title": "Gastroenterology"},
    )
    result = WebClient._parse_crossref_work(work, "10.64898/2026.07.01.26356993")
    assert result["itemType"] == "preprint"
    assert result["repository"] == "medRxiv"
    assert "Gastroenterology" not in str(result)


def test_repository_from_crossref_institution_biorxiv():
    """bioRxiv records resolve to bioRxiv, again ignoring the subject collection."""
    work = _posted_content(
        institution=[{"name": "bioRxiv"}],
        **{"group-title": "Pathology"},
    )
    result = WebClient._parse_crossref_work(work, "10.1101/2024.01.01.123456")
    assert result["repository"] == "bioRxiv"


def test_repository_ignores_group_title_when_institution_absent():
    """Without `institution` and without a usable DOI/URL, no repository is guessed."""
    work = _posted_content(**{"group-title": "Health Informatics"})
    result = WebClient._parse_crossref_work(work, "10.1101/2024.01.01.999999")
    assert "repository" not in result


@pytest.mark.parametrize(
    ("doi", "expected"),
    [
        ("10.48550/arXiv.2501.01234", "arXiv"),
        ("10.48550/arxiv.2501.01234", "arXiv"),
        ("10.2139/ssrn.4123456", "SSRN"),
    ],
)
def test_repository_falls_back_to_doi_prefix(doi, expected):
    """arXiv/SSRN DOI prefixes identify the repository when institution is absent."""
    result = WebClient._parse_crossref_work(_posted_content(), doi)
    assert result["repository"] == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://arxiv.org/abs/2501.01234", "arXiv"),
        ("https://www.medrxiv.org/content/10.1101/2024.01.01.123456v1", "medRxiv"),
        ("https://www.biorxiv.org/content/10.1101/2024.01.01.123456v1", "bioRxiv"),
    ],
)
def test_repository_falls_back_to_url_host(url, expected):
    """The URL host is the last-resort repository signal."""
    result = WebClient._parse_crossref_work(_posted_content(URL=url), "10.1101/x")
    assert result["repository"] == expected


def test_repository_not_set_for_journal_articles():
    """Only preprints get a repository field."""
    work = {
        "type": "journal-article",
        "title": ["An article"],
        "container-title": ["Gastroenterology"],
        "issued": {"date-parts": [[2025, 3, 4]]},
        "institution": [{"name": "Some University"}],
    }
    result = WebClient._parse_crossref_work(work, "10.1/x")
    assert "repository" not in result


# ---------------------------------------------------------------------------
# 2. Dates
# ---------------------------------------------------------------------------


def test_crossref_prefers_most_complete_date_over_first_field():
    """A year-only published-print must not beat a full published-online date."""
    work = {
        "type": "journal-article",
        "title": ["An article"],
        "published-print": {"date-parts": [[2024]]},
        "published-online": {"date-parts": [[2023, 11, 7]]},
        "created": {"date-parts": [[2023, 11, 8]]},
    }
    result = WebClient._parse_crossref_work(work, "10.1/x")
    assert result["date"] == "2023-11-07"


def test_crossref_reads_issued_field():
    """`issued` is considered (it was previously never read at all)."""
    work = {
        "type": "journal-article",
        "title": ["An article"],
        "issued": {"date-parts": [[2022, 5, 9]]},
        "created": {"date-parts": [[2022]]},
    }
    result = WebClient._parse_crossref_work(work, "10.1/x")
    assert result["date"] == "2022-05-09"


def test_crossref_date_ties_prefer_earlier_field():
    """Equally complete dates break toward issued > published-print > ... > created."""
    work = {
        "type": "journal-article",
        "title": ["An article"],
        "issued": {"date-parts": [[2021, 1, 2]]},
        "published-online": {"date-parts": [[2021, 3, 4]]},
    }
    result = WebClient._parse_crossref_work(work, "10.1/x")
    assert result["date"] == "2021-01-02"


def test_crossref_year_only_stays_year_only():
    """When every field is year-only the date remains a bare year."""
    work = {
        "type": "journal-article",
        "title": ["An article"],
        "published-print": {"date-parts": [[1998]]},
    }
    result = WebClient._parse_crossref_work(work, "10.1/x")
    assert result["date"] == "1998"


def test_crossref_date_zero_pads_month_and_day():
    """Month and day are zero-padded to two digits."""
    work = {
        "type": "journal-article",
        "title": ["An article"],
        "issued": {"date-parts": [[2020, 1, 5]]},
    }
    result = WebClient._parse_crossref_work(work, "10.1/x")
    assert result["date"] == "2020-01-05"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2015 Jan-Feb", "2015-01"),
        ("1998 Nov-Dec", "1998-11"),
        ("2001 Spring", "2001"),
        ("", ""),
        ("no year here", ""),
    ],
)
def test_parse_medline_date(text, expected):
    """MedlineDate free text yields at least a year, plus the first month."""
    assert _parse_medline_date(text) == expected


def _pubmed_xml(pub_date_xml: str, journal_title: str = "Frontiers in medicine") -> str:
    return f"""<PubmedArticleSet><PubmedArticle><MedlineCitation>
      <Article>
        <ArticleTitle>Some title.</ArticleTitle>
        <Journal>
          <Title>{journal_title}</Title>
          <JournalIssue>
            <Volume>12</Volume>
            <Issue>3</Issue>
            {pub_date_xml}
          </JournalIssue>
        </Journal>
      </Article>
    </MedlineCitation></PubmedArticle></PubmedArticleSet>"""


def test_pubmed_uses_medline_date_when_year_absent():
    """MedlineDate is parsed when PubDate carries no Year element."""
    xml = _pubmed_xml("<PubDate><MedlineDate>2015 Jan-Feb</MedlineDate></PubDate>")
    result = WebClient._parse_pubmed_xml(xml, "123")
    assert result["date"] == "2015-01"


def test_pubmed_normalizes_month_abbreviation():
    """A normal PubDate month abbreviation becomes a zero-padded number."""
    xml = _pubmed_xml("<PubDate><Year>2024</Year><Month>Mar</Month><Day>7</Day></PubDate>")
    result = WebClient._parse_pubmed_xml(xml, "123")
    assert result["date"] == "2024-03-07"


# ---------------------------------------------------------------------------
# 3. Publication title normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # NLM subtitle after a colon
        (
            "Digestive endoscopy : official journal of the Japan "
            "Gastroenterological Endoscopy Society",
            "Digestive Endoscopy",
        ),
        (
            "Gut microbes : an open access journal for the gut",
            "Gut Microbes",
        ),
        # place-of-publication parentheticals
        ("Gastroenterology (Philadelphia, Pa.)", "Gastroenterology"),
        ("Hepatology (Baltimore, Md.)", "Hepatology"),
        ("Angewandte Chemie (Weinheim, Baden-Wurttemberg, Germany)", "Angewandte Chemie"),
        # ": JAMIA"-style self abbreviation
        (
            "Journal of the American Medical Informatics Association : JAMIA",
            "Journal of the American Medical Informatics Association",
        ),
        # sentence case -> Title Case with lowercase particles
        ("Frontiers in medicine", "Frontiers in Medicine"),
        ("Annals of internal medicine", "Annals of Internal Medicine"),
        # acronym / brand tokens preserved
        ("JAMA network open", "JAMA Network Open"),
        ("BMC medicine", "BMC Medicine"),
        ("npj digital medicine", "npj Digital Medicine"),
        # explicit overrides
        ("medRxiv : the preprint server for health sciences", "medRxiv"),
        ("Lancet regional health. Americas", "The Lancet Regional Health: Americas"),
        ("Cell reports. Medicine", "Cell Reports Medicine"),
        ("JACC. Case reports", "JACC: Case Reports"),
        ("Nature reviews. Disease primers", "Nature Reviews Disease Primers"),
        (
            "Nature reviews. Gastroenterology & hepatology",
            "Nature Reviews Gastroenterology & Hepatology",
        ),
        ("The Lancet. Digital Health", "The Lancet Digital Health"),
        (
            "Mayo Clinic proceedings. Digital health",
            "Mayo Clinic Proceedings: Digital Health",
        ),
        ("BMJ (Clinical research ed.)", "BMJ"),
        ("Lancet (London, England)", "The Lancet"),
        ("EClinicalMedicine", "eClinicalMedicine"),
        ("NPJ digital medicine", "npj Digital Medicine"),
        ("NPJ precision oncology", "npj Precision Oncology"),
        ("PloS one", "PLoS One"),
        (
            "IEEE transactions on bio-medical engineering",
            "IEEE Transactions on Biomedical Engineering",
        ),
        (
            "AMIA ... Annual Symposium proceedings. AMIA Symposium",
            "AMIA Annual Symposium Proceedings",
        ),
        ("iGIE : innovation, investigation and insights", "iGIE"),
        # already-canonical names are left alone
        ("Gastroenterology", "Gastroenterology"),
        ("The Lancet Digital Health", "The Lancet Digital Health"),
        ("", ""),
    ],
)
def test_normalize_publication_title(raw, expected):
    assert _normalize_publication_title(raw) == expected


def test_crossref_container_title_is_normalized():
    """The CrossRef branch routes container-title through the normalizer."""
    work = {
        "type": "journal-article",
        "title": ["An article"],
        "container-title": ["PloS one"],
        "issued": {"date-parts": [[2020, 1, 1]]},
    }
    result = WebClient._parse_crossref_work(work, "10.1/x")
    assert result["publicationTitle"] == "PLoS One"


def test_pubmed_journal_title_is_normalized():
    """The PubMed branch routes NLM's sentence-case Title through the normalizer."""
    xml = _pubmed_xml("<PubDate><Year>2024</Year></PubDate>", journal_title="Frontiers in medicine")
    result = WebClient._parse_pubmed_xml(xml, "123")
    assert result["publicationTitle"] == "Frontiers in Medicine"


# ---------------------------------------------------------------------------
# 4. Cross-source enrichment
# ---------------------------------------------------------------------------


def make_web_client() -> WebClient:
    return WebClient(api_key="testkey", user_id="123456")


def test_needs_enrichment_year_only_date():
    assert WebClient._needs_enrichment({"date": "2024", "publicationTitle": "X"})
    assert not WebClient._needs_enrichment(
        {
            "date": "2024-01-02",
            "publicationTitle": "X",
            "volume": "1",
            "issue": "2",
            "pages": "3-4",
        }
    )


def test_needs_enrichment_preprint_without_repository():
    assert WebClient._needs_enrichment({"itemType": "preprint", "date": "2024-01-02"})
    assert not WebClient._needs_enrichment(
        {"itemType": "preprint", "date": "2024-01-02", "repository": "medRxiv"}
    )


def test_merge_metadata_only_fills_missing_fields():
    """A populated field is never overwritten; a sparser date is upgraded."""
    primary = {
        "itemType": "journalArticle",
        "date": "2024",
        "publicationTitle": "Gastroenterology",
        "volume": "",
    }
    filled = WebClient._merge_metadata(
        primary,
        {
            "date": "2024-03-01",
            "publicationTitle": "Gastroenterology (Philadelphia, Pa.)",
            "volume": "166",
            "pages": "1-9",
        },
    )
    assert primary["date"] == "2024-03-01"
    assert primary["publicationTitle"] == "Gastroenterology"
    assert primary["volume"] == "166"
    assert primary["pages"] == "1-9"
    assert set(filled) == {"date", "volume", "pages"}


def test_merge_metadata_does_not_downgrade_a_complete_date():
    primary = {"date": "2024-03-01"}
    WebClient._merge_metadata(primary, {"date": "2024"})
    assert primary["date"] == "2024-03-01"


def test_merge_metadata_skips_journal_fields_on_preprints():
    primary = {"itemType": "preprint", "date": "2024-01-02"}
    WebClient._merge_metadata(
        primary, {"publicationTitle": "Some Journal", "volume": "1", "repository": "medRxiv"}
    )
    assert primary["repository"] == "medRxiv"
    assert "publicationTitle" not in primary
    assert "volume" not in primary


def test_enrichment_queries_at_most_one_additional_source(monkeypatch):
    """A sparse PubMed result is backfilled from CrossRef exactly once."""
    client = make_web_client()
    calls: list[str] = []

    def fake_crossref(identifier: str) -> dict:
        calls.append(identifier)
        return {
            "itemType": "preprint",
            "date": "2026-07-01",
            "repository": "medRxiv",
            "DOI": "10.64898/2026.07.01.26356993",
        }

    monkeypatch.setattr(client, "_resolve_via_crossref", fake_crossref)
    metadata = {"itemType": "preprint", "title": "T", "date": "2026"}
    result = client._finalize_metadata(metadata, "10.64898/2026.07.01.26356993", "pubmed")

    assert calls == ["10.64898/2026.07.01.26356993"]
    assert result["date"] == "2026-07-01"
    assert result["repository"] == "medRxiv"


def test_enrichment_is_skipped_when_result_is_complete(monkeypatch):
    client = make_web_client()

    def fail(identifier: str):  # pragma: no cover - must not be called
        raise AssertionError("enrichment should not run")

    monkeypatch.setattr(client, "_resolve_via_crossref", fail)
    metadata = {
        "itemType": "journalArticle",
        "date": "2024-03-01",
        "publicationTitle": "Gastroenterology",
        "volume": "1",
        "issue": "2",
        "pages": "3-4",
    }
    assert client._finalize_metadata(metadata, "10.1/x", "pubmed") is metadata


def test_enrichment_network_error_does_not_fail_the_create(monkeypatch):
    """A failing enrichment lookup leaves the original metadata intact."""
    client = make_web_client()

    def boom(identifier: str):
        raise RuntimeError("network down")

    monkeypatch.setattr(client, "_resolve_via_crossref", boom)
    metadata = {"itemType": "journalArticle", "title": "T", "date": "2024"}
    result = client._finalize_metadata(metadata, "10.1/x", "pubmed")
    assert result["date"] == "2024"


def test_crossref_winner_has_no_further_source(monkeypatch):
    """CrossRef is last in the chain — nothing follows it, so nothing fans out."""
    client = make_web_client()
    monkeypatch.setattr(
        client,
        "_resolve_via_pubmed",
        lambda identifier: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    metadata = {"itemType": "journalArticle", "title": "T", "date": "2024"}
    assert client._finalize_metadata(metadata, "10.1/x", "crossref")["date"] == "2024"


def test_finalize_normalizes_and_infers_repository(monkeypatch):
    """Normalization runs on the merged result, including repository inference."""
    client = make_web_client()
    monkeypatch.setattr(client, "_resolve_via_pubmed", lambda identifier: None)
    metadata = {
        "itemType": "preprint",
        "title": "T",
        "date": "2025-01-02",
        "url": "https://arxiv.org/abs/2501.01234",
    }
    result = client._finalize_metadata(metadata, "10.48550/arXiv.2501.01234", "translate")
    assert result["repository"] == "arXiv"
