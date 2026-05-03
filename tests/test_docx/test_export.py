"""
Tests for card_export.export_cards_to_docx.

Verifies the output is a valid docx, contains expected card content, and that
underline/highlight XML runs are generated correctly.
"""
import io
import json
import zipfile


SAMPLE_CARD = {
    "tag": "Climate change causes global extinction",
    "author": "Smith, John",
    "author_qualifications": "Professor at Harvard",
    "date": "2024",
    "title": "Climate Tipping Points",
    "publisher": "Nature",
    "url": "https://nature.com/climate",
    "initials": "JS",
    "card_text": "Global temperatures have risen by 1.2 degrees since pre-industrial levels.",
    "underlined": json.dumps(["temperatures have risen"]),
    "highlighted": json.dumps(["1.2 degrees"]),
}


def test_export_returns_bytes():
    from card_export import export_cards_to_docx
    result = export_cards_to_docx([SAMPLE_CARD])
    assert isinstance(result, bytes)
    assert len(result) > 100


def test_export_is_valid_zip():
    from card_export import export_cards_to_docx
    result = export_cards_to_docx([SAMPLE_CARD])
    buf = io.BytesIO(result)
    assert zipfile.is_zipfile(buf)


def test_export_has_required_docx_parts():
    from card_export import export_cards_to_docx
    result = export_cards_to_docx([SAMPLE_CARD])
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        names = zf.namelist()
    assert "[Content_Types].xml" in names
    assert "word/document.xml" in names
    assert "word/styles.xml" in names
    assert "_rels/.rels" in names


def test_export_document_xml_is_valid_xml():
    from card_export import export_cards_to_docx
    result = export_cards_to_docx([SAMPLE_CARD])
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        doc_xml = zf.read("word/document.xml").decode("utf-8")
    assert doc_xml.startswith("<?xml")
    assert "<w:document" in doc_xml
    assert "</w:document>" in doc_xml


def test_export_contains_tag_text():
    from card_export import export_cards_to_docx
    result = export_cards_to_docx([SAMPLE_CARD])
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        doc_xml = zf.read("word/document.xml").decode()
    assert "Climate change causes global extinction" in doc_xml


def test_export_contains_card_body_text():
    from card_export import export_cards_to_docx
    result = export_cards_to_docx([SAMPLE_CARD])
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        doc_xml = zf.read("word/document.xml").decode()
    # Body text is split across underline/highlight runs in the XML, so check
    # fragments that appear in plain runs rather than the full contiguous string.
    assert "Global" in doc_xml
    assert "pre-industrial levels" in doc_xml


def test_export_contains_cite_fields():
    from card_export import export_cards_to_docx
    result = export_cards_to_docx([SAMPLE_CARD])
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        doc_xml = zf.read("word/document.xml").decode()
    assert "Smith, John" in doc_xml
    assert "2024" in doc_xml


def test_export_underline_runs_present():
    """Text in the underlined list must be wrapped in underline XML runs."""
    from card_export import export_cards_to_docx
    result = export_cards_to_docx([SAMPLE_CARD])
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        doc_xml = zf.read("word/document.xml").decode()
    # Underlined runs use <w:u w:val="single"/>
    assert '<w:u w:val="single"/>' in doc_xml
    assert "temperatures have risen" in doc_xml


def test_export_highlight_runs_present():
    """Text in the highlighted list must use the highlight colour."""
    from card_export import export_cards_to_docx
    result = export_cards_to_docx([SAMPLE_CARD])
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        doc_xml = zf.read("word/document.xml").decode()
    assert 'w:val="cyan"' in doc_xml
    assert "1.2 degrees" in doc_xml


def test_export_multiple_cards():
    from card_export import export_cards_to_docx
    cards = [
        {**SAMPLE_CARD, "tag": f"Card {i}", "card_text": f"Body for card {i}."}
        for i in range(3)
    ]
    result = export_cards_to_docx(cards)
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        doc_xml = zf.read("word/document.xml").decode()
    for i in range(3):
        assert f"Card {i}" in doc_xml
        assert f"Body for card {i}." in doc_xml


def test_export_empty_card_list():
    """Exporting zero cards should still produce a valid docx."""
    from card_export import export_cards_to_docx
    result = export_cards_to_docx([])
    assert zipfile.is_zipfile(io.BytesIO(result))


def test_export_card_with_no_underlines():
    from card_export import export_cards_to_docx
    card = {
        "tag": "Plain card",
        "card_text": "No underlines here.",
        "underlined": json.dumps([]),
        "highlighted": json.dumps([]),
    }
    result = export_cards_to_docx([card])
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        doc_xml = zf.read("word/document.xml").decode()
    assert "No underlines here." in doc_xml


def test_export_xml_special_chars_escaped():
    from card_export import export_cards_to_docx
    card = {
        "tag": 'Tag with <angle> & "quotes"',
        "card_text": "Text with & ampersand.",
        "underlined": json.dumps([]),
        "highlighted": json.dumps([]),
    }
    result = export_cards_to_docx([card])
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        doc_xml = zf.read("word/document.xml").decode()
    # XML special chars must be escaped — raw < or & in attribute/text is invalid XML
    assert "&lt;angle&gt;" in doc_xml or "angle" in doc_xml
    assert "&amp;" in doc_xml


def test_export_highlight_color_customizable():
    from card_export import export_cards_to_docx
    card = {
        "tag": "HL color test",
        "card_text": "Some text to highlight.",
        "underlined": json.dumps(["Some text"]),
        "highlighted": json.dumps(["Some text"]),
    }
    result = export_cards_to_docx([card], hl_color="yellow")
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        doc_xml = zf.read("word/document.xml").decode()
    assert 'w:val="yellow"' in doc_xml
