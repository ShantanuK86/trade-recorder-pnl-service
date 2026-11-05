"""
Shared pytest fixtures.

Every test gets a fresh in-memory SQLite database via the ``client``
fixture, ensuring complete isolation between tests.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # Ensure all models are registered with Base.metadata
from app.database import Base, get_db
from app.main import app


@pytest.fixture()
def client():
    """
    Yield a ``TestClient`` backed by a throwaway in-memory SQLite database.

    The database is created fresh and destroyed after each test.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )
    Base.metadata.create_all(bind=engine)

    def _override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


# ── Helper factories ────────────────────────────────────────────────────── #

def make_trade(
    venue: str = "binance",
    trade_id: str = "T-1001",
    symbol: str = "BTC-USD",
    side: str = "BUY",
    quantity: str = "10",
    price: str = "100",
    timestamp: str = "2026-11-03T10:00:00Z",
) -> dict:
    """Convenience factory for trade payloads."""
    return {
        "venue": venue,
        "trade_id": trade_id,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
        "timestamp": timestamp,
    }

