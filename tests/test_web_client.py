"""Tests for WebClient — write operations via Zotero Web API."""

import httpx
import pytest
import respx

from zotero_mcp.web_client import WebClient

WEB_BASE = "https://api.zotero.org"
TRANSLATE_URL = "https://translate.zotero.org/search"
PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CROSSREF_BASE = "https://api.crossref.org"

# -- Sample PubMed efetch XML for tests --

SAMPLE_EFETCH_XML = """\
<?xml version="1.0" ?>
<PubmedArticleSet>
<PubmedArticle>
  <MedlineCitation>
    <Article>
      <Journal>
        <ISSN>1234-5678</ISSN>
        <JournalIssue>
          <Volume>1</Volume>
          <Issue>2</Issue>
          <PubDate><Year>2024</Year><Month>Mar</Month></PubDate>
        </JournalIssue>
        <Title>Test Journal</Title>
      </Journal>
      <ArticleTitle>Fallback Paper</ArticleTitle>
      <Pagination><MedlinePgn>10-20</MedlinePgn></Pagination>
      <Abstract>
        <AbstractText>This is the abstract text.</AbstractText>
      </Abstract>
      <AuthorList>
        <Author><LastName>Smith</LastName><ForeName>John</ForeName></Author>
        <Author><LastName>Doe</LastName><ForeName>Jane</ForeName></Author>
      </AuthorList>
      <PublicationTypeList>
        <PublicationType>Journal Article</PublicationType>
      </PublicationTypeList>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="doi">10.1234/test</ArticleId>
      <ArticleId IdType="pubmed">12345678</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
</PubmedArticleSet>"""

SAMPLE_PREPRINT_XML = """\
<?xml version="1.0" ?>
<PubmedArticleSet>
<PubmedArticle>
  <MedlineCitation>
    <Article>
      <Journal>
        <JournalIssue>
          <PubDate><Year>2024</Year></PubDate>
        </JournalIssue>
        <Title>bioRxiv</Title>
      </Journal>
      <ArticleTitle>A Preprint Study</ArticleTitle>
      <Abstract>
        <AbstractText Label="BACKGROUND">Background info.</AbstractText>
        <AbstractText Label="METHODS">Methods info.</AbstractText>
      </Abstract>
      <AuthorList>
        <Author><LastName>Lee</LastName><ForeName>Alex</ForeName></Author>
      </AuthorList>
      <PublicationTypeList>
        <PublicationType>Preprint</PublicationType>
      </PublicationTypeList>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="doi">10.1101/2024.01.01.123</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
</PubmedArticleSet>"""

# -- Helpers --

ZOTERO_CREATE_SUCCESS = {
    "successful": {"0": {"key": "NEW123", "data": {"key": "NEW123"}}},
    "success": {"0": "NEW123"},
    "unchanged": {},
    "failed": {},
}


def _mock_zotero_create() -> None:
    """Mock the Zotero Web API item creation endpoint."""
    respx.post(f"{WEB_BASE}/users/12345/items").mock(
        return_value=httpx.Response(200, json=ZOTERO_CREATE_SUCCESS)
    )


# -- Existing tests (updated for efetch) --


@respx.mock
def test_create_item_from_identifier_doi():
    """create_item_from_identifier resolves DOI and creates item."""
    respx.post(TRANSLATE_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "itemType": "journalArticle",
                    "title": "Test Paper From DOI",
                    "creators": [
                        {
                            "creatorType": "author",
                            "firstName": "Jane",
                            "lastName": "Smith",
                        }
                    ],
                    "DOI": "10.1234/test",
                    "date": "2024",
                }
            ],
        )
    )
    _mock_zotero_create()

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_item_from_identifier("10.1234/test")
    assert result["key"] == "NEW123"
    assert result["title"] == "Test Paper From DOI"


@respx.mock
def test_create_item_from_identifier_with_collections_and_tags():
    """create_item_from_identifier applies collections and tags."""
    respx.post(TRANSLATE_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "itemType": "journalArticle",
                    "title": "Tagged Paper",
                    "creators": [],
                    "DOI": "10.5678/tagged",
                }
            ],
        )
    )
    respx.post(f"{WEB_BASE}/users/12345/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "successful": {"0": {"key": "TAG456", "data": {"key": "TAG456"}}},
                "success": {"0": "TAG456"},
                "unchanged": {},
                "failed": {},
            },
        )
    )

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_item_from_identifier(
        "10.5678/tagged",
        collection_keys=["COL1"],
        tags=["oncology", "review"],
    )
    assert result["key"] == "TAG456"

    request = respx.calls[-1].request
    import json

    body = json.loads(request.content)
    assert body[0]["collections"] == ["COL1"]
    assert {"tag": "oncology"} in body[0]["tags"]


@respx.mock
def test_create_item_translation_server_down_falls_back_to_pubmed():
    """Falls back to PubMed efetch when translation server is unavailable."""
    respx.post(TRANSLATE_URL).mock(side_effect=httpx.ConnectError("Connection refused"))
    # Mock PubMed DOI search
    respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(
        return_value=httpx.Response(
            200, json={"esearchresult": {"idlist": ["12345678"]}}
        )
    )
    # Mock PubMed efetch XML (replaces old esummary mock)
    respx.get(f"{PUBMED_BASE}/efetch.fcgi").mock(
        return_value=httpx.Response(200, text=SAMPLE_EFETCH_XML)
    )
    _mock_zotero_create()

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_item_from_identifier("10.1234/test")
    assert result["key"] == "NEW123"
    assert result["title"] == "Fallback Paper"


@respx.mock
def test_create_item_unresolvable_identifier():
    """Raises error when all resolution paths fail."""
    # Translation server returns empty
    respx.post(TRANSLATE_URL).mock(return_value=httpx.Response(200, json=[]))
    # CrossRef returns 404 for non-DOI identifiers (PMID "99999999")
    # PubMed won't match a bare number that's not a real PMID
    respx.get(f"{PUBMED_BASE}/efetch.fcgi").mock(
        return_value=httpx.Response(200, text="<PubmedArticleSet></PubmedArticleSet>")
    )

    client = WebClient(api_key="test-key", user_id="12345")
    with pytest.raises(RuntimeError, match="No metadata found.*99999999"):
        client.create_item_from_identifier("99999999")


@respx.mock
def test_create_item_duplicate_doi_returns_existing():
    """Returns existing item key when DOI already in library."""
    LOCAL_BASE = "http://localhost:23119/api"
    respx.get(f"{LOCAL_BASE}/users/0/items").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "EXISTING1",
                    "data": {
                        "key": "EXISTING1",
                        "itemType": "journalArticle",
                        "title": "Already Here",
                        "DOI": "10.1234/existing",
                        "creators": [],
                        "date": "2024",
                        "collections": [],
                        "tags": [],
                    },
                }
            ],
        )
    )
    respx.post(TRANSLATE_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "itemType": "journalArticle",
                    "title": "Already Here",
                    "DOI": "10.1234/existing",
                    "creators": [],
                }
            ],
        )
    )

    from zotero_mcp.local_client import LocalClient

    local = LocalClient()
    client = WebClient(api_key="test-key", user_id="12345", local_client=local)
    result = client.create_item_from_identifier("10.1234/existing")
    assert result["key"] == "EXISTING1"
    assert result["duplicate"] is True


def test_missing_api_key_raises_error():
    """Missing API key gives clear error with link."""
    with pytest.raises(ValueError, match="ZOTERO_API_KEY.*zotero.org/settings/keys"):
        WebClient(api_key="", user_id="12345")


def test_missing_user_id_raises_error():
    """Missing user ID gives clear error with link."""
    with pytest.raises(ValueError, match="ZOTERO_API_KEY.*zotero.org/settings/keys"):
        WebClient(api_key="test-key", user_id="")


LOCAL_BASE = "http://localhost:23119/api"


@respx.mock
def test_add_to_collection():
    """add_to_collection reads item locally, patches via web API."""
    respx.get(f"{LOCAL_BASE}/users/0/items/ITEM1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ITEM1",
                "version": 10,
                "data": {
                    "key": "ITEM1",
                    "version": 10,
                    "collections": ["COL1"],
                },
            },
        )
    )
    respx.patch(f"{WEB_BASE}/users/12345/items/ITEM1").mock(
        return_value=httpx.Response(204)
    )

    from zotero_mcp.local_client import LocalClient

    local = LocalClient()
    client = WebClient(api_key="test-key", user_id="12345", local_client=local)
    result = client.add_to_collection("ITEM1", "COL2")
    assert "COL1" in result["collections"]
    assert "COL2" in result["collections"]


@respx.mock
def test_update_item():
    """update_item reads locally, patches via web API with version."""
    respx.get(f"{LOCAL_BASE}/users/0/items/ITEM1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ITEM1",
                "version": 10,
                "data": {
                    "key": "ITEM1",
                    "version": 10,
                    "title": "Old Title",
                },
            },
        )
    )
    respx.patch(f"{WEB_BASE}/users/12345/items/ITEM1").mock(
        return_value=httpx.Response(204)
    )

    from zotero_mcp.local_client import LocalClient

    local = LocalClient()
    client = WebClient(api_key="test-key", user_id="12345", local_client=local)
    result = client.update_item("ITEM1", {"title": "New Title"})
    assert result["key"] == "ITEM1"

    request = respx.calls[-1].request
    assert request.headers["If-Unmodified-Since-Version"] == "10"


@respx.mock
def test_update_item_version_conflict():
    """update_item raises clear error on 412 Precondition Failed."""
    respx.get(f"{LOCAL_BASE}/users/0/items/ITEM1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ITEM1",
                "version": 10,
                "data": {"key": "ITEM1", "version": 10, "title": "Old"},
            },
        )
    )
    respx.patch(f"{WEB_BASE}/users/12345/items/ITEM1").mock(
        return_value=httpx.Response(412)
    )

    from zotero_mcp.local_client import LocalClient

    local = LocalClient()
    client = WebClient(api_key="test-key", user_id="12345", local_client=local)
    with pytest.raises(RuntimeError, match="Version conflict.*ITEM1.*retry"):
        client.update_item("ITEM1", {"title": "New"})


@respx.mock
def test_web_api_rate_limit_surfaces_error():
    """Rate limit (429) error is surfaced to the user."""
    respx.post(TRANSLATE_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "itemType": "journalArticle",
                    "title": "Paper",
                    "DOI": "10.1/x",
                    "creators": [],
                }
            ],
        )
    )
    respx.post(f"{WEB_BASE}/users/12345/items").mock(return_value=httpx.Response(429))

    client = WebClient(api_key="test-key", user_id="12345")
    with pytest.raises(httpx.HTTPStatusError):
        client.create_item_from_identifier("10.1/x")


# -- New tests: PubMed efetch with abstract --


@respx.mock
def test_pubmed_fallback_includes_abstract():
    """PubMed efetch fallback includes abstractNote in metadata."""
    respx.post(TRANSLATE_URL).mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(
        return_value=httpx.Response(
            200, json={"esearchresult": {"idlist": ["12345678"]}}
        )
    )
    respx.get(f"{PUBMED_BASE}/efetch.fcgi").mock(
        return_value=httpx.Response(200, text=SAMPLE_EFETCH_XML)
    )
    _mock_zotero_create()

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_item_from_identifier("10.1234/test")
    assert result["key"] == "NEW123"

    # Verify the metadata sent to Zotero included the abstract
    import json

    request = respx.calls[-1].request
    body = json.loads(request.content)
    assert body[0]["abstractNote"] == "This is the abstract text."
    assert body[0]["creators"][0]["lastName"] == "Smith"
    assert body[0]["creators"][0]["firstName"] == "John"


@respx.mock
def test_pubmed_fallback_detects_preprint_type():
    """PubMed efetch maps PublicationType 'Preprint' to Zotero preprint."""
    respx.post(TRANSLATE_URL).mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(
        return_value=httpx.Response(
            200, json={"esearchresult": {"idlist": ["99999999"]}}
        )
    )
    respx.get(f"{PUBMED_BASE}/efetch.fcgi").mock(
        return_value=httpx.Response(200, text=SAMPLE_PREPRINT_XML)
    )
    _mock_zotero_create()

    client = WebClient(api_key="test-key", user_id="12345")
    client.create_item_from_identifier("10.1101/2024.01.01.123")

    import json

    request = respx.calls[-1].request
    body = json.loads(request.content)
    assert body[0]["itemType"] == "preprint"
    # Structured abstract with labels
    assert "BACKGROUND: Background info." in body[0]["abstractNote"]
    assert "METHODS: Methods info." in body[0]["abstractNote"]


def test_parse_pubmed_xml_handles_structured_abstract():
    """_parse_pubmed_xml concatenates labeled abstract sections."""
    result = WebClient._parse_pubmed_xml(SAMPLE_PREPRINT_XML, "99999")
    assert result is not None
    assert (
        result["abstractNote"] == "BACKGROUND: Background info.\nMETHODS: Methods info."
    )


def test_parse_pubmed_xml_returns_none_on_invalid_xml():
    """_parse_pubmed_xml returns None on garbage input."""
    assert WebClient._parse_pubmed_xml("not xml at all", "123") is None


def test_parse_pubmed_xml_returns_none_on_empty_set():
    """_parse_pubmed_xml returns None when no PubmedArticle found."""
    empty = "<PubmedArticleSet></PubmedArticleSet>"
    assert WebClient._parse_pubmed_xml(empty, "123") is None


# -- New tests: CrossRef fallback --


@respx.mock
def test_crossref_fallback_for_book_chapter():
    """CrossRef resolves a book chapter DOI with correct item type."""
    respx.post(TRANSLATE_URL).mock(side_effect=httpx.ConnectError("down"))
    # PubMed won't find this non-biomedical DOI
    respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )
    # CrossRef returns a book chapter
    respx.get(f"{CROSSREF_BASE}/works/10.1007/978-3-030-12345-6_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "type": "book-chapter",
                    "title": ["A Chapter Title"],
                    "author": [{"family": "Chen", "given": "Wei"}],
                    "container-title": ["Advances in Computing"],
                    "publisher": "Springer",
                    "published-print": {"date-parts": [[2023, 5]]},
                    "DOI": "10.1007/978-3-030-12345-6_1",
                    "ISBN": ["978-3-030-12345-6"],
                    "abstract": "<p>This chapter discusses...</p>",
                }
            },
        )
    )
    _mock_zotero_create()

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_item_from_identifier("10.1007/978-3-030-12345-6_1")
    assert result["key"] == "NEW123"

    import json

    request = respx.calls[-1].request
    body = json.loads(request.content)
    assert body[0]["itemType"] == "bookSection"
    assert body[0]["bookTitle"] == "Advances in Computing"
    assert body[0]["publisher"] == "Springer"
    assert body[0]["ISBN"] == "978-3-030-12345-6"
    assert body[0]["abstractNote"] == "This chapter discusses..."


@respx.mock
def test_crossref_fallback_for_arxiv_preprint():
    """CrossRef resolves an arXiv DOI as a preprint."""
    respx.post(TRANSLATE_URL).mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )
    respx.get(f"{CROSSREF_BASE}/works/10.48550/arXiv.2301.08243").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "type": "posted-content",
                    "title": ["Attention Is All You Need (Again)"],
                    "author": [
                        {"family": "Vaswani", "given": "Ashish"},
                        {"family": "Shazeer", "given": "Noam"},
                    ],
                    "published-online": {"date-parts": [[2023, 1, 20]]},
                    "DOI": "10.48550/arXiv.2301.08243",
                    "abstract": "<jats:p>We revisit transformers...</jats:p>",
                }
            },
        )
    )
    _mock_zotero_create()

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_item_from_identifier("10.48550/arXiv.2301.08243")
    assert result["key"] == "NEW123"

    import json

    request = respx.calls[-1].request
    body = json.loads(request.content)
    assert body[0]["itemType"] == "preprint"
    assert body[0]["abstractNote"] == "We revisit transformers..."


@respx.mock
def test_fallback_chain_translation_pubmed_miss_crossref_hit():
    """Full fallback chain: translation down -> PubMed miss -> CrossRef hit."""
    respx.post(TRANSLATE_URL).mock(return_value=httpx.Response(503))
    # PubMed doesn't know this DOI
    respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )
    # CrossRef has it
    respx.get(f"{CROSSREF_BASE}/works/10.1145/1234567.1234568").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "type": "proceedings-article",
                    "title": ["A Conference Paper"],
                    "author": [{"family": "Park", "given": "Soo"}],
                    "container-title": ["Proceedings of ACM SIGCHI"],
                    "published-print": {"date-parts": [[2024]]},
                    "DOI": "10.1145/1234567.1234568",
                }
            },
        )
    )
    _mock_zotero_create()

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_item_from_identifier("10.1145/1234567.1234568")
    assert result["key"] == "NEW123"

    import json

    request = respx.calls[-1].request
    body = json.loads(request.content)
    assert body[0]["itemType"] == "conferencePaper"
    assert body[0]["proceedingsTitle"] == "Proceedings of ACM SIGCHI"


# -- New tests: URL fallback with DOI extraction --


@respx.mock
def test_url_fallback_extracts_arxiv_doi():
    """create_item_from_url extracts DOI from arxiv.org URL."""
    TRANSLATE_WEB_URL = "https://translate.zotero.org/web"
    respx.post(TRANSLATE_WEB_URL).mock(side_effect=httpx.ConnectError("down"))
    # PubMed doesn't have arXiv papers
    respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )
    # CrossRef resolves the arXiv DOI
    respx.get(f"{CROSSREF_BASE}/works/10.48550/arXiv.2301.12345").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "type": "posted-content",
                    "title": ["An ArXiv Paper"],
                    "author": [{"family": "Kim", "given": "Min"}],
                    "published-online": {"date-parts": [[2023]]},
                    "DOI": "10.48550/arXiv.2301.12345",
                }
            },
        )
    )
    _mock_zotero_create()

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_item_from_url("https://arxiv.org/abs/2301.12345")
    assert result["item_type"] == "preprint"
    assert result["title"] == "An ArXiv Paper"


def test_extract_doi_from_url_patterns():
    """_extract_doi_from_url handles doi.org, arxiv, biorxiv patterns."""
    extract = WebClient._extract_doi_from_url

    assert (
        extract("https://doi.org/10.1038/s41586-020-2012-7")
        == "10.1038/s41586-020-2012-7"
    )
    assert extract("https://arxiv.org/abs/2301.08243") == "10.48550/arXiv.2301.08243"
    assert (
        extract("https://www.biorxiv.org/content/10.1101/2024.01.15.123456")
        == "10.1101/2024.01.15.123456"
    )
    assert (
        extract("https://www.medrxiv.org/content/10.1101/2024.02.20.654321")
        == "10.1101/2024.02.20.654321"
    )
    assert extract("https://example.com/no-doi-here") == ""


def test_parse_crossref_work_returns_none_without_title():
    """_parse_crossref_work returns None when title is missing."""
    assert WebClient._parse_crossref_work({"type": "journal-article"}, "10.1/x") is None
    assert (
        WebClient._parse_crossref_work(
            {"type": "journal-article", "title": []}, "10.1/x"
        )
        is None
    )


# -- New tests: create_collection --


@respx.mock
def test_create_collection():
    """create_collection creates a top-level collection."""
    respx.post(f"{WEB_BASE}/users/12345/collections").mock(
        return_value=httpx.Response(
            200,
            json={
                "successful": {"0": {"key": "COL123", "data": {"key": "COL123"}}},
                "success": {"0": "COL123"},
                "unchanged": {},
                "failed": {},
            },
        )
    )

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_collection("Oncology")
    assert result["key"] == "COL123"
    assert result["name"] == "Oncology"
    assert result["parent_key"] == ""

    import json

    request = respx.calls[-1].request
    body = json.loads(request.content)
    assert body[0]["name"] == "Oncology"
    assert body[0]["parentCollection"] is False


@respx.mock
def test_create_collection_with_parent():
    """create_collection nests under a parent collection."""
    respx.post(f"{WEB_BASE}/users/12345/collections").mock(
        return_value=httpx.Response(
            200,
            json={
                "successful": {"0": {"key": "SUB456", "data": {"key": "SUB456"}}},
                "success": {"0": "SUB456"},
                "unchanged": {},
                "failed": {},
            },
        )
    )

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.create_collection("Gastric Cancer", parent_key="COL123")
    assert result["key"] == "SUB456"
    assert result["parent_key"] == "COL123"

    import json

    request = respx.calls[-1].request
    body = json.loads(request.content)
    assert body[0]["parentCollection"] == "COL123"


@respx.mock
def test_create_collection_api_failure():
    """create_collection raises RuntimeError on API failure."""
    respx.post(f"{WEB_BASE}/users/12345/collections").mock(
        return_value=httpx.Response(
            200,
            json={"successful": {}, "failed": {"0": {"message": "Invalid"}}},
        )
    )

    client = WebClient(api_key="test-key", user_id="12345")
    with pytest.raises(RuntimeError, match="Failed to create"):
        client.create_collection("Bad Collection")
