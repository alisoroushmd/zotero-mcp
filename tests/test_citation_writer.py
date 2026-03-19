"""Tests for citation_writer -- Zotero field code generation."""

import json
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from zotero_mcp.citation_writer import (
    TextBlock,
    parse_citations,
    zotero_to_csl_json,
    add_citation_field,
    add_bibliography_field,
    build_document,
)

# -- Parser tests --


def test_parse_single_citation():
    blocks, mapping = parse_citations("text [@ABC123] more")
    assert len(blocks) == 3
    assert blocks[0] == TextBlock("text", "text ", [], [])
    assert blocks[1] == TextBlock("citation", "", ["ABC123"], [1])
    assert blocks[2] == TextBlock("text", " more", [], [])
    assert mapping == {"ABC123": 1}


def test_parse_grouped_citations():
    blocks, mapping = parse_citations("text [@AAA, @BBB]")
    assert len(blocks) == 2
    citation = blocks[1]
    assert citation.keys == ["AAA", "BBB"]
    assert citation.numbers == [1, 2]
    assert mapping == {"AAA": 1, "BBB": 2}


def test_parse_duplicate_same_number():
    blocks, mapping = parse_citations("first [@ABC] second [@ABC]")
    assert mapping == {"ABC": 1}
    assert blocks[1].numbers == [1]
    assert blocks[3].numbers == [1]


def test_parse_no_citations():
    blocks, mapping = parse_citations("plain text no citations")
    assert len(blocks) == 1
    assert blocks[0].kind == "text"
    assert mapping == {}


def test_parse_sequential_numbering():
    _, mapping = parse_citations("[@A] then [@B] then [@C]")
    assert mapping == {"A": 1, "B": 2, "C": 3}


# -- CSL-JSON conversion tests --

SAMPLE_ITEM = {
    "key": "ABC123",
    "itemType": "journalArticle",
    "title": "Test Article Title",
    "creators": [
        {"creatorType": "author", "firstName": "John", "lastName": "Doe"},
        {"creatorType": "author", "firstName": "Jane", "lastName": "Smith"},
        {"creatorType": "editor", "firstName": "Ed", "lastName": "Itor"},
    ],
    "date": "2024-03-15",
    "DOI": "10.1234/test",
    "publicationTitle": "Journal of Testing",
    "volume": "42",
    "issue": "3",
    "pages": "100-110",
    "ISSN": "1234-5678",
}


def test_journal_article_conversion():
    csl = zotero_to_csl_json(SAMPLE_ITEM, "12345")
    assert csl["type"] == "article-journal"
    assert csl["title"] == "Test Article Title"
    assert csl["container-title"] == "Journal of Testing"
    assert csl["volume"] == "42"
    assert csl["issue"] == "3"
    assert csl["page"] == "100-110"
    assert csl["DOI"] == "10.1234/test"


def test_creator_mapping():
    csl = zotero_to_csl_json(SAMPLE_ITEM, "12345")
    # Only authors, not editors
    assert len(csl["author"]) == 2
    assert csl["author"][0] == {"family": "Doe", "given": "John"}
    assert csl["author"][1] == {"family": "Smith", "given": "Jane"}


def test_date_parsing_full():
    csl = zotero_to_csl_json({**SAMPLE_ITEM, "date": "2024-03-15"}, "12345")
    assert csl["issued"] == {"date-parts": [[2024, 3, 15]]}


def test_date_parsing_year_only():
    csl = zotero_to_csl_json({**SAMPLE_ITEM, "date": "2024"}, "12345")
    assert csl["issued"] == {"date-parts": [[2024]]}


def test_date_parsing_year_month():
    csl = zotero_to_csl_json({**SAMPLE_ITEM, "date": "2024-03"}, "12345")
    assert csl["issued"] == {"date-parts": [[2024, 3]]}


def test_uri_construction():
    csl = zotero_to_csl_json(SAMPLE_ITEM, "12345")
    assert "http://zotero.org/users/12345/items/ABC123" in csl["_uris"]


def test_unknown_item_type_fallback():
    item = {**SAMPLE_ITEM, "itemType": "podcast"}
    csl = zotero_to_csl_json(item, "12345")
    assert csl["type"] == "article"


# -- Field code tests --


def test_citation_field_code_structure():
    doc = Document()
    para = doc.add_paragraph()
    citation_json = {
        "citationID": "test1",
        "properties": {
            "formattedCitation": "1",
            "plainCitation": "1",
            "noteIndex": 0,
        },
        "citationItems": [
            {
                "id": 1,
                "uris": ["http://example.com"],
                "itemData": {"type": "article-journal", "title": "Test"},
            }
        ],
        "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json",
    }
    add_citation_field(para, citation_json, "1")

    # Check XML has fldChar elements
    xml = para._element.xml
    assert "w:fldChar" in xml
    assert "ADDIN ZOTERO_ITEM CSL_CITATION" in xml


def test_citation_instrtext_valid_json():
    doc = Document()
    para = doc.add_paragraph()
    csl_data = {"type": "article-journal", "title": "Test Paper"}
    citation_json = {
        "citationID": "test1",
        "properties": {
            "formattedCitation": "1",
            "plainCitation": "1",
            "noteIndex": 0,
        },
        "citationItems": [
            {"id": 1, "uris": ["http://example.com"], "itemData": csl_data}
        ],
        "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json",
    }
    add_citation_field(para, citation_json, "1")

    # Extract instrText and verify it's valid JSON after the prefix
    instr_elements = para._element.findall(".//" + qn("w:instrText"))
    assert len(instr_elements) >= 1
    instr_text = instr_elements[0].text
    json_str = instr_text.replace("ADDIN ZOTERO_ITEM CSL_CITATION ", "")
    parsed = json.loads(json_str)
    assert parsed["citationID"] == "test1"


def test_bibliography_field_code():
    doc = Document()
    para = doc.add_paragraph()
    add_bibliography_field(para)
    xml = para._element.xml
    assert "ADDIN ZOTERO_BIBL" in xml
    assert "CSL_BIBLIOGRAPHY" in xml


def test_superscript_display():
    doc = Document()
    para = doc.add_paragraph()
    citation_json = {
        "citationID": "test1",
        "properties": {
            "formattedCitation": "1",
            "plainCitation": "1",
            "noteIndex": 0,
        },
        "citationItems": [
            {
                "id": 1,
                "uris": ["uri"],
                "itemData": {"type": "article-journal", "title": "T"},
            }
        ],
        "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json",
    }
    add_citation_field(para, citation_json, "1")
    xml = para._element.xml
    assert "w:vertAlign" in xml
    assert "superscript" in xml


# -- Document assembly tests --


def test_build_simple_document(tmp_path):
    output = tmp_path / "test.docx"
    item_data = {
        "ABC": {
            "key": "ABC",
            "itemType": "journalArticle",
            "title": "Test Paper",
            "creators": [
                {"creatorType": "author", "firstName": "J", "lastName": "Doe"}
            ],
            "date": "2024",
            "DOI": "10.1/test",
            "publicationTitle": "Test Journal",
            "volume": "1",
            "issue": "1",
            "pages": "1-5",
        }
    }
    result = build_document(
        "This is a test [@ABC].",
        item_data,
        "12345",
        str(output),
    )
    assert Path(result).exists()
    doc = Document(result)
    # Should have at least: content paragraph + References heading + bibliography paragraph
    assert len(doc.paragraphs) >= 3


def test_build_with_headings(tmp_path):
    output = tmp_path / "headings.docx"
    result = build_document(
        "# Introduction\n\nSome text.\n\n## Methods\n\nMore text.",
        {},
        "12345",
        str(output),
    )
    doc = Document(result)
    styles = [p.style.name for p in doc.paragraphs]
    assert "Heading 1" in styles
    assert "Heading 2" in styles


def test_build_multiple_citations_sequential(tmp_path):
    output = tmp_path / "multi.docx"
    item_data = {
        "A": {
            "key": "A",
            "itemType": "journalArticle",
            "title": "Paper A",
            "creators": [],
            "date": "2024",
            "DOI": "",
            "publicationTitle": "J",
            "volume": "",
            "issue": "",
            "pages": "",
        },
        "B": {
            "key": "B",
            "itemType": "journalArticle",
            "title": "Paper B",
            "creators": [],
            "date": "2024",
            "DOI": "",
            "publicationTitle": "J",
            "volume": "",
            "issue": "",
            "pages": "",
        },
    }
    result = build_document("First [@A] then [@B].", item_data, "12345", str(output))
    doc = Document(result)
    xml = doc.paragraphs[0]._element.xml
    # Both citations should be present
    assert "ADDIN ZOTERO_ITEM" in xml


def test_build_bibliography_at_end(tmp_path):
    output = tmp_path / "biblio.docx"
    item_data = {
        "X": {
            "key": "X",
            "itemType": "journalArticle",
            "title": "Paper X",
            "creators": [],
            "date": "2024",
            "DOI": "",
            "publicationTitle": "J",
            "volume": "",
            "issue": "",
            "pages": "",
        },
    }
    result = build_document("Text [@X].", item_data, "12345", str(output))
    doc = Document(result)
    last_para = doc.paragraphs[-1]
    assert "ADDIN ZOTERO_BIBL" in last_para._element.xml
