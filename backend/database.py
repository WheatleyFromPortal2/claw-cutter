from sqlalchemy import create_engine, Column, String, DateTime, Integer, Float
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:///./claw_cutter.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    created_at = Column(DateTime)
    status = Column(String)  # queued | running | done | error
    progress = Column(Integer, default=0)
    cards_total = Column(Integer, default=0)
    cards_done = Column(Integer, default=0)
    filename = Column(String)
    settings = Column(String)  # JSON blob
    error = Column(String, nullable=True)
    expires_at = Column(DateTime)
    card_log = Column(String, nullable=True)  # JSON array of per-card results
    tokens_input = Column(Integer, default=0)
    tokens_output = Column(Integer, default=0)
    processing_secs = Column(Float, default=0.0)
    filesize = Column(Integer, default=0)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
