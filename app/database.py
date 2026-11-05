"""
Database engine, session factory, and base model.

SQLite is used for development simplicity (no Docker / external services).
Swap DATABASE_URL to a PostgreSQL DSN for production — the ORM layer is
identical; only the connection string changes.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

# --------------------------------------------------------------------------- #
#  Engine
# --------------------------------------------------------------------------- #
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    # SQLite needs this for FastAPI's threaded request handling
    connect_args["check_same_thread"] = False

engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=False)

# Enable WAL mode and foreign keys for SQLite
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

# --------------------------------------------------------------------------- #
#  Session
# --------------------------------------------------------------------------- #
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency — yields a scoped session and ensures cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables (idempotent)."""
    Base.metadata.create_all(bind=engine)

