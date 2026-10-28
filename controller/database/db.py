"""database/db.py — Database session management and initialization."""
from __future__ import annotations

from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# ---------------------------------------------------------------------------
# Database file path (inside controller directory)
# ---------------------------------------------------------------------------
DB_PATH = Path(__file__).parent.parent / "stma.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# ---------------------------------------------------------------------------
# Engine — SQLite with WAL for better concurrent read performance
# ---------------------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _connection_record):
    """Enable WAL mode and foreign keys for every SQLite connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ---------------------------------------------------------------------------
# Table creation & Auto-Migration
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Create all tables and auto-migrate missing columns for existing SQLite tables."""
    Base.metadata.create_all(bind=engine)

    # Auto-add missing columns to existing SQLite tables if models have new fields
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        for table_name, table in Base.metadata.tables.items():
            cur.execute(f"PRAGMA table_info({table_name});")
            db_cols = {row[1] for row in cur.fetchall()}
            for col in table.columns:
                if col.name not in db_cols:
                    col_type = str(col.type)
                    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type};")
        conn.commit()
        conn.close()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("SQLite auto-migration notice: %s", e)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
def get_db() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session and ensure it's closed after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
