"""Engine setup and transaction ownership for SQLite and PostgreSQL."""

from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL
from app.finance import STORAGE_PRECISION, STORAGE_SCALE

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False, "timeout": 30}
elif DATABASE_URL.startswith("postgresql"):
    connect_args = {
        "connect_timeout": 5,
        "options": "-c timezone=UTC -c lock_timeout=5000 -c statement_timeout=15000",
    }

engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=False)

if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as db:
        yield db


@contextmanager
def write_transaction(db):
    """SQLite reserves the writer before any read; PostgreSQL locks rows in services."""
    with db.begin():
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        yield


def init_db(bind=engine):
    # Workers can start together. Serialize DDL as well as runtime writes.
    with bind.begin() as connection:
        if bind.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        elif bind.dialect.name == "postgresql":
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(74219051)")
        Base.metadata.create_all(bind=connection)
        if bind.dialect.name == "postgresql":
            for table in Base.metadata.sorted_tables:
                for column in inspect(connection).get_columns(table.name):
                    datatype = column["type"]
                    if getattr(datatype, "scale", None) is not None and (
                        datatype.precision != STORAGE_PRECISION or datatype.scale != STORAGE_SCALE
                    ):
                        raise RuntimeError(
                            "Existing numeric schema needs migration: "
                            "stop writers and run python -m app.migrate"
                        )
