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
from sqlalchemy.orm import Session

from ai import get_prompts
from database import Base, Job, SessionLocal, engine, get_db
from metrics import get_current_user_count, get_tokens_per_sec, get_uptime_secs, record_user
from model_router import router as model_router
from tasks import run_cutting_job

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
    # Startup
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

        db.commit()
    finally:
        db.close()

    yield
    # Shutdown (nothing to clean up)


app = FastAPI(title="Claw Cutter", lifespan=lifespan)

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
    db_path = Path(__file__).parent / "claw_cutter.db"
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
  <title>Claw Cutter — Status</title>
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
  <h1>✦ Claw Cutter — Status</h1>
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


# Serve built frontend in production
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
