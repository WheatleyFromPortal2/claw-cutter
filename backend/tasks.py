import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from database import SessionLocal, Job, Project, Card
from docx_utils import strip_cutting, extract_text_from_xml, apply_cuttings, build_output_docx
from ai import (
    parse_cards, underline_card, highlight_card, get_prompts,
    research_project, cut_card_with_context, review_and_refine_cutting,
    _generate_search_queries, triage_search_results,
)
from utils import get_git_commit, normalize_date
from search import web_search, search_enabled, get_search_stats
from metrics import record_tokens

DATA_DIR = os.getenv("DATA_DIR", "./data")
logger = logging.getLogger(__name__)

MAX_REFINE_ROUNDS = 3  # refinement passes after the initial cut


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

                    for _ in range(MAX_REFINE_ROUNDS):
                        refine_result, ref_model, ref_tokens = await review_and_refine_cutting(
                            card, topic, underlined, highlighted, underline_prompt, highlight_prompt
                        )
                        ref_in = ref_tokens.get("input", 0)
                        ref_out = ref_tokens.get("output", 0)
                        total_input_tokens += ref_in
                        total_output_tokens += ref_out
                        record_tokens(ref_model, ref_in + ref_out)
                        if refine_result.get("satisfied", True):
                            break
                        new_ul = refine_result.get("underlined", underlined)
                        new_hl = refine_result.get("highlighted", highlighted)
                        if sorted(new_ul) == sorted(underlined) and sorted(new_hl) == sorted(highlighted):
                            break
                        underlined = new_ul
                        highlighted = new_hl
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


async def run_research_job(project_id: str, min_articles: int = 10) -> None:
    print(f"[research {project_id[:8]}] started", flush=True)
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return

        research_log: list[dict] = []

        def add_log(msg: str) -> None:
            entry = {"ts": datetime.utcnow().isoformat(), "msg": msg}
            research_log.append(entry)
            project.research_log = json.dumps(research_log)
            db.commit()
            print(f"[research {project_id[:8]}] {msg}", flush=True)

        add_log("Starting research job…")

        # Iteratively search and triage until we have enough high-quality results
        MIN_GOOD_RESULTS = min(max(1, min_articles), 100)
        MAX_ROUNDS = 5

        triaged_results: list[dict] = []
        seen_urls: set[str] = set()
        all_queries: list[str] = []

        if search_enabled():
            for round_num in range(1, MAX_ROUNDS + 1):
                need = MIN_GOOD_RESULTS - len(triaged_results)
                add_log(f"Search round {round_num}/{MAX_ROUNDS} — need {need} more good results…")
                queries = await _generate_search_queries(
                    project.topic or "", project.description or "", exclude=all_queries
                )
                all_queries.extend(queries)
                add_log(f"  Queries: {', '.join(q[:60] for q in queries)}")

                round_done = False
                for q in queries:
                    add_log(f"  Searching: {q[:80]}")
                    hits = await web_search(q, count=50)

                    # Warn if the search was rate-limited (limiter already waited)
                    rl = get_search_stats()
                    if rl.get("last_rate_limit_event"):
                        evt = rl["last_rate_limit_event"]
                        add_log(f"  ⚠ Rate limit hit: {evt['reason']} (waited {evt['waited_secs']:.0f}s)")

                    new_hits = [h for h in hits if h.get("url") not in seen_urls]
                    seen_urls.update(h.get("url", "") for h in new_hits)
                    add_log(f"    → {len(hits)} results, {len(new_hits)} new after dedup")

                    if new_hits:
                        add_log(f"    Triaging {len(new_hits)} results…")
                        kept = await triage_search_results(
                            new_hits, project.topic or "", project.description or ""
                        )
                        triaged_results.extend(kept)
                        add_log(
                            f"    Kept {len(kept)}/{len(new_hits)} — "
                            f"total {len(triaged_results)} good results so far"
                        )

                        if len(triaged_results) >= MIN_GOOD_RESULTS:
                            add_log(
                                f"  Target reached ({len(triaged_results)} ≥ {MIN_GOOD_RESULTS})"
                                f" — stopping early"
                            )
                            round_done = True
                            break
                    else:
                        add_log(f"    No new results to triage")

                if round_done or len(triaged_results) >= MIN_GOOD_RESULTS:
                    break
            else:
                add_log(f"Reached max search rounds with {len(triaged_results)} triaged results")
        else:
            add_log("Web search not configured — using AI knowledge only")

        search_results = triaged_results
        add_log(f"Researching with AI ({len(search_results)} triaged search results)…")
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
        db.commit()

        articles = result.get("articles", [])
        add_log(f"AI suggested {len(articles)} articles — creating cards…")

        project_tag = f"proj::{project.name}"

        import uuid
        auto_initials = f"AI traced by lionclaw ({get_git_commit()})"
        for art in articles:
            url = art.get("url") or ""
            card = Card(
                id=str(uuid.uuid4()),
                project_id=project_id,
                tag=art.get("tag") or "",
                author=art.get("author"),
                author_qualifications=art.get("author_qualifications"),
                date=normalize_date(art.get("date")),
                title=art.get("title"),
                publisher=art.get("publisher"),
                url=url,
                initials=auto_initials,
                topic=project.topic or "",
                tags=json.dumps([project_tag]),
                card_text="",
                missing_full_text=True,
                card_status="researched",
                created_at=datetime.utcnow(),
            )
            db.add(card)
            db.commit()
            add_log(f"  Created card: {(art.get('title') or art.get('tag') or '')[:60]}")

        project.research_status = "done"
        add_log(f"Research complete — {len(articles)} cards created")
        db.commit()

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
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return

        approved = (
            db.query(Card)
            .filter(Card.project_id == project_id, Card.card_status == "approved")
            .all()
        )
        if not approved:
            project.cut_status = "done"
            db.commit()
            return

        cut_log: list[dict] = []

        def add_log(msg: str) -> None:
            entry = {"ts": datetime.utcnow().isoformat(), "msg": msg}
            cut_log.append(entry)
            project.cut_log = json.dumps(cut_log)
            db.commit()
            print(f"[cut {project_id[:8]}] {msg}", flush=True)

        add_log(f"Starting to cut {len(approved)} approved cards…")

        prompts = get_prompts()

        for i, card in enumerate(approved, 1):
            label = (card.tag or card.title or "Untitled")[:60]
            add_log(f"[{i}/{len(approved)}] Cutting: {label}")
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

            underlined = ul_result.get("underlined", [])
            highlighted = hl_result.get("highlighted", [])

            if underlined:
                for refine_round in range(1, MAX_REFINE_ROUNDS + 1):
                    refine_result, ref_model, ref_tokens = await review_and_refine_cutting(
                        card_dict, project.topic or "", underlined, highlighted,
                        prompts["underline"], prompts["highlight"],
                    )
                    record_tokens(ref_model, ref_tokens.get("input", 0) + ref_tokens.get("output", 0))
                    if refine_result.get("satisfied", True):
                        add_log(f"  ✓ Refinement round {refine_round}: satisfied")
                        break
                    new_ul = refine_result.get("underlined", underlined)
                    new_hl = refine_result.get("highlighted", highlighted)
                    if sorted(new_ul) == sorted(underlined) and sorted(new_hl) == sorted(highlighted):
                        add_log(f"  ✓ Refinement round {refine_round}: converged (no change)")
                        break
                    underlined = new_ul
                    highlighted = new_hl
                    add_log(f"  ↻ Refinement round {refine_round}: revised to {len(underlined)} underlines, {len(highlighted)} highlights")

            card.underlined = json.dumps(underlined)
            card.highlighted = json.dumps(highlighted)
            card.card_status = "cut"
            card.updated_at = datetime.utcnow()
            db.commit()
            add_log(f"  → {len(underlined)} underlines, {len(highlighted)} highlights")

        project.cut_status = "done"
        add_log(f"All {len(approved)} cards cut successfully")
        db.commit()

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
