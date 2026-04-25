import json
import logging
import os
from pathlib import Path

from database import SessionLocal, Job
from docx_utils import strip_tracing, extract_text_from_xml, apply_tracings, build_output_docx
from ai import parse_cards, underline_card, highlight_card, UNDERLINE_PROMPT, HIGHLIGHT_PROMPT

DATA_DIR = os.getenv("DATA_DIR", "./data")
logger = logging.getLogger(__name__)


async def run_tracing_job(job_id: str) -> None:
    print(f"[job {job_id[:8]}] background task started", flush=True)
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            print(f"[job {job_id[:8]}] job not found in DB", flush=True)
            return

        job.status = "running"
        db.commit()

        input_path = Path(DATA_DIR) / job_id / "input.docx"
        with open(input_path, "rb") as f:
            docx_bytes = f.read()

        settings = json.loads(job.settings)
        hl_color = settings.get("hl_color", "cyan")
        topic = settings.get("topic", "")
        mode = settings.get("mode", "all")
        underline_prompt = settings.get("underline_prompt") or UNDERLINE_PROMPT
        highlight_prompt = settings.get("highlight_prompt") or HIGHLIGHT_PROMPT

        stripped_xml = strip_tracing(docx_bytes)
        raw_text = extract_text_from_xml(stripped_xml)

        print(f"[job {job_id[:8]}] extracted {len(raw_text)} chars from docx", flush=True)
        print(f"[job {job_id[:8]}] text sample:\n{raw_text[:800]}", flush=True)

        cards = parse_cards(raw_text)
        print(f"[job {job_id[:8]}] parsed {len(cards)} cards", flush=True)
        for i, c in enumerate(cards[:5]):
            print(f"[job {job_id[:8]}] card {i+1}: tag={c['tag'][:80]!r}  body_len={len(c['body'])}", flush=True)

        job.cards_total = len(cards)
        job.cards_done = 0
        job.card_log = json.dumps([])
        db.commit()

        tracings = []
        card_log = []

        for card in cards:
            underline_result, ul_model = await underline_card(card, topic, underline_prompt)
            relevant = underline_result.get("relevant", False)
            underlined = underline_result.get("underlined", [])

            # "all" mode traces regardless of relevance flag; "topic_only" respects it
            should_trace = bool(underlined) and (mode == "all" or relevant)

            if should_trace:
                highlight_result, hl_model = await highlight_card(card, underlined, highlight_prompt)
                highlighted = highlight_result.get("highlighted", [])
                tracings.append(
                    {
                        "tag": card["tag"],
                        "underlined": underlined,
                        "highlighted": highlighted,
                        "skip": False,
                    }
                )
                card_log.append(
                    {
                        "tag": card["tag"][:120],
                        "ul_count": len(underlined),
                        "hl_count": len(highlighted),
                        "skipped": False,
                        "model": ul_model,
                    }
                )
            else:
                tracings.append(
                    {
                        "tag": card["tag"],
                        "underlined": [],
                        "highlighted": [],
                        "skip": True,
                    }
                )
                card_log.append(
                    {
                        "tag": card["tag"][:120],
                        "ul_count": 0,
                        "hl_count": 0,
                        "skipped": True,
                        "model": ul_model,
                    }
                )

            job.cards_done += 1
            job.progress = int((job.cards_done / max(job.cards_total, 1)) * 100)
            job.card_log = json.dumps(card_log)
            db.commit()

        traced_xml = apply_tracings(stripped_xml, tracings, hl_color)
        output_bytes = build_output_docx(docx_bytes, traced_xml)

        output_path = Path(DATA_DIR) / job_id / "output.docx"
        with open(output_path, "wb") as f:
            f.write(output_bytes)

        job.status = "done"
        job.progress = 100
        db.commit()

    except Exception as e:
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = "error"
                job.error = str(e)
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()
