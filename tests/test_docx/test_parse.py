"""
Tests for docx parsing: strip_cutting, extract_text_from_xml, parse_cards.

These tests use the real test docx file to guarantee the parser works on
production-representative input — not just synthetic fixtures.
"""
import io
import zipfile

import pytest


# ── strip_cutting ─────────────────────────────────────────────────────────────

def test_strip_cutting_returns_string(test_docx_bytes):
    from docx_utils import strip_cutting
    result = strip_cutting(test_docx_bytes)
    assert isinstance(result, str)
    assert len(result) > 0


def test_strip_cutting_is_xml(test_docx_bytes):
    from docx_utils import strip_cutting
    xml = strip_cutting(test_docx_bytes)
    assert xml.startswith("<?xml") or "<w:document" in xml


def test_strip_cutting_removes_underline_style(test_docx_bytes):
    from docx_utils import strip_cutting
    xml = strip_cutting(test_docx_bytes)
    assert 'w:val="StyleUnderline"' not in xml


def test_strip_cutting_removes_highlight_runs(test_docx_bytes):
    from docx_utils import strip_cutting
    xml = strip_cutting(test_docx_bytes)
    assert "<w:highlight" not in xml


# ── extract_text_from_xml ─────────────────────────────────────────────────────

def test_extract_text_nonempty(test_docx_bytes):
    from docx_utils import strip_cutting, extract_text_from_xml
    xml = strip_cutting(test_docx_bytes)
    text = extract_text_from_xml(xml)
    assert isinstance(text, str)
    assert len(text) > 500  # real debate file has substantial text


def test_extract_text_has_newlines(test_docx_bytes):
    from docx_utils import strip_cutting, extract_text_from_xml
    xml = strip_cutting(test_docx_bytes)
    text = extract_text_from_xml(xml)
    assert "\n" in text


def test_extract_text_no_xml_tags(test_docx_bytes):
    from docx_utils import strip_cutting, extract_text_from_xml
    xml = strip_cutting(test_docx_bytes)
    text = extract_text_from_xml(xml)
    assert "<w:" not in text
    assert "</" not in text


# ── parse_cards ───────────────────────────────────────────────────────────────

def test_parse_cards_finds_cards(test_docx_bytes):
    from docx_utils import strip_cutting, extract_text_from_xml
    from ai import parse_cards
    xml = strip_cutting(test_docx_bytes)
    text = extract_text_from_xml(xml)
    cards = parse_cards(text)
    assert len(cards) >= 1, f"Expected at least 1 card, got {len(cards)}"


def test_parse_cards_finds_many_cards(test_docx_bytes):
    """The test file is a full debate round file — should have many cards."""
    from docx_utils import strip_cutting, extract_text_from_xml
    from ai import parse_cards
    xml = strip_cutting(test_docx_bytes)
    text = extract_text_from_xml(xml)
    cards = parse_cards(text)
    assert len(cards) >= 5, f"Expected many cards in a real debate file, got {len(cards)}"


def test_parse_cards_structure(test_docx_bytes):
    """Each card must have tag, cite, and body fields."""
    from docx_utils import strip_cutting, extract_text_from_xml
    from ai import parse_cards
    xml = strip_cutting(test_docx_bytes)
    text = extract_text_from_xml(xml)
    cards = parse_cards(text)
    for card in cards:
        assert "tag" in card, "Card missing 'tag'"
        assert "cite" in card, "Card missing 'cite'"
        assert "body" in card, "Card missing 'body'"


def test_parse_cards_body_nonempty(test_docx_bytes):
    from docx_utils import strip_cutting, extract_text_from_xml
    from ai import parse_cards
    xml = strip_cutting(test_docx_bytes)
    text = extract_text_from_xml(xml)
    cards = parse_cards(text)
    for card in cards:
        assert len(card["body"]) > 80, f"Card body too short: {card['tag'][:60]!r}"


def test_parse_cards_tag_reasonable_length(test_docx_bytes):
    from docx_utils import strip_cutting, extract_text_from_xml
    from ai import parse_cards
    xml = strip_cutting(test_docx_bytes)
    text = extract_text_from_xml(xml)
    cards = parse_cards(text)
    for card in cards:
        assert len(card["tag"]) <= 350, f"Tag too long: {card['tag'][:80]!r}"
        assert len(card["tag"]) > 0, "Tag is empty"


def test_parse_cards_cite_has_date(test_docx_bytes):
    """At least one card's cite line should contain a year."""
    import re
    from docx_utils import strip_cutting, extract_text_from_xml
    from ai import parse_cards
    xml = strip_cutting(test_docx_bytes)
    text = extract_text_from_xml(xml)
    cards = parse_cards(text)
    has_date = any(re.search(r"\d{2,4}", c["cite"]) for c in cards)
    assert has_date, "No card cite contained a year-like number"


def test_parse_cards_full_round_trip(test_docx_bytes):
    """
    Round-trip test: strip → extract text → parse cards → verify total body
    character count is substantial (proves the pipeline didn't silently drop content).
    """
    from docx_utils import strip_cutting, extract_text_from_xml
    from ai import parse_cards
    xml = strip_cutting(test_docx_bytes)
    text = extract_text_from_xml(xml)
    cards = parse_cards(text)
    total_body = sum(len(c["body"]) for c in cards)
    assert total_body > 2000, f"Total parsed body text too short: {total_body} chars"
