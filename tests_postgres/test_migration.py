"""Explicit PostgreSQL tests: run against a dedicated TEST_DATABASE_URL."""

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.database import Base, init_db
from app.migrate import migrate
from app.models import DecimalField, Position, Trade
from app.schemas import TradeCreate
from app.services.trade_service import ingest_trade


@pytest.fixture
def isolated_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.fail("Set TEST_DATABASE_URL to a dedicated PostgreSQL database")
    admin = create_engine(url)
    schema = "migration_" + uuid4().hex
    with admin.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
    try:
        yield scoped
    finally:
        scoped.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def payload(**changes):
    return TradeCreate(
        venue="binance",
        trade_id="old",
        symbol="BTC-USD",
        side="BUY",
        quantity="10",
        price="100",
        timestamp="2026-11-03T10:00:00Z",
        **changes,
    )


def test_repeatable_numeric_migration_preserves_existing_rows(isolated_engine):
    bind = isolated_engine
    Base.metadata.create_all(bind)
    with bind.begin() as conn:
        for table in Base.metadata.sorted_tables:
            for constraint in inspect(conn).get_check_constraints(table.name):
                conn.exec_driver_sql(
                    f'ALTER TABLE "{table.name}" DROP CONSTRAINT "{constraint["name"]}"'
                )
            for column in table.columns:
                if isinstance(column.type, DecimalField):
                    conn.exec_driver_sql(
                        f'ALTER TABLE "{table.name}" ALTER COLUMN "{column.name}" TYPE NUMERIC(38,18)'
                    )
    with Session(bind) as db:
        ingest_trade(db, payload())
    with pytest.raises(RuntimeError, match="needs migration"):
        init_db(bind)
    migrate(bind)
    migrate(bind)
    init_db(bind)
    with Session(bind) as db:
        assert db.query(Trade).count() == 1
        assert db.query(Position).one().net_quantity == 10
    # Services own their write transaction and receive a fresh request session.
    with Session(bind) as db:
        assert ingest_trade(db, payload()).duplicate
    with Session(bind) as db:
        data = payload().model_dump()
        data.update(trade_id="tiny", symbol="TINY-USD", quantity="1e-18", price="1e-18")
        ingest_trade(db, TradeCreate(**data))
    with Session(bind) as db:
        row = db.query(Position).filter_by(symbol="TINY-USD").one()
        assert row.total_cost == Decimal("1e-36")
    for table in Base.metadata.sorted_tables:
        assert inspect(bind).get_check_constraints(table.name)
        for column in inspect(bind).get_columns(table.name):
            datatype = column["type"]
            if getattr(datatype, "scale", None) is not None:
                assert (datatype.precision, datatype.scale) == (120, 36)
