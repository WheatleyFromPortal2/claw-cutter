import json
import logging
import os
import re as _re
import time
from datetime import datetime
from pathlib import Path

import httpx

from database import SessionLocal, Job, Project, Card
from docx_utils import strip_cutting, extract_text_from_xml, apply_cuttings, build_output_docx
from ai import (
    parse_cards, underline_card, highlight_card, get_prompts,
    research_project, cut_card_with_context, _generate_search_queries,
)
from search import web_search, search_enabled
from metrics import record_tokens

DATA_DIR = os.getenv("DATA_DIR", "./data")
logger = logging.getLogger(__name__)


async def _fetch_article_text(url: str) -> tuple[str, str]:
    """Fetch URL and return (title, extracted_text). Returns ('', '') on failure."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "LionClaw/1.0"})
            resp.raise_for_status()
            raw_html = resp.text

        title_match = _re.search(r"<title[^>]*>([^<]+)</title>", raw_html, _re.I)
        title = title_match.group(1).strip() if title_match else ""

        clean = _re.sub(r"<script[^>]*>[\s\S]*?</script>", "", raw_html, flags=_re.I)
        clean = _re.sub(r"<style[^>]*>[\s\S]*?</style>", "", clean, flags=_re.I)
        clean = _re.sub(r"<[^>]+>", " ", clean)
        clean = _re.sub(r"&[a-zA-Z]+;", " ", clean)
        clean = _re.sub(r"\s+", " ", clean).strip()
        return title, clean[:8000]
    except Exception:
        return "", ""


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


async def run_research_job(project_id: str) -> None:
    print(f"[research {project_id[:8]}] started", flush=True)
    db = SessionLocal()

    def _log(msg: str):
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            existing = []
            if project.research_log:
                try:
                    existing = json.loads(project.research_log)
                except Exception:
                    pass
            existing.append(msg)
            project.research_log = json.dumps(existing)
            db.commit()
        print(f"[research {project_id[:8]}] {msg}", flush=True)

    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return

        project.research_log = json.dumps([])
        db.commit()

        # Gather real search results if search API key is configured
        search_results = []
        if search_enabled():
            _log("Generating search queries…")
            queries = await _generate_search_queries(
                project.topic or "", project.description or ""
            )
            for q in queries:
                _log(f"Searching: {q}")
                hits = await web_search(q, count=5)
                search_results.extend(hits)
                _log(f"  → {len(hits)} results found")
            _log(f"Total search results: {len(search_results)}")
        else:
            _log("Web search not configured — using AI knowledge only")

        _log("Asking AI to research articles…")
        result, model_id, tokens = await research_project(
            project.name or "",
            project.topic or "",
            project.description or "",
            search_results=search_results or None,
        )
        record_tokens(model_id, tokens.get("input", 0) + tokens.get("output", 0))

        if result.get("error"):
            project.research_status = "error"
            project.research_error = result["error"]
            db.commit()
            return

        project.link_story = result.get("link_story", "")
        project.research_status = "done"
        db.commit()

        articles = result.get("articles", [])
        _log(f"AI suggested {len(articles)} articles — creating cards…")

        project_tag = f"proj::{project.name}"

        def _field(d, key):
            val = d.get(key)
            if val is None:
                return None
            s = str(val).strip()
            return s if s else None

        for i, art in enumerate(articles):
            import uuid
            title = art.get("title", "")
            author = art.get("author", "")
            art_url = _field(art, "url") or ""
            _log(f"  [{i+1}/{len(articles)}] {title or 'Untitled'} — {author or 'Unknown author'}")

            card_text = ""
            full_text_fetched = 0
            if art_url:
                _log(f"    Fetching: {art_url}")
                fetched_title, card_text = await _fetch_article_text(art_url)
                if card_text:
                    full_text_fetched = 1
                    _log(f"    ✓ {len(card_text)} chars fetched")
                    if fetched_title and not title:
                        title = fetched_title
                else:
                    _log(f"    ✗ Could not fetch article text")

            card = Card(
                id=str(uuid.uuid4()),
                project_id=project_id,
                tag=art.get("tag", "") or "",
                author=_field(art, "author"),
                author_qualifications=_field(art, "author_qualifications"),
                date=_field(art, "date"),
                title=_field(art, "title") or title or None,
                publisher=_field(art, "publisher"),
                url=art_url or None,
                initials=_field(art, "initials"),
                topic=project.topic or "",
                tags=json.dumps([project_tag]),
                card_text=card_text,
                full_text_fetched=full_text_fetched,
                card_status="researched",
                created_at=datetime.utcnow(),
            )
            db.add(card)
            db.commit()

        db.commit()
        _log(f"Done — {len(articles)} cards created")
        print(f"[research {project_id[:8]}] done — {len(articles)} cards created", flush=True)

    except Exception as exc:
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                project.research_status = "error"
                project.research_error = str(exc)
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()


async def run_project_cut_job(project_id: str) -> None:
    print(f"[cut {project_id[:8]}] started", flush=True)
    db = SessionLocal()

    def _log(msg: str):
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            existing = []
            if project.cut_log:
                try:
                    existing = json.loads(project.cut_log)
                except Exception:
                    pass
            existing.append(msg)
            project.cut_log = json.dumps(existing)
            db.commit()
        print(f"[cut {project_id[:8]}] {msg}", flush=True)

    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return

        project.cut_log = json.dumps([])
        db.commit()

        approved = (
            db.query(Card)
            .filter(Card.project_id == project_id, Card.card_status == "approved")
            .all()
        )
        if not approved:
            project.cut_status = "done"
            db.commit()
            return

        _log(f"Cutting {len(approved)} approved cards…")
        prompts = get_prompts()

        for i, card in enumerate(approved):
            tag_preview = (card.tag or "Untitled")[:80]
            _log(f"[{i+1}/{len(approved)}] Cutting: {tag_preview}")
            card_dict = {
                "tag": card.tag or "",
                "author": card.author or "",
                "date": card.date or "",
                "title": card.title or "",
                "card_text": card.card_text or "",
            }
            ul_result, hl_result, model_id, ul_tokens, hl_tokens = await cut_card_with_context(
                card_dict,
                project.name or "",
                project.link_story or "",
                project.topic or "",
                prompts["underline"],
                prompts["highlight"],
            )
            record_tokens(model_id, ul_tokens.get("input", 0) + ul_tokens.get("output", 0))
            record_tokens(model_id, hl_tokens.get("input", 0) + hl_tokens.get("output", 0))

            ul_count = len(ul_result.get("underlined", []))
            hl_count = len(hl_result.get("highlighted", []))
            _log(f"  ✓ {ul_count} underlines, {hl_count} highlights")

            card.underlined = json.dumps(ul_result.get("underlined", []))
            card.highlighted = json.dumps(hl_result.get("highlighted", []))
            card.card_status = "cut"
            card.updated_at = datetime.utcnow()
            db.commit()

        project.cut_status = "done"
        db.commit()
        _log(f"Done — {len(approved)} cards cut")
        print(f"[cut {project_id[:8]}] done — {len(approved)} cards cut", flush=True)

    except Exception as exc:
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                project.cut_status = "error"
                project.cut_error = str(exc)
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()
