"""
End-to-end integration test: upload a real debate docx → AI cuts it → download output.

AI calls are mocked to return deterministic results so the test never requires a
live model server. The mock is structured to actually underline/highlight text that
exists in the real docx, so the output file contains real formatting changes.

If this test passes, the entire cutting pipeline works:
  upload → parse docx → AI underline → AI highlight → apply to XML → write output.docx
"""
import io
import json
import zipfile
from unittest.mock import AsyncMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _first_phrase(body: str, max_len: int = 60) -> str:
    """Return the first whitespace-stripped segment of the body up to max_len chars."""
    stripped = body.strip()
    # Find the first complete word boundary within max_len
    if len(stripped) <= max_len:
        return stripped
    end = stripped.rfind(" ", 0, max_len)
    return stripped[:end] if end > 0 else stripped[:max_len]


# ── Integration test ──────────────────────────────────────────────────────────

def test_full_cutting_workflow(integration_client, test_docx_bytes, tmp_path):
    """
    1. Upload the real test docx.
    2. Mock AI to underline/highlight the first phrase of each card body.
    3. Wait for the background job to finish (TestClient runs bg tasks synchronously).
    4. Assert job status == 'done'.
    5. Download the output docx.
    6. Assert it is a valid zip and document.xml contains underline formatting.
    """
    # We need to know what parse_cards will produce from the test file so we can
    # return plausible phrases from the mocked AI.
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
    from docx_utils import strip_cutting, extract_text_from_xml
    from ai import parse_cards

    xml = strip_cutting(test_docx_bytes)
    text = extract_text_from_xml(xml)
    cards = parse_cards(text)
    assert len(cards) >= 1, "Test docx must parse into at least one card"

    # Build a lookup: tag → (underline_phrase, highlight_phrase)
    # We use the first 50 chars of the body as the underline phrase, and the
    # first 20 chars of that as the highlight phrase.
    phrase_map: dict[str, tuple[str, str]] = {}
    for card in cards:
        body = card["body"].strip()
        ul_phrase = _first_phrase(body, 50)
        hl_phrase = _first_phrase(ul_phrase, 20)
        if ul_phrase and hl_phrase and ul_phrase in body:
            phrase_map[card["tag"]] = (ul_phrase, hl_phrase)

    # --- Mock AI calls ---

    async def mock_underline(card, topic, prompt):
        tag = card.get("tag", "")
        if tag in phrase_map:
            ul_phrase, _ = phrase_map[tag]
            return {"relevant": True, "underlined": [ul_phrase]}, "mock-model", {"input": 1, "output": 1}
        return {"relevant": True, "underlined": []}, "mock-model", {"input": 1, "output": 0}

    async def mock_highlight(card, underlined, prompt):
        tag = card.get("tag", "")
        if tag in phrase_map and underlined:
            _, hl_phrase = phrase_map[tag]
            if hl_phrase:
                return {"highlighted": [hl_phrase]}, "mock-model", {"input": 1, "output": 1}
        return {"highlighted": []}, "mock-model", {"input": 1, "output": 0}

    with patch("tasks.underline_card", side_effect=mock_underline), \
         patch("tasks.highlight_card", side_effect=mock_highlight):

        # 1. Upload
        resp = integration_client.post(
            "/api/jobs",
            files={"file": ("test.docx", test_docx_bytes, "application/octet-stream")},
            data={"settings": json.dumps({"mode": "all", "topic": "test"})},
        )
        assert resp.status_code == 200, f"Upload failed: {resp.text}"
        job_id = resp.json()["job_id"]

    # 2. Check job completed (background task ran during the POST above)
    status_resp = integration_client.get(f"/api/jobs/{job_id}")
    assert status_resp.status_code == 200
    job = status_resp.json()

    assert job["status"] == "done", (
        f"Job did not complete. Status: {job['status']}. Error: {job.get('error')}"
    )
    assert job["cards_total"] == len(cards), (
        f"Expected {len(cards)} cards, job recorded {job['cards_total']}"
    )
    assert job["progress"] == 100

    # 3. Download output docx
    dl_resp = integration_client.get(f"/api/jobs/{job_id}/download")
    assert dl_resp.status_code == 200, f"Download failed: {dl_resp.text}"
    assert "wordprocessingml" in dl_resp.headers.get("content-type", "")

    output_bytes = dl_resp.content
    assert len(output_bytes) > 1000, "Output docx suspiciously small"

    # 4. Verify the output is a valid zip / docx
    assert zipfile.is_zipfile(io.BytesIO(output_bytes)), "Output is not a valid zip/docx"

    with zipfile.ZipFile(io.BytesIO(output_bytes)) as zf:
        assert "word/document.xml" in zf.namelist()
        doc_xml = zf.read("word/document.xml").decode("utf-8")

    # 5. Verify underline formatting runs are present in the document XML
    assert 'w:val="StyleUnderline"' in doc_xml, (
        "Output docx does not contain any underlined runs — AI cutting did not apply"
    )

    # 6. Verify the download filename ends with _CUT.docx
    content_disp = dl_resp.headers.get("content-disposition", "")
    assert "_CUT.docx" in content_disp or "CUT" in content_disp


def test_cutting_workflow_all_cards_processed(integration_client, test_docx_bytes):
    """cards_done must equal cards_total when status is done."""
    async def mock_underline(card, topic, prompt):
        return {"relevant": True, "underlined": []}, "mock", {}

    async def mock_highlight(card, underlined, prompt):
        return {"highlighted": []}, "mock", {}

    with patch("tasks.underline_card", side_effect=mock_underline), \
         patch("tasks.highlight_card", side_effect=mock_highlight):
        resp = integration_client.post(
            "/api/jobs",
            files={"file": ("test2.docx", test_docx_bytes, "application/octet-stream")},
            data={"settings": json.dumps({"mode": "all"})},
        )
        job_id = resp.json()["job_id"]

    job = integration_client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done"
    assert job["cards_done"] == job["cards_total"]


def test_cutting_workflow_topic_only_mode(integration_client, test_docx_bytes):
    """In topic_only mode, cards with no underlines are skipped (not cut)."""
    async def mock_underline(card, topic, prompt):
        # Return no underlines → card should be skipped in topic_only mode
        return {"relevant": False, "underlined": []}, "mock", {}

    async def mock_highlight(card, underlined, prompt):
        return {"highlighted": []}, "mock", {}

    with patch("tasks.underline_card", side_effect=mock_underline), \
         patch("tasks.highlight_card", side_effect=mock_highlight):
        resp = integration_client.post(
            "/api/jobs",
            files={"file": ("test3.docx", test_docx_bytes, "application/octet-stream")},
            data={"settings": json.dumps({"mode": "topic_only", "topic": "specific topic"})},
        )
        job_id = resp.json()["job_id"]

    job = integration_client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done"
    # All cards skipped → zero underlines and highlights in the log
    card_log = job.get("card_log", [])
    assert all(entry.get("skipped") for entry in card_log), (
        "Expected all cards to be skipped in topic_only mode with no underlines"
    )


def test_cutting_workflow_card_log_populated(integration_client, test_docx_bytes):
    """card_log should have one entry per card with ul_count, hl_count, skipped."""
    async def mock_underline(card, topic, prompt):
        return {"relevant": True, "underlined": ["some phrase"]}, "mock", {}

    async def mock_highlight(card, underlined, prompt):
        return {"highlighted": ["phrase"]}, "mock", {}

    with patch("tasks.underline_card", side_effect=mock_underline), \
         patch("tasks.highlight_card", side_effect=mock_highlight):
        resp = integration_client.post(
            "/api/jobs",
            files={"file": ("log_test.docx", test_docx_bytes, "application/octet-stream")},
            data={"settings": json.dumps({"mode": "all"})},
        )
        job_id = resp.json()["job_id"]

    job = integration_client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done"
    card_log = job.get("card_log", [])
    assert len(card_log) == job["cards_total"]
    for entry in card_log:
        assert "tag" in entry
        assert "ul_count" in entry
        assert "hl_count" in entry
        assert "skipped" in entry
