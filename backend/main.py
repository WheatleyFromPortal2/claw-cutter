import json
import os
import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path, PurePath
from typing import Optional

import psutil
from dotenv import load_dotenv

load_dotenv()

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ai import get_prompts, generate_cite, parse_cite_creator
from card_export import export_cards_to_docx
from database import Base, Job, Project, Card, SessionLocal, engine, get_db
from metrics import get_current_user_count, get_tokens_per_sec, get_uptime_secs, record_user
from model_router import router as model_router
from tasks import run_cutting_job, run_research_job, run_project_cut_job

DATA_DIR = os.getenv("DATA_DIR", "./data")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def _load_tokens() -> tuple[set[str], set[str]]:
    admin_tokens: set[str] = set()
    user_tokens: set[str] = set()

    for t in os.getenv("ADMIN_TOKENS", "").split(","):
        t = t.strip()
        if t:
            admin_tokens.add(t)

    for t in os.getenv("USER_TOKENS", "").split(","):
        t = t.strip()
        if t:
            user_tokens.add(t)

    return admin_tokens, user_tokens


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    model_router.start_watching()

    db = SessionLocal()
    try:
        for job in db.query(Job).filter(Job.status == "running").all():
            job.status = "error"
            job.error = "Server restarted while job was running"

        now = datetime.utcnow()
        for job in db.query(Job).filter(Job.expires_at < now).all():
            job_dir = Path(DATA_DIR) / job.id
            if job_dir.exists():
                shutil.rmtree(job_dir)
            db.delete(job)

        # Reset in-progress research/cut jobs
        for proj in db.query(Project).filter(
            or_(Project.research_status == "running", Project.cut_status == "running")
        ).all():
            if proj.research_status == "running":
                proj.research_status = "error"
                proj.research_error = "Server restarted"
            if proj.cut_status == "running":
                proj.cut_status = "error"
                proj.cut_error = "Server restarted"

        db.commit()
    finally:
        db.close()

    yield


app = FastAPI(title="LionClaw", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


def verify_token(authorization: Optional[str] = Header(None)) -> str:
    """Return role ('admin' or 'user'), or raise 401/403."""
    admin_tokens, user_tokens = _load_tokens()
    if not admin_tokens and not user_tokens:
        return "admin"
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization[len("Bearer "):]
    if token in admin_tokens:
        record_user(token)
        return "admin"
    if token in user_tokens:
        record_user(token)
        return "user"
    raise HTTPException(status_code=401, detail="Unauthorized")


def require_admin(role: str = Depends(verify_token)) -> None:
    if role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")


# ── Auth / meta ───────────────────────────────────────────────────────────────

@app.get("/api/role")
def get_role(role: str = Depends(verify_token)):
    return {"role": role}


@app.get("/api/prompts")
def get_prompts_endpoint(_: None = Depends(verify_token)):
    return get_prompts()


@app.get("/api/models")
def list_models(_: None = Depends(verify_token)):
    return [
        {
            "id": m.id,
            "name": m.name,
            "provider": m.provider,
            "model": m.model,
            "enabled": m.enabled,
            "preference": m.preference,
            "timeout_secs": m.timeout_secs,
        }
        for m in model_router.all_models()
    ]


# ── Docx cutting jobs (existing) ─────────────────────────────────────────────

@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    settings: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    try:
        settings_dict = json.loads(settings)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid settings JSON")

    prompts = get_prompts()
    if not settings_dict.get("underline_prompt"):
        settings_dict["underline_prompt"] = prompts["underline"]
    if not settings_dict.get("highlight_prompt"):
        settings_dict["highlight_prompt"] = prompts["highlight"]

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds the 10 MB size limit.")

    job_id = str(uuid.uuid4())
    now = datetime.utcnow()

    job_dir = Path(DATA_DIR) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    with open(job_dir / "input.docx", "wb") as f:
        f.write(content)

    job = Job(
        id=job_id,
        created_at=now,
        status="queued",
        progress=0,
        cards_total=0,
        cards_done=0,
        filename=file.filename,
        settings=json.dumps(settings_dict),
        error=None,
        expires_at=now + timedelta(hours=2),
        card_log=None,
    )
    db.add(job)
    db.commit()

    background_tasks.add_task(run_cutting_job, job_id)

    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs")
def list_jobs(
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    jobs = db.query(Job).order_by(Job.created_at.desc()).all()
    return [
        {
            "id": j.id,
            "filename": j.filename,
            "status": j.status,
            "progress": j.progress,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in jobs
    ]


def _job_ul_hl_totals(job: Job) -> tuple[int, int]:
    ul_total = 0
    hl_total = 0
    if job.card_log:
        try:
            for entry in json.loads(job.card_log):
                ul_total += entry.get("ul_count", 0)
                hl_total += entry.get("hl_count", 0)
        except Exception:
            pass
    return ul_total, hl_total


@app.get("/api/jobs/{job_id}")
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    card_log = []
    if job.card_log:
        try:
            card_log = json.loads(job.card_log)
        except json.JSONDecodeError:
            pass

    ul_total, hl_total = _job_ul_hl_totals(job)

    return {
        "id": job.id,
        "filename": job.filename,
        "status": job.status,
        "progress": job.progress,
        "cards_total": job.cards_total,
        "cards_done": job.cards_done,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "error": job.error,
        "card_log": card_log,
        "tokens_input": job.tokens_input or 0,
        "tokens_output": job.tokens_output or 0,
        "processing_secs": job.processing_secs or 0.0,
        "filesize": job.filesize or 0,
        "underlines": ul_total,
        "highlights": hl_total,
    }


@app.get("/api/jobs/{job_id}/download")
def download_job(
    job_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(status_code=400, detail="Job not complete")

    output_path = Path(DATA_DIR) / job_id / "output.docx"
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    stem = PurePath(job.filename).stem
    download_name = f"{stem}_CUT.docx"

    return FileResponse(
        path=str(output_path),
        filename=download_name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.delete("/api/jobs/{job_id}")
def delete_job(
    job_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_dir = Path(DATA_DIR) / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir)

    db.delete(job)
    db.commit()
    return {"status": "deleted"}


@app.get("/api/stats")
def get_stats(
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    jobs = db.query(Job).filter(Job.status == "done").all()
    total_ul = 0
    total_hl = 0
    total_tokens_input = 0
    total_tokens_output = 0
    total_processing_secs = 0.0
    total_filesize = 0

    for j in jobs:
        ul, hl = _job_ul_hl_totals(j)
        total_ul += ul
        total_hl += hl
        total_tokens_input += j.tokens_input or 0
        total_tokens_output += j.tokens_output or 0
        total_processing_secs += j.processing_secs or 0.0
        total_filesize += j.filesize or 0

    return {
        "jobs_completed": len(jobs),
        "tokens_input": total_tokens_input,
        "tokens_output": total_tokens_output,
        "processing_secs": total_processing_secs,
        "filesize": total_filesize,
        "underlines": total_ul,
        "highlights": total_hl,
    }


# ── Projects ──────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    topic: Optional[str] = ""
    description: Optional[str] = ""


def _project_out(p: Project, db: Session) -> dict:
    card_count = db.query(Card).filter(Card.project_id == p.id, Card.card_status != "trashed").count()
    research_log = []
    if p.research_log:
        try:
            research_log = json.loads(p.research_log)
        except Exception:
            pass
    cut_log = []
    if p.cut_log:
        try:
            cut_log = json.loads(p.cut_log)
        except Exception:
            pass
    return {
        "id": p.id,
        "name": p.name,
        "topic": p.topic or "",
        "description": p.description or "",
        "link_story": p.link_story or "",
        "research_status": p.research_status or "idle",
        "research_error": p.research_error or "",
        "research_log": research_log,
        "cut_status": p.cut_status or "idle",
        "cut_error": p.cut_error or "",
        "cut_log": cut_log,
        "status": p.status or "active",
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "card_count": card_count,
    }


@app.get("/api/projects")
def list_projects(
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return [_project_out(p, db) for p in projects]


@app.post("/api/projects")
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    project = Project(
        id=str(uuid.uuid4()),
        name=body.name,
        topic=body.topic or "",
        description=body.description or "",
        research_status="idle",
        cut_status="idle",
        status="active",
        created_at=datetime.utcnow(),
    )
    db.add(project)
    db.commit()
    return _project_out(project, db)


@app.get("/api/projects/{project_id}")
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_out(project, db)


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    topic: Optional[str] = None
    description: Optional[str] = None


@app.patch("/api/projects/{project_id}")
def update_project(
    project_id: str,
    body: ProjectUpdate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if body.name is not None:
        project.name = body.name
    if body.topic is not None:
        project.topic = body.topic
    if body.description is not None:
        project.description = body.description
    db.commit()
    return _project_out(project, db)


@app.delete("/api/projects/{project_id}")
def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    db.query(Card).filter(Card.project_id == project_id).delete()
    db.delete(project)
    db.commit()
    return {"status": "deleted"}


@app.post("/api/projects/{project_id}/research")
def start_research(
    project_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.research_status == "running":
        raise HTTPException(status_code=409, detail="Research already running")

    project.research_status = "running"
    project.research_error = None
    db.commit()

    background_tasks.add_task(run_research_job, project_id)
    return {"status": "started"}


@app.post("/api/projects/{project_id}/cut")
def start_project_cut(
    project_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.cut_status == "running":
        raise HTTPException(status_code=409, detail="Cut job already running")

    project.cut_status = "running"
    project.cut_error = None
    db.commit()

    background_tasks.add_task(run_project_cut_job, project_id)
    return {"status": "started"}


# ── Cards ─────────────────────────────────────────────────────────────────────

_CITE_FIELDS = ("author", "author_qualifications", "date", "title", "publisher", "url", "initials")


def _card_out(c: Card) -> dict:
    low_confidence = [f for f in _CITE_FIELDS if getattr(c, f) is None]
    return {
        "id": c.id,
        "project_id": c.project_id or "",
        "tag": c.tag or "",
        "author": c.author,
        "author_qualifications": c.author_qualifications,
        "date": c.date,
        "title": c.title,
        "publisher": c.publisher,
        "url": c.url,
        "initials": c.initials,
        "topic": c.topic or "",
        "tags": json.loads(c.tags) if c.tags else [],
        "card_text": c.card_text or "",
        "full_text_fetched": bool(c.full_text_fetched),
        "underlined": json.loads(c.underlined) if c.underlined else [],
        "highlighted": json.loads(c.highlighted) if c.highlighted else [],
        "card_status": c.card_status or "researched",
        "low_confidence_fields": low_confidence,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@app.get("/api/projects/{project_id}/cards")
def list_project_cards(
    project_id: str,
    card_status: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    query = db.query(Card).filter(Card.project_id == project_id)
    if card_status:
        query = query.filter(Card.card_status == card_status)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Card.tag.ilike(like),
                Card.author.ilike(like),
                Card.publisher.ilike(like),
                Card.title.ilike(like),
                Card.initials.ilike(like),
                Card.date.ilike(like),
            )
        )
    return [_card_out(c) for c in query.order_by(Card.created_at.asc()).all()]


@app.get("/api/cards")
def search_cards(
    project_id: Optional[str] = None,
    card_status: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    query = db.query(Card)
    if project_id:
        query = query.filter(Card.project_id == project_id)
    if card_status:
        query = query.filter(Card.card_status == card_status)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Card.tag.ilike(like),
                Card.author.ilike(like),
                Card.publisher.ilike(like),
                Card.title.ilike(like),
                Card.initials.ilike(like),
                Card.date.ilike(like),
            )
        )
    return [_card_out(c) for c in query.order_by(Card.created_at.desc()).all()]


@app.get("/api/cards/{card_id}")
def get_card(
    card_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    return _card_out(card)


class CardUpdate(BaseModel):
    tag: Optional[str] = None
    author: Optional[str] = None
    author_qualifications: Optional[str] = None
    date: Optional[str] = None
    title: Optional[str] = None
    publisher: Optional[str] = None
    url: Optional[str] = None
    initials: Optional[str] = None
    topic: Optional[str] = None
    tags: Optional[list] = None
    card_text: Optional[str] = None
    underlined: Optional[list] = None
    highlighted: Optional[list] = None


@app.patch("/api/cards/{card_id}")
def update_card(
    card_id: str,
    body: CardUpdate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    fields = body.model_dump(exclude_none=True)
    for key, val in fields.items():
        if key in ("tags", "underlined", "highlighted"):
            setattr(card, key, json.dumps(val))
        else:
            setattr(card, key, val)

    card.updated_at = datetime.utcnow()
    db.commit()
    return _card_out(card)


@app.post("/api/cards/{card_id}/approve")
def approve_card(
    card_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    card.card_status = "approved"
    card.updated_at = datetime.utcnow()
    db.commit()
    return {"id": card.id, "card_status": card.card_status}


@app.post("/api/cards/{card_id}/trash")
def trash_card(
    card_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    card.card_status = "trashed"
    card.updated_at = datetime.utcnow()
    db.commit()
    return {"id": card.id, "card_status": card.card_status}


@app.post("/api/cards/{card_id}/restore")
def restore_card(
    card_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    card.card_status = "researched"
    card.updated_at = datetime.utcnow()
    db.commit()
    return {"id": card.id, "card_status": card.card_status}


class CiteRequest(BaseModel):
    article_text: str


@app.post("/api/cards/{card_id}/cite")
async def generate_card_cite(
    card_id: str,
    body: CiteRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    cite, model_id, tokens = await generate_cite(body.article_text)
    if cite.get("error"):
        raise HTTPException(status_code=500, detail=cite["error"])

    for field in ("author", "author_qualifications", "date", "title", "publisher", "url", "initials"):
        val = cite.get(field)
        if val:
            setattr(card, field, val)

    card.updated_at = datetime.utcnow()
    db.commit()
    return _card_out(card)


@app.post("/api/projects/{project_id}/cards/approve-all")
def approve_all_cards(
    project_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    now = datetime.utcnow()
    cards = (
        db.query(Card)
        .filter(Card.project_id == project_id, Card.card_status == "researched")
        .all()
    )
    for c in cards:
        c.card_status = "approved"
        c.updated_at = now
    db.commit()
    return {"approved": len(cards)}


@app.post("/api/projects/{project_id}/cards/trash-unapproved")
def trash_unapproved_cards(
    project_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    now = datetime.utcnow()
    cards = (
        db.query(Card)
        .filter(Card.project_id == project_id, Card.card_status == "researched")
        .all()
    )
    for c in cards:
        c.card_status = "trashed"
        c.updated_at = now
    db.commit()
    return {"trashed": len(cards)}


class CiteCreatorRequest(BaseModel):
    cite_text: str


@app.post("/api/cards/{card_id}/cite-creator")
async def parse_card_cite_creator(
    card_id: str,
    body: CiteCreatorRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    cite, model_id, tokens = await parse_cite_creator(body.cite_text)
    if cite.get("error"):
        raise HTTPException(status_code=500, detail=cite["error"])

    for field in ("author", "author_qualifications", "date", "title", "publisher", "url", "initials"):
        val = cite.get(field)
        if val is not None:
            setattr(card, field, val if val else None)

    card.updated_at = datetime.utcnow()
    db.commit()
    return _card_out(card)


class ArticleTextRequest(BaseModel):
    article_text: str


@app.post("/api/cards/{card_id}/article-text")
def set_card_article_text(
    card_id: str,
    body: ArticleTextRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    card.card_text = body.article_text
    card.full_text_fetched = 1
    card.updated_at = datetime.utcnow()
    db.commit()
    return _card_out(card)


class AddFromUrlRequest(BaseModel):
    url: str


@app.post("/api/projects/{project_id}/cards/from-url")
async def add_card_from_url(
    project_id: str,
    body: AddFromUrlRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    import httpx

    url = body.url.strip()
    title = url
    card_text = ""
    fetched = 0

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "LionClaw/1.0"})
            resp.raise_for_status()
            raw_html = resp.text

        import re as _re
        title_match = _re.search(r"<title[^>]*>([^<]+)</title>", raw_html, _re.I)
        if title_match:
            title = title_match.group(1).strip()

        clean = _re.sub(r"<script[^>]*>[\s\S]*?</script>", "", raw_html, flags=_re.I)
        clean = _re.sub(r"<style[^>]*>[\s\S]*?</style>", "", clean, flags=_re.I)
        clean = _re.sub(r"<[^>]+>", " ", clean)
        clean = _re.sub(r"&[a-zA-Z]+;", " ", clean)
        clean = _re.sub(r"\s+", " ", clean).strip()
        card_text = clean[:8000]
        fetched = 1
    except Exception:
        pass

    card = Card(
        id=str(uuid.uuid4()),
        project_id=project_id,
        tag="",
        title=title,
        url=url,
        card_text=card_text,
        full_text_fetched=fetched,
        topic=project.topic or "",
        tags=json.dumps([f"proj::{project.name}"]),
        card_status="approved",
        created_at=datetime.utcnow(),
    )
    db.add(card)
    db.commit()
    return _card_out(card)


class ExportRequest(BaseModel):
    card_ids: list[str]
    hl_color: Optional[str] = "cyan"


@app.post("/api/cards/export")
def export_cards(
    body: ExportRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
):
    cards = (
        db.query(Card)
        .filter(Card.id.in_(body.card_ids))
        .all()
    )
    if not cards:
        raise HTTPException(status_code=404, detail="No cards found")

    # Preserve requested order
    id_to_card = {c.id: c for c in cards}
    ordered = [id_to_card[cid] for cid in body.card_ids if cid in id_to_card]

    card_dicts = [
        {
            "tag": c.tag,
            "author": c.author,
            "author_qualifications": c.author_qualifications,
            "date": c.date,
            "title": c.title,
            "publisher": c.publisher,
            "url": c.url,
            "initials": c.initials,
            "card_text": c.card_text,
            "underlined": c.underlined,
            "highlighted": c.highlighted,
        }
        for c in ordered
    ]

    docx_bytes = export_cards_to_docx(card_dicts, body.hl_color or "cyan")

    import io
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=lionclaw_export.docx"},
    )


# ── Status page & helpers ─────────────────────────────────────────────────────

def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=Path(__file__).parent.parent,
        )
        return result.stdout.strip()[:12] if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _data_dir_size_bytes() -> int:
    total = 0
    data_path = Path(DATA_DIR)
    if data_path.exists():
        for f in data_path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    return total


def _db_size_bytes() -> int:
    db_path = Path(__file__).parent / "lionclaw.db"
    try:
        return db_path.stat().st_size
    except OSError:
        return 0


@app.get("/api/status")
def api_status(db: Session = Depends(get_db)):
    running_jobs = db.query(Job).filter(Job.status == "running").count()
    queued_jobs = db.query(Job).filter(Job.status == "queued").count()

    return {
        "git_commit": _git_commit(),
        "uptime_secs": get_uptime_secs(),
        "current_users": get_current_user_count(),
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "tokens_per_sec": get_tokens_per_sec(),
        "db_size_bytes": _db_size_bytes(),
        "data_dir_size_bytes": _data_dir_size_bytes(),
        "running_jobs": running_jobs,
        "queued_jobs": queued_jobs,
    }


def _fmt_uptime(secs: float) -> str:
    secs = int(secs)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h or d:
        parts.append(f"{h}h")
    if m or h or d:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@app.get("/status", response_class=HTMLResponse)
def status_page(db: Session = Depends(get_db)):
    running_jobs = db.query(Job).filter(Job.status == "running").count()
    queued_jobs = db.query(Job).filter(Job.status == "queued").count()

    git_commit = _git_commit()
    uptime = _fmt_uptime(get_uptime_secs())
    current_users = get_current_user_count()
    cpu = psutil.cpu_percent(interval=0.1)
    tps = get_tokens_per_sec()
    db_size = _fmt_bytes(_db_size_bytes())
    data_size = _fmt_bytes(_data_dir_size_bytes())

    tps_rows = ""
    for model_id, rate in tps.items():
        tps_rows += f"<tr><td>{model_id}</td><td>{rate:.1f} tok/s</td></tr>\n"
    if not tps_rows:
        tps_rows = "<tr><td colspan='2' style='color:#6b7280'>No recent activity</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="5">
  <title>LionClaw — Status</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0f1117; color: #e2e4ef; margin: 0; padding: 32px; }}
    h1 {{ font-size: 20px; font-weight: 700; margin-bottom: 24px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
    .card {{ background: #1a1d27; border: 1px solid #2e3248; border-radius: 8px; padding: 16px; }}
    .label {{ font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }}
    .value {{ font-size: 22px; font-weight: 700; font-family: monospace; }}
    table {{ width: 100%; border-collapse: collapse; background: #1a1d27; border: 1px solid #2e3248; border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #2e3248; font-size: 13px; }}
    th {{ font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; }}
    tr:last-child td {{ border-bottom: none; }}
    .note {{ font-size: 11px; color: #6b7280; margin-top: 16px; }}
    a {{ color: #6366f1; text-decoration: none; }}
  </style>
</head>
<body>
  <h1>✦ LionClaw — Status</h1>
  <div class="grid">
    <div class="card"><div class="label">Git Commit</div><div class="value" style="font-size:14px">{git_commit}</div></div>
    <div class="card"><div class="label">App Uptime</div><div class="value">{uptime}</div></div>
    <div class="card"><div class="label">Current Users</div><div class="value">{current_users}</div></div>
    <div class="card"><div class="label">CPU Usage</div><div class="value">{cpu:.1f}%</div></div>
    <div class="card"><div class="label">Running Jobs</div><div class="value">{running_jobs}</div></div>
    <div class="card"><div class="label">Queued Jobs</div><div class="value">{queued_jobs}</div></div>
    <div class="card"><div class="label">Database Size</div><div class="value">{db_size}</div></div>
    <div class="card"><div class="label">Used Space (/data)</div><div class="value">{data_size}</div></div>
  </div>

  <h2 style="font-size:14px;font-weight:600;margin-bottom:12px">Tokens/s (last 60s, per model)</h2>
  <table>
    <thead><tr><th>Model</th><th>Rate</th></tr></thead>
    <tbody>{tps_rows}</tbody>
  </table>

  <p class="note">Auto-refreshes every 5 seconds · <a href="/">← Back to app</a> · JSON: <a href="/api/status">/api/status</a></p>
</body>
</html>"""
    return HTMLResponse(content=html)


# Serve favicon.png from backend/resources if present
_resources_dir = Path(__file__).parent / "resources"
_resources_dir.mkdir(exist_ok=True)

@app.get("/favicon.png")
def serve_favicon():
    favicon_path = _resources_dir / "favicon.png"
    if favicon_path.exists():
        return FileResponse(str(favicon_path), media_type="image/png")
    raise HTTPException(status_code=404, detail="favicon.png not found")

# Serve built frontend in production
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
