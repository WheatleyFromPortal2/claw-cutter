"""
Shared fixtures for the LionClaw test suite.

Import order matters: DATA_DIR must be set in the environment before any backend
module is imported, because main.py and tasks.py read it at module level.
"""
import os
import sys
import tempfile
from pathlib import Path

# ── Must happen before any backend import ────────────────────────────────────
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="lionclaw_test_")
os.environ["DATA_DIR"] = _TEST_DATA_DIR

# Clear auth tokens so verify_token() auto-passes as admin (no tokens → admin).
# load_dotenv() in main.py does NOT override pre-existing env vars, so these
# blank values take precedence over anything in backend/.env.
os.environ["ADMIN_TOKENS"] = ""
os.environ["USER_TOKENS"] = ""

# Add backend directory to Python path so test files can import backend modules.
_BACKEND = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))

# ── Standard imports (after path is set) ─────────────────────────────────────
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from database import Base, get_db
from main import app

TEST_DOCX_PATH = Path(__file__).parent / "test-files" / "Emory-Gazmararian-Forman-Neg-Coast 1-Doubles.docx"


# ── Docx fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_docx_path() -> Path:
    assert TEST_DOCX_PATH.exists(), f"Test docx not found: {TEST_DOCX_PATH}"
    return TEST_DOCX_PATH


@pytest.fixture(scope="session")
def test_docx_bytes(test_docx_path) -> bytes:
    return test_docx_path.read_bytes()


# ── Database fixtures ─────────────────────────────────────────────────────────

def _make_test_engine():
    # StaticPool forces all SQLAlchemy requests to reuse a single connection.
    # This is essential for SQLite :memory: databases — without it, each new
    # connection sees an empty database (tables created on one connection are
    # invisible on another).
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture
def db_engine():
    """Fresh in-memory SQLite engine with all tables for each test."""
    engine = _make_test_engine()
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """SQLAlchemy session bound to the test engine."""
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


# ── FastAPI test client ───────────────────────────────────────────────────────

@pytest.fixture
def test_client(db_engine):
    """
    FastAPI TestClient with get_db overridden to use the isolated test engine.

    No ADMIN_TOKENS or USER_TOKENS are set, so verify_token() returns 'admin'
    automatically — matching the production behaviour when no tokens are configured.
    """
    TestSession = sessionmaker(bind=db_engine)

    def _override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    app.dependency_overrides.clear()


# ── Integration-test client (also patches tasks layer) ───────────────────────

@pytest.fixture
def integration_client(db_engine, tmp_path, monkeypatch):
    """
    TestClient where BOTH the API layer AND the background task layer use the
    same isolated test database and a temporary DATA_DIR.

    Background tasks call tasks.SessionLocal() directly (not via FastAPI DI),
    so we monkeypatch tasks.SessionLocal and tasks.DATA_DIR as well.
    """
    import tasks  # noqa: import inside fixture so monkeypatch applies cleanly

    TestSession = sessionmaker(bind=db_engine)

    def _override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(tasks, "SessionLocal", TestSession)
    monkeypatch.setattr(tasks, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr("main.DATA_DIR", str(tmp_path))

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    app.dependency_overrides.clear()
