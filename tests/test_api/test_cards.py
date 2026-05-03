"""Tests for card CRUD, status transitions, bulk operations, export, and cite endpoints."""
import io
import json
import uuid
import zipfile
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import sessionmaker


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_project(test_client, name="Test Project"):
    return test_client.post("/api/projects", json={"name": name}).json()["id"]


def _insert_card(db_engine, project_id, **kwargs):
    """Insert a Card directly into the DB, bypassing the API."""
    from database import Card
    Session = sessionmaker(bind=db_engine)
    s = Session()
    card = Card(
        id=str(uuid.uuid4()),
        project_id=project_id,
        tag=kwargs.get("tag", "Default tag"),
        author=kwargs.get("author", None),
        date=kwargs.get("date", None),
        title=kwargs.get("title", None),
        card_text=kwargs.get("card_text", "Body text."),
        card_status=kwargs.get("card_status", "researched"),
        missing_full_text=kwargs.get("missing_full_text", False),
        created_at=datetime.utcnow(),
    )
    s.add(card)
    s.commit()
    card_id = card.id
    s.close()
    return card_id


# ── GET /api/projects/{id}/cards ──────────────────────────────────────────────

def test_list_cards_empty(test_client):
    project_id = _create_project(test_client)
    resp = test_client.get(f"/api/projects/{project_id}/cards")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_cards_returns_all(test_client, db_engine):
    project_id = _create_project(test_client)
    for i in range(3):
        _insert_card(db_engine, project_id, tag=f"Card {i}")

    resp = test_client.get(f"/api/projects/{project_id}/cards")
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_list_cards_filter_by_status(test_client, db_engine):
    project_id = _create_project(test_client)
    _insert_card(db_engine, project_id, tag="Researched", card_status="researched")
    _insert_card(db_engine, project_id, tag="Approved", card_status="approved")
    _insert_card(db_engine, project_id, tag="Trashed", card_status="trashed")

    resp = test_client.get(f"/api/projects/{project_id}/cards?card_status=approved")
    cards = resp.json()
    assert len(cards) == 1
    assert cards[0]["card_status"] == "approved"


def test_list_cards_search_query(test_client, db_engine):
    project_id = _create_project(test_client)
    _insert_card(db_engine, project_id, tag="Climate extinction link", author="Smith, John")
    _insert_card(db_engine, project_id, tag="Economy impacts only")

    resp = test_client.get(f"/api/projects/{project_id}/cards?q=Smith")
    cards = resp.json()
    assert len(cards) == 1
    assert "Smith" in cards[0]["author"]


# ── GET /api/cards/{id} ───────────────────────────────────────────────────────

def test_get_card(test_client, db_engine):
    project_id = _create_project(test_client)
    card_id = _insert_card(db_engine, project_id, tag="Specific card", author="Jones, Alice")

    resp = test_client.get(f"/api/cards/{card_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == card_id
    assert body["tag"] == "Specific card"
    assert body["author"] == "Jones, Alice"
    assert "card_status" in body
    assert "missing_full_text" in body
    assert "underlined" in body
    assert "highlighted" in body


def test_get_card_not_found(test_client):
    resp = test_client.get("/api/cards/no-such-card")
    assert resp.status_code == 404


# ── PATCH /api/cards/{id} ─────────────────────────────────────────────────────

def test_update_card_fields(test_client, db_engine):
    project_id = _create_project(test_client)
    card_id = _insert_card(db_engine, project_id)

    resp = test_client.patch(f"/api/cards/{card_id}", json={
        "tag": "Updated tag",
        "author": "Doe, Jane",
        "date": "2025",
        "title": "New Title",
        "publisher": "MIT Press",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["tag"] == "Updated tag"
    assert body["author"] == "Doe, Jane"
    assert body["date"] == "2025"
    assert body["title"] == "New Title"
    assert body["publisher"] == "MIT Press"


def test_update_card_text(test_client, db_engine):
    project_id = _create_project(test_client)
    card_id = _insert_card(db_engine, project_id)

    resp = test_client.patch(f"/api/cards/{card_id}", json={"card_text": "New verbatim text."})
    assert resp.status_code == 200
    assert resp.json()["card_text"] == "New verbatim text."


def test_update_card_underlined_highlighted(test_client, db_engine):
    project_id = _create_project(test_client)
    card_id = _insert_card(db_engine, project_id, card_text="The quick brown fox jumps over the lazy dog")

    resp = test_client.patch(f"/api/cards/{card_id}", json={
        "underlined": ["quick brown fox"],
        "highlighted": ["fox"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "quick brown fox" in body["underlined"]
    assert "fox" in body["highlighted"]


def test_update_card_not_found(test_client):
    resp = test_client.patch("/api/cards/no-such", json={"tag": "x"})
    assert resp.status_code == 404


# ── POST /api/cards/{id}/approve ─────────────────────────────────────────────

def test_approve_card(test_client, db_engine):
    project_id = _create_project(test_client)
    card_id = _insert_card(db_engine, project_id, card_status="researched")

    resp = test_client.post(f"/api/cards/{card_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["card_status"] == "approved"

    get_resp = test_client.get(f"/api/cards/{card_id}")
    assert get_resp.json()["card_status"] == "approved"


# ── POST /api/cards/{id}/trash ────────────────────────────────────────────────

def test_trash_card(test_client, db_engine):
    project_id = _create_project(test_client)
    card_id = _insert_card(db_engine, project_id, card_status="researched")

    resp = test_client.post(f"/api/cards/{card_id}/trash")
    assert resp.status_code == 200
    assert resp.json()["card_status"] == "trashed"


# ── POST /api/cards/{id}/restore ─────────────────────────────────────────────

def test_restore_card(test_client, db_engine):
    project_id = _create_project(test_client)
    card_id = _insert_card(db_engine, project_id, card_status="trashed")

    resp = test_client.post(f"/api/cards/{card_id}/restore")
    assert resp.status_code == 200
    assert resp.json()["card_status"] == "researched"


def test_approve_trash_restore_cycle(test_client, db_engine):
    project_id = _create_project(test_client)
    card_id = _insert_card(db_engine, project_id)

    test_client.post(f"/api/cards/{card_id}/approve")
    assert test_client.get(f"/api/cards/{card_id}").json()["card_status"] == "approved"

    test_client.post(f"/api/cards/{card_id}/trash")
    assert test_client.get(f"/api/cards/{card_id}").json()["card_status"] == "trashed"

    test_client.post(f"/api/cards/{card_id}/restore")
    assert test_client.get(f"/api/cards/{card_id}").json()["card_status"] == "researched"


# ── POST /api/projects/{id}/cards/approve-all ─────────────────────────────────

def test_approve_all_cards(test_client, db_engine):
    project_id = _create_project(test_client)
    for _ in range(4):
        _insert_card(db_engine, project_id, card_status="researched")
    _insert_card(db_engine, project_id, card_status="trashed")  # should not be approved

    resp = test_client.post(f"/api/projects/{project_id}/cards/approve-all")
    assert resp.status_code == 200
    assert resp.json()["approved"] == 4

    cards = test_client.get(f"/api/projects/{project_id}/cards?card_status=approved").json()
    assert len(cards) == 4


# ── POST /api/projects/{id}/cards/trash-unapproved ───────────────────────────

def test_trash_unapproved_cards(test_client, db_engine):
    project_id = _create_project(test_client)
    for _ in range(3):
        _insert_card(db_engine, project_id, card_status="researched")
    _insert_card(db_engine, project_id, card_status="approved")  # should not be trashed

    resp = test_client.post(f"/api/projects/{project_id}/cards/trash-unapproved")
    assert resp.status_code == 200
    assert resp.json()["trashed"] == 3

    trashed = test_client.get(f"/api/projects/{project_id}/cards?card_status=trashed").json()
    assert len(trashed) == 3
    approved = test_client.get(f"/api/projects/{project_id}/cards?card_status=approved").json()
    assert len(approved) == 1


# ── POST /api/cards/export ────────────────────────────────────────────────────

def test_export_cards_returns_docx(test_client, db_engine):
    project_id = _create_project(test_client)
    card_id = _insert_card(
        db_engine, project_id,
        tag="Export test card",
        card_text="This text should appear in the export.",
    )

    resp = test_client.post("/api/cards/export", json={
        "card_ids": [card_id],
        "hl_color": "cyan",
    })
    assert resp.status_code == 200
    assert "wordprocessingml" in resp.headers.get("content-type", "")

    # Verify the response bytes are a valid docx (zip)
    docx_bytes = resp.content
    assert len(docx_bytes) > 100
    buf = io.BytesIO(docx_bytes)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        assert "word/document.xml" in names
        doc_xml = zf.read("word/document.xml").decode()
        assert "Export test card" in doc_xml


def test_export_no_cards_found(test_client):
    resp = test_client.post("/api/cards/export", json={
        "card_ids": ["nonexistent-id"],
        "hl_color": "cyan",
    })
    assert resp.status_code == 404


def test_export_preserves_order(test_client, db_engine):
    project_id = _create_project(test_client)
    ids = [
        _insert_card(db_engine, project_id, tag=f"Card {i}", card_text=f"Text {i}.")
        for i in range(3)
    ]
    reversed_ids = list(reversed(ids))

    resp = test_client.post("/api/cards/export", json={"card_ids": reversed_ids})
    assert resp.status_code == 200

    buf = io.BytesIO(resp.content)
    with zipfile.ZipFile(buf) as zf:
        doc_xml = zf.read("word/document.xml").decode()

    # Reversed order: Card 2 should appear before Card 0 in the XML
    pos = [doc_xml.find(f"Card {i}") for i in [2, 1, 0]]
    assert pos[0] < pos[1] < pos[2], "Cards not in requested export order"


# ── POST /api/cards/{id}/cite-verbatim (mocked AI) ───────────────────────────

def test_cite_verbatim_populates_fields(test_client, db_engine):
    project_id = _create_project(test_client)
    card_id = _insert_card(db_engine, project_id)

    mock_cite = {
        "author": "Smith, John",
        "author_qualifications": "Professor at Harvard",
        "date": "2024",
        "title": "AI and Healthcare",
        "publisher": "MIT Press",
        "url": "https://example.com/article",
        "initials": "JS",
    }
    with patch("main.parse_verbatim_cite", new=AsyncMock(return_value=(mock_cite, "mock", {}))):
        resp = test_client.post(f"/api/cards/{card_id}/cite-verbatim", json={
            "cite_text": "Smith 24 – John Smith, Professor at Harvard, 2024, \"AI and Healthcare,\" MIT Press. https://example.com/article"
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["author"] == "Smith, John"
    assert body["date"] == "2024"
    assert body["title"] == "AI and Healthcare"
    assert body["initials"] == "JS"


def test_cite_verbatim_card_not_found(test_client):
    resp = test_client.post("/api/cards/no-such/cite-verbatim", json={"cite_text": "x"})
    assert resp.status_code == 404


# ── POST /api/cards/{id}/cite (mocked AI) ────────────────────────────────────

def test_generate_cite_populates_fields(test_client, db_engine):
    project_id = _create_project(test_client)
    card_id = _insert_card(db_engine, project_id)

    mock_cite = {
        "author": "Doe, Jane",
        "date": "2023",
        "title": "Policy Analysis",
        "publisher": "Brookings",
        "url": "",
        "initials": "JD",
    }
    with patch("main.generate_cite", new=AsyncMock(return_value=(mock_cite, "mock", {}))):
        resp = test_client.post(f"/api/cards/{card_id}/cite", json={
            "article_text": "Jane Doe, Brookings Institution, 2023. Policy Analysis..."
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["author"] == "Doe, Jane"
    assert body["date"] == "2023"
