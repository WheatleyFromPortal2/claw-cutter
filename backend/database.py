from sqlalchemy import create_engine, Column, String, DateTime, Integer, Float, Text, event
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:///./lionclaw.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True)
    name = Column(String)
    topic = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    link_story = Column(Text, nullable=True)
    research_status = Column(String, default="idle")  # idle | running | done | error
    research_error = Column(String, nullable=True)
    research_log = Column(Text, nullable=True)  # JSON array of log lines
    cut_status = Column(String, default="idle")  # idle | running | done | error
    cut_error = Column(String, nullable=True)
    cut_log = Column(Text, nullable=True)  # JSON array of log lines
    status = Column(String, default="active")  # active | archived
    created_at = Column(DateTime)


class Card(Base):
    __tablename__ = "cards"

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=True)

    # Debate card metadata
    tag = Column(String, nullable=True)
    author = Column(String, nullable=True)
    author_qualifications = Column(Text, nullable=True)
    date = Column(String, nullable=True)
    title = Column(String, nullable=True)
    publisher = Column(String, nullable=True)
    url = Column(String, nullable=True)
    initials = Column(String, nullable=True)
    topic = Column(String, nullable=True)
    tags = Column(String, nullable=True)  # JSON array of string tags

    # Card content
    card_text = Column(Text, nullable=True)
    full_text_fetched = Column(Integer, default=0)  # 0 = no, 1 = yes
    underlined = Column(Text, nullable=True)   # JSON array of underlined phrases
    highlighted = Column(Text, nullable=True)  # JSON array of highlighted phrases

    # Workflow status: researched | approved | cut | trashed
    card_status = Column(String, default="researched")

    created_at = Column(DateTime)
    updated_at = Column(DateTime, nullable=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
