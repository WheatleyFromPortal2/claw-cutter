import json
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from ai import HIGHLIGHT_PROMPT, UNDERLINE_PROMPT
from database import Base, Job, SessionLocal, engine, get_db
from model_router import router as model_router
from tasks import run_tracing_job

APP_TOKEN = os.getenv("APP_TOKEN", "")
DATA_DIR = os.getenv("DATA_DIR", "./data")


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


app = FastAPI(title="Card Tracer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def verify_token(authorization: Optional[str] = Header(None)) -> None:
    if not APP_TOKEN:
        return
    if not authorization or authorization != f"Bearer {APP_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


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

    if not settings_dict.get("underline_prompt"):
        settings_dict["underline_prompt"] = UNDERLINE_PROMPT
    if not settings_dict.get("highlight_prompt"):
        settings_dict["highlight_prompt"] = HIGHLIGHT_PROMPT

    job_id = str(uuid.uuid4())
    now = datetime.utcnow()

    job_dir = Path(DATA_DIR) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    content = await file.read()
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

    background_tasks.add_task(run_tracing_job, job_id)

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

    return FileResponse(
        path=str(output_path),
        filename=f"traced_{job.filename}",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.delete("/api/jobs/{job_id}")
def delete_job(
    job_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
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


# Serve built frontend in production
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
