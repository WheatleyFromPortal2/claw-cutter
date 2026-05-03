"""
Tests for the docx cutting job API endpoints.

All AI calls during job processing are mocked so tests are deterministic
and don't require a live model server.
"""
import io
import json
import zipfile
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest


def _minimal_docx() -> bytes:
    """Build the smallest valid .docx that parse_cards can process."""
    tag_line = "Resolved: Climate change is an existential threat"
    cite_line = "Smith 24 – John Smith, Professor, January 2024, MIT Press. https://example.com"
    body_line = "Climate change poses an unprecedented risk to human civilization and ecosystems worldwide."

    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        f"<w:p><w:r><w:t>{tag_line}</w:t></w:r></w:p>"
        f"<w:p><w:r><w:t>{cite_line}</w:t></w:r></w:p>"
        f"<w:p><w:r><w:t>{body_line}</w:t></w:r></w:p>"
        "</w:body>"
        "</w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


@pytest.fixture
def docx_bytes():
    return _minimal_docx()


# ── GET /api/jobs (empty) ─────────────────────────────────────────────────────

def test_list_jobs_empty(test_client):
    resp = test_client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


# ── POST /api/jobs ────────────────────────────────────────────────────────────

def test_create_job_returns_queued(test_client, docx_bytes):
    resp = test_client.post(
        "/api/jobs",
        files={"file": ("test.docx", docx_bytes, "application/octet-stream")},
        data={"settings": json.dumps({"mode": "all", "topic": "climate"})},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == "queued"


def test_create_job_appears_in_list(test_client, docx_bytes):
    resp = test_client.post(
        "/api/jobs",
        files={"file": ("test.docx", docx_bytes, "application/octet-stream")},
        data={"settings": json.dumps({"mode": "all"})},
    )
    job_id = resp.json()["job_id"]

    list_resp = test_client.get("/api/jobs")
    ids = [j["id"] for j in list_resp.json()]
    assert job_id in ids


def test_create_job_bad_settings_json(test_client, docx_bytes):
    resp = test_client.post(
        "/api/jobs",
        files={"file": ("test.docx", docx_bytes, "application/octet-stream")},
        data={"settings": "not-json"},
    )
    assert resp.status_code == 400


def test_create_job_file_too_large(test_client):
    big = b"x" * (11 * 1024 * 1024)
    resp = test_client.post(
        "/api/jobs",
        files={"file": ("big.docx", big, "application/octet-stream")},
        data={"settings": "{}"},
    )
    assert resp.status_code == 413


# ── GET /api/jobs/{id} ────────────────────────────────────────────────────────

def test_get_job_details(test_client, docx_bytes):
    create_resp = test_client.post(
        "/api/jobs",
        files={"file": ("detail.docx", docx_bytes, "application/octet-stream")},
        data={"settings": json.dumps({"mode": "all"})},
    )
    job_id = create_resp.json()["job_id"]

    resp = test_client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job_id
    assert body["filename"] == "detail.docx"
    assert "status" in body
    assert "progress" in body
    assert "cards_total" in body
    assert "tokens_input" in body


def test_get_job_not_found(test_client):
    resp = test_client.get("/api/jobs/nonexistent-id")
    assert resp.status_code == 404


# ── GET /api/jobs/{id}/download ───────────────────────────────────────────────

def test_download_job_not_done_returns_400(test_client, docx_bytes):
    create_resp = test_client.post(
        "/api/jobs",
        files={"file": ("dl.docx", docx_bytes, "application/octet-stream")},
        data={"settings": "{}"},
    )
    job_id = create_resp.json()["job_id"]
    resp = test_client.get(f"/api/jobs/{job_id}/download")
    # Job is queued/running/error — download not allowed yet
    assert resp.status_code in (400, 404)


# ── DELETE /api/jobs/{id} ─────────────────────────────────────────────────────

def test_delete_job(test_client, docx_bytes):
    create_resp = test_client.post(
        "/api/jobs",
        files={"file": ("del.docx", docx_bytes, "application/octet-stream")},
        data={"settings": "{}"},
    )
    job_id = create_resp.json()["job_id"]

    del_resp = test_client.delete(f"/api/jobs/{job_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    assert test_client.get(f"/api/jobs/{job_id}").status_code == 404


def test_delete_nonexistent_job(test_client):
    assert test_client.delete("/api/jobs/no-such-job").status_code == 404


# ── GET /api/stats ────────────────────────────────────────────────────────────

def test_get_stats_empty(test_client):
    resp = test_client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["jobs_completed"] == 0
    assert body["tokens_input"] == 0


# ── GET /api/models ───────────────────────────────────────────────────────────

def test_list_models(test_client):
    resp = test_client.get("/api/models")
    assert resp.status_code == 200
    models = resp.json()
    assert isinstance(models, list)
    # At least one model should be configured (qwen-stmarks)
    assert len(models) >= 1
    assert "id" in models[0]
    assert "name" in models[0]
    assert "enabled" in models[0]


# ── GET /api/role ─────────────────────────────────────────────────────────────

def test_get_role_no_tokens_is_admin(test_client):
    # No ADMIN_TOKENS/USER_TOKENS env vars set → auto admin
    resp = test_client.get("/api/role")
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
