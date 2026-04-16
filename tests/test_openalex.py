"""Tests for OpenAlexClient — OpenAlex API wrapper."""

import httpx
import pytest
import respx

from zotero_mcp.openalex_client import OpenAlexClient

OPENALEX_BASE = "https://api.openalex.org"


@respx.mock
def test_get_work_returns_metadata():
    """get_work returns work metadata for a valid DOI."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/test").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W12345",
                "doi": "https://doi.org/10.1234/test",
                "title": "Test Paper",
                "is_retracted": False,
                "cited_by_count": 42,
                "cited_by_api_url": f"{OPENALEX_BASE}/works?filter=cites:W12345",
                "referenced_works": ["https://openalex.org/W99999"],
            },
        )
    )
    client = OpenAlexClient()
    result = client.get_work("10.1234/test")
    assert result is not None
    assert result["title"] == "Test Paper"
    assert result["is_retracted"] is False
    assert result["cited_by_count"] == 42


@respx.mock
def test_get_work_returns_none_for_404():
    """get_work returns None when DOI not found."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/nonexistent").mock(
        return_value=httpx.Response(404)
    )
    client = OpenAlexClient()
    result = client.get_work("10.1234/nonexistent")
    assert result is None


@respx.mock
def test_get_work_returns_retracted_flag():
    """get_work correctly reports retracted papers."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/retracted").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W11111",
                "doi": "https://doi.org/10.1234/retracted",
                "title": "Retracted Paper",
                "is_retracted": True,
                "cited_by_count": 5,
                "cited_by_api_url": "",
                "referenced_works": [],
            },
        )
    )
    client = OpenAlexClient()
    result = client.get_work("10.1234/retracted")
    assert result["is_retracted"] is True


@respx.mock
def test_get_work_strips_doi_prefix():
    """get_work handles DOIs with https://doi.org/ prefix."""
    respx.get(f"{OPENALEX_BASE}/works/doi:10.1234/test").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W12345",
                "doi": "https://doi.org/10.1234/test",
                "title": "Test Paper",
                "is_retracted": False,
                "cited_by_count": 10,
            },
        )
    )
    client = OpenAlexClient()
    result = client.get_work("https://doi.org/10.1234/test")
    assert result is not None
    assert result["title"] == "Test Paper"


@respx.mock
def test_bulk_get_works_batches_dois():
    """bulk_get_works fetches metadata for multiple DOIs in batches."""
    respx.get(
        f"{OPENALEX_BASE}/works",
        params__contains={"filter": "doi:10.1/a|10.1/b"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "doi": "https://doi.org/10.1/a",
                        "title": "Paper A",
                        "publication_year": 2020,
                        "authorships": [],
                        "referenced_works": ["https://openalex.org/W99"],
                    },
                    {
                        "id": "https://openalex.org/W2",
                        "doi": "https://doi.org/10.1/b",
                        "title": "Paper B",
                        "publication_year": 2022,
                        "authorships": [],
                        "referenced_works": [],
                    },
                ]
            },
        )
    )
    client = OpenAlexClient()
    results = client.bulk_get_works(["10.1/a", "10.1/b"])
    assert len(results) == 2
    assert results[0]["doi"] == "https://doi.org/10.1/a"


@respx.mock
def test_resolve_openalex_ids_to_dois():
    """resolve_ids_to_dois converts OpenAlex IDs to DOIs."""
    respx.get(
        f"{OPENALEX_BASE}/works",
        params__contains={"filter": "openalex:W99|W100"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W99",
                        "doi": "https://doi.org/10.1/ref1",
                    },
                    {"id": "https://openalex.org/W100", "doi": None},
                ]
            },
        )
    )
    client = OpenAlexClient()
    mapping = client.resolve_ids_to_dois(["W99", "W100"])
    assert mapping == {"W99": "10.1/ref1"}
    # W100 has no DOI, so it's excluded


@respx.mock
def test_bulk_get_works_single_doi():
    """bulk_get_works works with a single DOI (no trailing pipe)."""
    respx.get(
        f"{OPENALEX_BASE}/works",
        params__contains={"filter": "doi:10.1/only"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "W1",
                        "doi": "https://doi.org/10.1/only",
                        "title": "Only",
                        "publication_year": 2023,
                        "authorships": [],
                        "referenced_works": [],
                    },
                ]
            },
        )
    )
    client = OpenAlexClient()
    results = client.bulk_get_works(["10.1/only"])
    assert len(results) == 1


# --- Static extraction helpers (no API calls, no respx needed) ---


def test_extract_topics():
    """extract_topics parses topic hierarchy from an OpenAlex work dict."""
    work = {
        "topics": [
            {
                "id": "https://openalex.org/T10234",
                "display_name": "Gastric Cancer Risk Factors",
                "subfield": {"id": "https://openalex.org/subfields/2721", "display_name": "Gastroenterology"},
                "field": {"id": "https://openalex.org/fields/27", "display_name": "Medicine"},
                "domain": {"id": "https://openalex.org/domains/4", "display_name": "Health Sciences"},
                "score": 0.95,
            },
            {
                "id": "https://openalex.org/T20456",
                "display_name": "Helicobacter pylori Pathogenesis",
                "subfield": {"id": "https://openalex.org/subfields/2726", "display_name": "Microbiology"},
                "field": {"id": "https://openalex.org/fields/27", "display_name": "Medicine"},
                "domain": {"id": "https://openalex.org/domains/4", "display_name": "Health Sciences"},
                "score": 0.82,
            },
        ]
    }
    topics = OpenAlexClient.extract_topics(work)
    assert len(topics) == 2
    assert topics[0]["topic_id"] == "T10234"
    assert topics[0]["topic_name"] == "Gastric Cancer Risk Factors"
    assert topics[0]["subfield"] == "Gastroenterology"
    assert topics[0]["field"] == "Medicine"
    assert topics[0]["domain"] == "Health Sciences"
    assert topics[0]["score"] == 0.95
    assert topics[1]["topic_id"] == "T20456"
    assert topics[1]["topic_name"] == "Helicobacter pylori Pathogenesis"
    assert topics[1]["score"] == 0.82


def test_extract_topics_empty():
    """extract_topics returns empty list when work has no topics key."""
    assert OpenAlexClient.extract_topics({}) == []
    assert OpenAlexClient.extract_topics({"topics": []}) == []


def test_extract_authorships():
    """extract_authorships parses structured author records from an OpenAlex work dict."""
    work = {
        "authorships": [
            {
                "author": {
                    "id": "https://openalex.org/A5023888391",
                    "display_name": "John Smith",
                    "orcid": "https://orcid.org/0000-0001-2345-6789",
                },
                "institutions": [{"display_name": "Mount Sinai"}],
                "author_position": "first",
            },
            {
                "author": {
                    "id": "https://openalex.org/A5098765432",
                    "display_name": "Jane Doe",
                    "orcid": "https://orcid.org/0000-0002-9876-5432",
                },
                "institutions": [
                    {"display_name": "Harvard Medical School"},
                    {"display_name": "Brigham and Women's Hospital"},
                ],
                "author_position": "middle",
            },
            {
                "author": {
                    "id": "https://openalex.org/A5011112222",
                    "display_name": "Bob Chen",
                    "orcid": "https://orcid.org/0000-0003-1111-2222",
                },
                "institutions": [{"display_name": "Stanford University"}],
                "author_position": "last",
            },
        ]
    }
    authors = OpenAlexClient.extract_authorships(work)
    assert len(authors) == 3
    assert authors[0]["openalex_author_id"] == "A5023888391"
    assert authors[0]["display_name"] == "John Smith"
    assert authors[0]["orcid"] == "0000-0001-2345-6789"
    assert authors[0]["institution"] == "Mount Sinai"
    assert authors[0]["position"] == 0
    # Second author — takes first institution only
    assert authors[1]["openalex_author_id"] == "A5098765432"
    assert authors[1]["institution"] == "Harvard Medical School"
    assert authors[1]["position"] == 1
    # Third author
    assert authors[2]["position"] == 2
    assert authors[2]["display_name"] == "Bob Chen"


def test_extract_authorships_handles_missing_fields():
    """extract_authorships gracefully handles missing orcid and institutions."""
    work = {
        "authorships": [
            {
                "author": {
                    "id": "https://openalex.org/A5055555555",
                    "display_name": "Anonymous Researcher",
                    "orcid": None,
                },
                "institutions": [],
                "author_position": "first",
            },
        ]
    }
    authors = OpenAlexClient.extract_authorships(work)
    assert len(authors) == 1
    assert authors[0]["openalex_author_id"] == "A5055555555"
    assert authors[0]["display_name"] == "Anonymous Researcher"
    assert authors[0]["orcid"] == ""
    assert authors[0]["institution"] == ""
    assert authors[0]["position"] == 0


def test_reconstruct_abstract():
    """reconstruct_abstract rebuilds plain text from inverted index."""
    work = {
        "abstract_inverted_index": {
            "Background:": [0],
            "Helicobacter": [1],
            "pylori": [2],
            "infection": [3],
            "is": [4],
            "a": [5],
            "risk": [6],
            "factor.": [7],
        }
    }
    result = OpenAlexClient.reconstruct_abstract(work)
    assert result == "Background: Helicobacter pylori infection is a risk factor."


def test_reconstruct_abstract_handles_repeated_words():
    """Words appearing at multiple positions are placed correctly."""
    work = {
        "abstract_inverted_index": {
            "the": [0, 3],
            "cat": [1],
            "and": [2],
            "dog": [4],
        }
    }
    result = OpenAlexClient.reconstruct_abstract(work)
    assert result == "the cat and the dog"


def test_reconstruct_abstract_returns_none_for_missing():
    """Returns None when abstract_inverted_index is missing or empty."""
    assert OpenAlexClient.reconstruct_abstract({}) is None
    assert OpenAlexClient.reconstruct_abstract({"abstract_inverted_index": None}) is None
    assert OpenAlexClient.reconstruct_abstract({"abstract_inverted_index": {}}) is None
