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


# -- search_items filter tests --

_ITEM_STUB = {
    "data": {
        "key": "ABC123",
        "title": "Test Paper",
        "itemType": "journalArticle",
        "creators": [],
        "date": "",
    }
}


@respx.mock
def test_search_items_passes_item_type_param():
    """search_items forwards item_type as itemType query param."""
    route = respx.get(f"{WEB_BASE}/users/12345/items/top").mock(
        return_value=httpx.Response(200, json=[_ITEM_STUB])
    )
    client = WebClient(api_key="k", user_id="12345")
    client.search_items("cancer", item_type="journalArticle")
    assert route.calls[0].request.url.params["itemType"] == "journalArticle"


@respx.mock
def test_search_items_passes_tag_param():
    """search_items forwards tag as tag query param."""
    route = respx.get(f"{WEB_BASE}/users/12345/items/top").mock(
        return_value=httpx.Response(200, json=[_ITEM_STUB])
    )
    client = WebClient(api_key="k", user_id="12345")
    client.search_items("cancer", tag="reviewed")
    assert route.calls[0].request.url.params["tag"] == "reviewed"


@respx.mock
def test_search_items_no_filters_omits_params():
    """search_items without filters sends no itemType or tag params."""
    route = respx.get(f"{WEB_BASE}/users/12345/items/top").mock(
        return_value=httpx.Response(200, json=[_ITEM_STUB])
    )
    client = WebClient(api_key="k", user_id="12345")
    client.search_items("cancer")
    params = route.calls[0].request.url.params
    assert "itemType" not in params
    assert "tag" not in params


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
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": ["12345678"]}})
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

    local = LocalClient(probe=False)
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
    respx.patch(f"{WEB_BASE}/users/12345/items/ITEM1").mock(return_value=httpx.Response(204))

    from zotero_mcp.local_client import LocalClient

    local = LocalClient(probe=False)
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
    respx.patch(f"{WEB_BASE}/users/12345/items/ITEM1").mock(return_value=httpx.Response(204))

    from zotero_mcp.local_client import LocalClient

    local = LocalClient(probe=False)
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
    respx.patch(f"{WEB_BASE}/users/12345/items/ITEM1").mock(return_value=httpx.Response(412))

    from zotero_mcp.local_client import LocalClient

    local = LocalClient(probe=False)
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
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": ["12345678"]}})
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
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": ["99999999"]}})
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
    assert result["abstractNote"] == "BACKGROUND: Background info.\nMETHODS: Methods info."


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

    assert extract("https://doi.org/10.1038/s41586-020-2012-7") == "10.1038/s41586-020-2012-7"
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
        WebClient._parse_crossref_work({"type": "journal-article", "title": []}, "10.1/x") is None
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


# -- Tag management tests --

BASE_TAG = f"{WEB_BASE}/users/12345"


@respx.mock
def test_get_tags_returns_sorted_list():
    """get_tags returns all tag strings sorted."""
    respx.get(f"{BASE_TAG}/tags").mock(
        return_value=httpx.Response(
            200, json=[{"tag": "zebra"}, {"tag": "apple"}, {"tag": "mango"}]
        )
    )
    client = WebClient(api_key="k", user_id="12345")
    tags = client.get_tags()
    assert tags == ["apple", "mango", "zebra"]


@respx.mock
def test_get_tags_passes_prefix_as_q():
    """get_tags with prefix sends q param."""
    route = respx.get(f"{BASE_TAG}/tags").mock(
        return_value=httpx.Response(200, json=[{"tag": "cancer-review"}])
    )
    client = WebClient(api_key="k", user_id="12345")
    client.get_tags(prefix="cancer")
    assert route.calls[0].request.url.params["q"] == "cancer"


@respx.mock
def test_remove_tag_sends_delete():
    """remove_tag DELETEs via /tags?tag=<name> (query param), not /tags/<name>.

    The Zotero Web API only supports tag deletion through the query-parameter
    form; DELETE on the /tags/<name> path segment returns 405 Method Not Allowed.
    """
    respx.get(f"{BASE_TAG}/items").mock(
        return_value=httpx.Response(200, json=[], headers={"Last-Modified-Version": "5"})
    )
    delete_route = respx.delete(f"{BASE_TAG}/tags").mock(return_value=httpx.Response(204))
    client = WebClient(api_key="k", user_id="12345")
    result = client.remove_tag("old-tag")
    assert result["status"] == "removed"
    assert delete_route.called
    req = delete_route.calls[0].request
    assert req.url.path == "/users/12345/tags"
    assert req.url.params["tag"] == "old-tag"
    assert req.headers["If-Unmodified-Since-Version"] == "5"


@respx.mock
def test_remove_tag_encodes_special_characters_in_query():
    """Tags with spaces/emoji ride in the url-encoded query param (regression: #405).

    Real-world trigger: the '❓ Multiple DOI' tag failed with HTTP 405 because
    the tag was interpolated into the DELETE path instead of the query string.
    """
    respx.get(f"{BASE_TAG}/items").mock(
        return_value=httpx.Response(200, json=[], headers={"Last-Modified-Version": "9"})
    )
    delete_route = respx.delete(f"{BASE_TAG}/tags").mock(return_value=httpx.Response(204))
    client = WebClient(api_key="k", user_id="12345")
    tag = "❓ Multiple DOI"
    result = client.remove_tag(tag)
    assert result["status"] == "removed"
    req = delete_route.calls[0].request
    assert req.url.path == "/users/12345/tags"
    # httpx decodes the param back to the original value ...
    assert req.url.params["tag"] == tag
    # ... but the wire form must be percent-encoded (no raw spaces/emoji).
    raw_query = req.url.query.decode()
    assert " " not in raw_query and "❓" not in raw_query


@respx.mock
def test_rename_tag_patches_all_items():
    """rename_tag fetches items with old tag and PATCHes each one."""
    item_data = {
        "data": {
            "key": "ITEM001",
            "version": 3,
            "tags": [{"tag": "old"}, {"tag": "keep"}],
        }
    }
    respx.get(f"{BASE_TAG}/items").mock(return_value=httpx.Response(200, json=[item_data]))
    patch_route = respx.patch(f"{BASE_TAG}/items/ITEM001").mock(return_value=httpx.Response(204))
    client = WebClient(api_key="k", user_id="12345")
    result = client.rename_tag("old", "new")
    assert result["updated"] == 1
    assert result["failed"] == []
    assert patch_route.called
    sent = patch_route.calls[0].request
    import json as _json

    body = _json.loads(sent.content)
    tag_names = [t["tag"] for t in body["tags"]]
    assert "new" in tag_names
    assert "old" not in tag_names
    assert "keep" in tag_names


# -- get_all_items_with_dois tests --


@respx.mock
def test_get_all_items_with_dois_paginates():
    """get_all_items_with_dois fetches multiple pages."""
    page1 = [
        {
            "key": "A",
            "data": {"itemType": "journalArticle", "title": "P1", "DOI": "10.1/a"},
        },
    ]
    page2 = [
        {
            "key": "B",
            "data": {"itemType": "journalArticle", "title": "P2", "DOI": "10.1/b"},
        },
    ]
    respx.get(
        url__regex=r".*/items/top.*",
        params__contains={"start": "0"},
    ).mock(return_value=httpx.Response(200, json=page1, headers={"Total-Results": "101"}))
    respx.get(
        url__regex=r".*/items/top.*",
        params__contains={"start": "100"},
    ).mock(return_value=httpx.Response(200, json=page2, headers={"Total-Results": "101"}))

    client = WebClient(api_key="test", user_id="123")
    items = client.get_all_items_with_dois()
    assert len(items) == 2
    assert items[0]["DOI"] == "10.1/a"


@respx.mock
def test_get_all_items_with_dois_skips_no_doi():
    """Items without DOIs are excluded."""
    items = [
        {
            "key": "A",
            "data": {"itemType": "journalArticle", "title": "P1", "DOI": "10.1/a"},
        },
        {"key": "B", "data": {"itemType": "journalArticle", "title": "P2"}},
    ]
    respx.get(url__regex=r".*/items/top.*").mock(
        return_value=httpx.Response(200, json=items, headers={"Total-Results": "2"})
    )
    client = WebClient(api_key="test", user_id="123")
    result = client.get_all_items_with_dois()
    assert len(result) == 1


# -- batch_organize tests --


@respx.mock
def test_batch_organize_moves_items_to_collection():
    """batch_organize PATCHes each item to add it to the target collection."""
    # _read_item falls back to web GET when no local client
    item_response = {
        "key": "ITEM1",
        "version": 5,
        "data": {
            "key": "ITEM1",
            "version": 5,
            "tags": [],
            "collections": [],
        },
    }
    respx.get(f"{WEB_BASE}/users/12345/items/ITEM1").mock(
        return_value=httpx.Response(200, json=item_response)
    )
    patch_route = respx.patch(f"{WEB_BASE}/users/12345/items/ITEM1").mock(
        return_value=httpx.Response(204)
    )

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.batch_organize(["ITEM1"], collection_key="COL999")

    assert "ITEM1" in result["updated_keys"]
    assert result["updated_count"] == 1
    assert result["failed_count"] == 0
    # Verify the PATCH was sent with the collection
    import json as _json

    patch_body = _json.loads(patch_route.calls[0].request.content)
    assert "COL999" in patch_body.get("collections", [])


@respx.mock
def test_batch_organize_handles_412_retry():
    """batch_organize retries on 412 version conflict and still records the item as updated."""
    item_v5 = {
        "key": "ITEM2",
        "version": 5,
        "data": {
            "key": "ITEM2",
            "version": 5,
            "tags": [],
            "collections": [],
        },
    }
    item_v6 = {
        "key": "ITEM2",
        "version": 6,
        "data": {
            "key": "ITEM2",
            "version": 6,
            "tags": [],
            "collections": [],
        },
    }

    # First GET: initial read; second GET: re-read after 412
    respx.get(f"{WEB_BASE}/users/12345/items/ITEM2").mock(
        side_effect=[
            httpx.Response(200, json=item_v5),
            httpx.Response(200, json=item_v6),
        ]
    )
    # First PATCH returns 412 (version conflict); second PATCH succeeds
    respx.patch(f"{WEB_BASE}/users/12345/items/ITEM2").mock(
        side_effect=[
            httpx.Response(412),
            httpx.Response(204),
        ]
    )

    client = WebClient(api_key="test-key", user_id="12345")
    result = client.batch_organize(["ITEM2"], collection_key="COLX")

    assert "ITEM2" in result["updated_keys"]
    assert result["updated_count"] == 1
    assert result["failed_count"] == 0


# -- NCBI eutils API key injection (ZOT-28) --


@respx.mock
def test_pubmed_get_injects_ncbi_api_key(monkeypatch):
    """When NCBI_API_KEY is set, _pubmed_get adds it to eutils query params."""
    from zotero_mcp.config import _reset_config

    monkeypatch.setenv("NCBI_API_KEY", "secret-ncbi-key")
    _reset_config()
    try:
        route = respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(
            return_value=httpx.Response(200, json={"esearchresult": {"idlist": ["999"]}})
        )
        client = WebClient(api_key="k", user_id="1")
        client.resolve_pmid_to_pmcid("12345")
        assert route.called
        sent_url = str(route.calls.last.request.url)
        assert "api_key=secret-ncbi-key" in sent_url
    finally:
        _reset_config()


@respx.mock
def test_pubmed_get_omits_key_when_unset(monkeypatch):
    """Without NCBI_API_KEY, no api_key param is sent."""
    from zotero_mcp.config import _reset_config

    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    _reset_config()
    try:
        route = respx.get(f"{PUBMED_BASE}/esearch.fcgi").mock(
            return_value=httpx.Response(200, json={"esearchresult": {"idlist": ["999"]}})
        )
        client = WebClient(api_key="k", user_id="1")
        client.resolve_pmid_to_pmcid("12345")
        assert route.called
        assert "api_key=" not in str(route.calls.last.request.url)
    finally:
        _reset_config()


# -- Retry-After hostile-value handling (ZOT-24 review) --


def test_parse_retry_after_rejects_hostile_values():
    """web_client _parse_retry_after rejects negative/nan/inf (ZOT-24 review)."""
    from zotero_mcp.web_client import _parse_retry_after

    assert _parse_retry_after("5", 9.0) == 5.0
    assert _parse_retry_after("-5", 9.0) == 9.0
    assert _parse_retry_after("nan", 9.0) == 9.0
    assert _parse_retry_after("inf", 9.0) == 9.0
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", 9.0) == 9.0
    assert _parse_retry_after(None, 9.0) == 9.0


# -- Stale dedup flag does not leak across pooled-client calls (ZOT-26 review) --


@respx.mock
def test_dedup_flag_does_not_leak_to_url_create():
    """A prior transient dedup failure must not flag a later DOI-less URL create."""
    client = WebClient(api_key="k", user_id="1")
    # Poison the flag as if a previous create had a dedup-check failure.
    client._dedup_check_failed = True

    # Translation /web returns a webpage stub with NO DOI -> no dedup check runs.
    respx.post("https://translate.zotero.org/web").mock(
        return_value=httpx.Response(
            200, json=[{"itemType": "webpage", "title": "Some Page", "url": "https://x.test"}]
        )
    )
    respx.post(f"{WEB_BASE}/users/1/items").mock(
        return_value=httpx.Response(
            200, json={"successful": {"0": {"key": "URLKEY", "data": {"key": "URLKEY"}}}}
        )
    )
    result = client.create_item_from_url("https://x.test/page")
    assert result["key"] == "URLKEY"
    # The stale flag must have been reset at entry, so no false warning.
    assert "dedup_check_failed" not in result
