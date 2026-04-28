import json
import logging
import os
import time
from pathlib import Path

from database import SessionLocal, Job
from docx_utils import strip_cutting, extract_text_from_xml, apply_cuttings, build_output_docx
from ai import parse_cards, underline_card, highlight_card, get_prompts
from metrics import record_tokens

DATA_DIR = os.getenv("DATA_DIR", "./data")
logger = logging.getLogger(__name__)


async def run_cutting_job(job_id: str) -> None:
    print(f"[job {job_id[:8]}] background task started", flush=True)
    db = SessionLocal()
    start_time = time.time()
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

        job.filesize = len(docx_bytes)
        db.commit()

        settings = json.loads(job.settings)
        hl_color = settings.get("hl_color", "cyan")
        topic = settings.get("topic", "")
        mode = settings.get("mode", "all")
        prompts = get_prompts()
        underline_prompt = settings.get("underline_prompt") or prompts["underline"]
        highlight_prompt = settings.get("highlight_prompt") or prompts["highlight"]

        stripped_xml = strip_cutting(docx_bytes)
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

        cuttings = []
        card_log = []
        total_input_tokens = 0
        total_output_tokens = 0

        for card in cards:
            underline_result, ul_model, ul_tokens = await underline_card(card, topic, underline_prompt)
            ul_in = ul_tokens.get("input", 0)
            ul_out = ul_tokens.get("output", 0)
            total_input_tokens += ul_in
            total_output_tokens += ul_out
            record_tokens(ul_model, ul_in + ul_out)

            relevant = underline_result.get("relevant", False)
            underlined = underline_result.get("underlined", [])

            # "all" mode cuts every card regardless of relevance flag; "topic_only" respects it
            should_cut = mode == "all" or (bool(underlined) and relevant)

            if should_cut:
                if underlined:
                    highlight_result, hl_model, hl_tokens = await highlight_card(card, underlined, highlight_prompt)
                    hl_in = hl_tokens.get("input", 0)
                    hl_out = hl_tokens.get("output", 0)
                    total_input_tokens += hl_in
                    total_output_tokens += hl_out
                    record_tokens(hl_model, hl_in + hl_out)
                    highlighted = highlight_result.get("highlighted", [])
                else:
                    highlighted = []
                cuttings.append(
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
                cuttings.append(
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

        cut_xml = apply_cuttings(stripped_xml, cuttings, hl_color)
        output_bytes = build_output_docx(docx_bytes, cut_xml)

        output_path = Path(DATA_DIR) / job_id / "output.docx"
        with open(output_path, "wb") as f:
            f.write(output_bytes)

        job.tokens_input = total_input_tokens
        job.tokens_output = total_output_tokens
        job.processing_secs = time.time() - start_time
        job.status = "done"
        job.progress = 100
        db.commit()

    except Exception as e:
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = "error"
                job.error = str(e)
                job.processing_secs = time.time() - start_time
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()
