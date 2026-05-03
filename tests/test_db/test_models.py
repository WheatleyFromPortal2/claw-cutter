"""
Tests for SQLAlchemy ORM models: Job, Project, Card.

Verifies that all fields persist correctly, JSON round-trips work,
status transitions are valid, and the schema matches what the API layer expects.
"""
import json
import uuid
from datetime import datetime, timedelta

import pytest


# ── Job model ────────────────────────────────────────────────────────────────

def test_job_create_defaults(db_session):
    from database import Job

    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    job = Job(
        id=job_id,
        created_at=now,
        status="queued",
        progress=0,
        cards_total=0,
        cards_done=0,
        filename="test.docx",
        settings=json.dumps({"mode": "all"}),
        expires_at=now + timedelta(hours=2),
    )
    db_session.add(job)
    db_session.commit()

    fetched = db_session.query(Job).filter(Job.id == job_id).first()
    assert fetched is not None
    assert fetched.status == "queued"
    assert fetched.progress == 0
    assert fetched.filename == "test.docx"
    assert fetched.error is None
    assert fetched.card_log is None
    assert fetched.tokens_input == 0
    assert fetched.tokens_output == 0


def test_job_status_transitions(db_session):
    from database import Job

    job = Job(
        id=str(uuid.uuid4()),
        created_at=datetime.utcnow(),
        status="queued",
        progress=0,
        cards_total=5,
        cards_done=0,
        filename="f.docx",
        settings="{}",
        expires_at=datetime.utcnow() + timedelta(hours=2),
    )
    db_session.add(job)
    db_session.commit()

    job.status = "running"
    job.progress = 50
    job.cards_done = 2
    db_session.commit()
    assert db_session.query(Job).filter(Job.id == job.id).first().status == "running"

    job.status = "done"
    job.progress = 100
    job.cards_done = 5
    job.tokens_input = 1000
    job.tokens_output = 500
    job.processing_secs = 3.14
    db_session.commit()

    fetched = db_session.query(Job).filter(Job.id == job.id).first()
    assert fetched.status == "done"
    assert fetched.progress == 100
    assert fetched.tokens_input == 1000
    assert fetched.processing_secs == pytest.approx(3.14, rel=1e-3)


def test_job_card_log_json(db_session):
    from database import Job

    log = [{"tag": "Card 1", "ul_count": 3, "hl_count": 1, "skipped": False, "model": "qwen"}]
    job = Job(
        id=str(uuid.uuid4()),
        created_at=datetime.utcnow(),
        status="done",
        progress=100,
        cards_total=1,
        cards_done=1,
        filename="f.docx",
        settings="{}",
        expires_at=datetime.utcnow() + timedelta(hours=2),
        card_log=json.dumps(log),
    )
    db_session.add(job)
    db_session.commit()

    fetched = db_session.query(Job).filter(Job.id == job.id).first()
    parsed = json.loads(fetched.card_log)
    assert len(parsed) == 1
    assert parsed[0]["ul_count"] == 3
    assert parsed[0]["model"] == "qwen"


def test_job_error_field(db_session):
    from database import Job

    job = Job(
        id=str(uuid.uuid4()),
        created_at=datetime.utcnow(),
        status="error",
        progress=0,
        cards_total=0,
        cards_done=0,
        filename="bad.docx",
        settings="{}",
        expires_at=datetime.utcnow() + timedelta(hours=2),
        error="File could not be parsed",
    )
    db_session.add(job)
    db_session.commit()

    fetched = db_session.query(Job).filter(Job.id == job.id).first()
    assert fetched.error == "File could not be parsed"


# ── Project model ─────────────────────────────────────────────────────────────

def test_project_create(db_session):
    from database import Project

    proj = Project(
        id=str(uuid.uuid4()),
        name="Single Payer",
        topic="Healthcare",
        description="Universal healthcare coverage",
        research_status="idle",
        cut_status="idle",
        status="active",
        created_at=datetime.utcnow(),
    )
    db_session.add(proj)
    db_session.commit()

    fetched = db_session.query(Project).filter(Project.name == "Single Payer").first()
    assert fetched is not None
    assert fetched.topic == "Healthcare"
    assert fetched.research_status == "idle"
    assert fetched.cut_status == "idle"
    assert fetched.link_story is None
    assert fetched.research_error is None


def test_project_research_status_cycle(db_session):
    from database import Project

    proj = Project(
        id=str(uuid.uuid4()),
        name="Test Project",
        research_status="idle",
        cut_status="idle",
        status="active",
        created_at=datetime.utcnow(),
    )
    db_session.add(proj)
    db_session.commit()

    for status in ("running", "done", "error"):
        proj.research_status = status
        db_session.commit()
        assert db_session.query(Project).filter(Project.id == proj.id).first().research_status == status


def test_project_log_fields(db_session):
    from database import Project

    log = [{"ts": "2026-01-01T00:00:00", "msg": "Starting…"}]
    proj = Project(
        id=str(uuid.uuid4()),
        name="Logged Project",
        research_status="done",
        cut_status="idle",
        status="active",
        created_at=datetime.utcnow(),
        research_log=json.dumps(log),
        link_story="A causes B causes C.",
    )
    db_session.add(proj)
    db_session.commit()

    fetched = db_session.query(Project).filter(Project.id == proj.id).first()
    parsed_log = json.loads(fetched.research_log)
    assert parsed_log[0]["msg"] == "Starting…"
    assert fetched.link_story == "A causes B causes C."


# ── Card model ────────────────────────────────────────────────────────────────

def _make_project(db_session):
    from database import Project
    proj = Project(
        id=str(uuid.uuid4()),
        name="Test Project",
        research_status="idle",
        cut_status="idle",
        status="active",
        created_at=datetime.utcnow(),
    )
    db_session.add(proj)
    db_session.commit()
    return proj


def test_card_create_full(db_session):
    from database import Card

    proj = _make_project(db_session)
    card = Card(
        id=str(uuid.uuid4()),
        project_id=proj.id,
        tag="Climate change causes extinction",
        author="Smith, John",
        author_qualifications="Professor of Climate Science at MIT",
        date="2024",
        title="Climate Tipping Points",
        publisher="Nature",
        url="https://nature.com/article",
        initials="JS",
        topic="Climate",
        tags=json.dumps(["proj::Test Project"]),
        card_text="The Earth is warming at an unprecedented rate.",
        underlined=json.dumps(["Earth is warming"]),
        highlighted=json.dumps(["unprecedented rate"]),
        missing_full_text=False,
        card_status="researched",
        created_at=datetime.utcnow(),
    )
    db_session.add(card)
    db_session.commit()

    fetched = db_session.query(Card).filter(Card.id == card.id).first()
    assert fetched.author == "Smith, John"
    assert fetched.card_status == "researched"
    assert fetched.missing_full_text == False
    assert json.loads(fetched.underlined) == ["Earth is warming"]
    assert json.loads(fetched.highlighted) == ["unprecedented rate"]
    assert json.loads(fetched.tags) == ["proj::Test Project"]


def test_card_null_cite_fields(db_session):
    """Null cite fields mean 'unknown' — distinct from empty string."""
    from database import Card

    proj = _make_project(db_session)
    card = Card(
        id=str(uuid.uuid4()),
        project_id=proj.id,
        tag="Some tag",
        author=None,
        author_qualifications=None,
        date=None,
        title=None,
        publisher=None,
        initials=None,
        card_text="",
        missing_full_text=True,
        card_status="researched",
        created_at=datetime.utcnow(),
    )
    db_session.add(card)
    db_session.commit()

    fetched = db_session.query(Card).filter(Card.id == card.id).first()
    assert fetched.author is None
    assert fetched.date is None
    assert fetched.missing_full_text == True


def test_card_status_transitions(db_session):
    from database import Card

    proj = _make_project(db_session)
    card = Card(
        id=str(uuid.uuid4()),
        project_id=proj.id,
        tag="Tag",
        card_text="Body text.",
        card_status="researched",
        created_at=datetime.utcnow(),
    )
    db_session.add(card)
    db_session.commit()

    for status in ("approved", "cut", "trashed", "researched"):
        card.card_status = status
        db_session.commit()
        assert db_session.query(Card).filter(Card.id == card.id).first().card_status == status


def test_card_underlined_highlighted_empty_lists(db_session):
    """Cards with no underlines/highlights should store valid empty JSON arrays."""
    from database import Card

    proj = _make_project(db_session)
    card = Card(
        id=str(uuid.uuid4()),
        project_id=proj.id,
        tag="Bare card",
        card_text="Some text.",
        underlined=json.dumps([]),
        highlighted=json.dumps([]),
        card_status="researched",
        created_at=datetime.utcnow(),
    )
    db_session.add(card)
    db_session.commit()

    fetched = db_session.query(Card).filter(Card.id == card.id).first()
    assert json.loads(fetched.underlined) == []
    assert json.loads(fetched.highlighted) == []


def test_multiple_cards_per_project(db_session):
    from database import Card

    proj = _make_project(db_session)
    for i in range(5):
        db_session.add(Card(
            id=str(uuid.uuid4()),
            project_id=proj.id,
            tag=f"Card {i}",
            card_status="researched",
            created_at=datetime.utcnow(),
        ))
    db_session.commit()

    count = db_session.query(Card).filter(Card.project_id == proj.id).count()
    assert count == 5
