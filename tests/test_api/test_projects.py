"""Tests for project CRUD and workflow-trigger endpoints."""
import pytest


# ── POST /api/projects ────────────────────────────────────────────────────────

def test_create_project(test_client):
    resp = test_client.post("/api/projects", json={
        "name": "Climate Neg",
        "topic": "Climate change is an existential threat",
        "description": "Single payer healthcare turns healthcare AI integration",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Climate Neg"
    assert body["topic"] == "Climate change is an existential threat"
    assert body["research_status"] == "idle"
    assert body["cut_status"] == "idle"
    assert "id" in body
    assert body["card_count"] == 0


def test_create_project_minimal(test_client):
    resp = test_client.post("/api/projects", json={"name": "Minimal"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Minimal"
    assert body["topic"] == ""
    assert body["description"] == ""


def test_create_project_missing_name_fails(test_client):
    resp = test_client.post("/api/projects", json={"topic": "No name"})
    assert resp.status_code == 422


# ── GET /api/projects ─────────────────────────────────────────────────────────

def test_list_projects_empty(test_client):
    resp = test_client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_projects_returns_created(test_client):
    test_client.post("/api/projects", json={"name": "Alpha"})
    test_client.post("/api/projects", json={"name": "Beta"})
    resp = test_client.get("/api/projects")
    names = [p["name"] for p in resp.json()]
    assert "Alpha" in names
    assert "Beta" in names


# ── GET /api/projects/{id} ────────────────────────────────────────────────────

def test_get_project(test_client):
    create_resp = test_client.post("/api/projects", json={"name": "Detail Test"})
    project_id = create_resp.json()["id"]

    resp = test_client.get(f"/api/projects/{project_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == project_id


def test_get_project_not_found(test_client):
    resp = test_client.get("/api/projects/nonexistent")
    assert resp.status_code == 404


# ── PATCH /api/projects/{id} ──────────────────────────────────────────────────

def test_update_project_name(test_client):
    project_id = test_client.post("/api/projects", json={"name": "Old Name"}).json()["id"]
    resp = test_client.patch(f"/api/projects/{project_id}", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


def test_update_project_topic_and_description(test_client):
    project_id = test_client.post("/api/projects", json={"name": "P"}).json()["id"]
    resp = test_client.patch(f"/api/projects/{project_id}", json={
        "topic": "Updated topic",
        "description": "Updated description",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["topic"] == "Updated topic"
    assert body["description"] == "Updated description"


def test_update_project_partial(test_client):
    """PATCH should only update supplied fields."""
    project_id = test_client.post("/api/projects", json={
        "name": "Partial",
        "topic": "Original topic",
    }).json()["id"]

    test_client.patch(f"/api/projects/{project_id}", json={"name": "Renamed"})
    resp = test_client.get(f"/api/projects/{project_id}")
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["topic"] == "Original topic"  # unchanged


def test_update_project_not_found(test_client):
    resp = test_client.patch("/api/projects/no-such", json={"name": "X"})
    assert resp.status_code == 404


# ── DELETE /api/projects/{id} ─────────────────────────────────────────────────

def test_delete_project(test_client):
    project_id = test_client.post("/api/projects", json={"name": "ToDelete"}).json()["id"]
    del_resp = test_client.delete(f"/api/projects/{project_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"
    assert test_client.get(f"/api/projects/{project_id}").status_code == 404


def test_delete_project_cascades_cards(test_client, db_engine):
    """Deleting a project must also delete its cards."""
    import json, uuid
    from datetime import datetime
    from sqlalchemy.orm import sessionmaker

    project_id = test_client.post("/api/projects", json={"name": "WithCards"}).json()["id"]

    # Insert cards directly into DB
    from database import Card
    Session = sessionmaker(bind=db_engine)
    s = Session()
    for _ in range(3):
        s.add(Card(
            id=str(uuid.uuid4()),
            project_id=project_id,
            tag="Tag",
            card_status="researched",
            created_at=datetime.utcnow(),
        ))
    s.commit()
    s.close()

    test_client.delete(f"/api/projects/{project_id}")

    s = Session()
    remaining = s.query(Card).filter(Card.project_id == project_id).count()
    s.close()
    assert remaining == 0


# ── POST /api/projects/{id}/research (trigger only — not full run) ───────────

def test_start_research_returns_started(test_client):
    project_id = test_client.post("/api/projects", json={
        "name": "Research Me",
        "topic": "Climate",
        "description": "Test research trigger",
    }).json()["id"]

    # We do NOT mock AI here — we just check the endpoint triggers correctly
    # and returns the expected response. The background task runs but may fail
    # due to no model server; that's acceptable for this trigger test.
    resp = test_client.post(f"/api/projects/{project_id}/research")
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_start_research_nonexistent_project(test_client):
    resp = test_client.post("/api/projects/no-such/research")
    assert resp.status_code == 404


def test_start_cut_returns_started(test_client):
    project_id = test_client.post("/api/projects", json={"name": "Cut Me"}).json()["id"]
    resp = test_client.post(f"/api/projects/{project_id}/cut")
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
