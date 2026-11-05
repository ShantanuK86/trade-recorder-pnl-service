"""Regressions for the independent audit and transaction boundary failures."""

import json
from decimal import Decimal as D
from decimal import localcontext
from fractions import Fraction
from random import Random

import pytest
from sqlalchemy import event

from app.models import Position
from tests.conftest import make_trade


def post(client, **changes):
    return client.post("/trades/", json=make_trade(**changes))


def statement(**changes):
    row = make_trade(**changes)
    row.pop("venue")
    return row


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "11"),
        ("price", "101"),
        ("side", "SELL"),
        ("symbol", "ETH-USD"),
        ("timestamp", "2026-11-03T10:00:01Z"),
    ],
)
def test_conflicting_identity_rejects_changed_data(client, field, value):
    assert post(client).status_code == 201
    before = client.get("/positions/BTC-USD").json()
    r = post(client, **{field: value})
    assert r.status_code == 409
    assert r.json()["error"] == "idempotency_conflict"
    assert r.json()["fields"] == [field]
    assert client.get("/positions/BTC-USD").json() == before


def test_offset_and_decimal_format_are_equivalent_duplicates(client):
    assert post(client).status_code == 201
    r = post(client, quantity="10.000", price="1e2", timestamp="2026-11-03T15:30:00+05:30")
    assert r.status_code == 200
    assert r.json()["timestamp"] == "2026-11-03T10:00:00Z"
    assert r.json()["quantity"] == "10"


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", True),
        ("price", 100.1),
        ("quantity", "1e-19"),
        ("quantity", "1e24"),
        ("price", "Infinity"),
        ("quantity", "sNaN"),
        ("quantity", "9" * 121),
        ("timestamp", "1"),
        ("timestamp", "0"),
        ("timestamp", "2026-11-03T10:00:00.1234567Z"),
        ("timestamp", "0001-01-01T00:00:00+14:00"),
        ("timestamp", "9999-12-31T23:59:59-12:00"),
        ("trade_id", "T\x00ID"),
    ],
)
def test_unsupported_inputs_return_422_without_creating_position(client, field, value):
    assert post(client, **{field: value}).status_code == 422
    assert client.get("/positions/").json() == []


def test_raw_json_fraction_is_rejected_before_money_rounding(client):
    raw = json.dumps(make_trade(quantity="RAW")).replace('"RAW"', "0.123456789123456789")
    assert (
        client.post(
            "/trades/", content=raw, headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )


def test_valid_18_place_inputs_keep_36_place_product(client):
    assert post(client, quantity="1e-18", price="1e-18").status_code == 201
    p = client.get("/positions/BTC-USD").json()
    assert D(p["total_cost"]) == D("1e-36")
    assert D(p["average_cost"]) == D("1e-18")


def test_large_product_is_exact(client):
    quantity = "123456789.123456789123456789"
    price = "123456789.123456789"
    assert post(client, quantity=quantity, price=price).status_code == 201
    with localcontext() as ctx:
        ctx.prec = 160
        expected = D(quantity) * D(price)
        assert D(client.get("/positions/BTC-USD").json()["total_cost"]) == expected


def test_recurring_average_full_close_removes_all_basis(client):
    assert post(client, quantity="1", price="1").status_code == 201
    assert post(client, trade_id="B2", quantity="2", price="2").status_code == 201
    for i in range(3):
        assert (
            post(client, trade_id="S" + str(i), side="SELL", quantity="1", price="3").status_code
            == 201
        )
    assert D(client.get("/positions/BTC-USD").json()["total_cost"]) == 0
    pnl = client.get("/pnl/").json()[0]
    assert D(pnl["avg_cost_realized_pnl"]) == D(pnl["fifo_realized_pnl"]) == 4


def test_database_failure_after_position_update_rolls_back_all_state(client):
    post(client)
    before = client.get("/positions/BTC-USD").json(), client.get("/pnl/").json()

    def fail_after_update(_mapper, _connection, _position):
        raise RuntimeError("injected after position update")

    event.listen(Position, "after_update", fail_after_update)
    try:
        with pytest.raises(RuntimeError, match="injected after position update"):
            post(client, trade_id="failed-sale", side="SELL", quantity="3")
    finally:
        event.remove(Position, "after_update", fail_after_update)
    assert (
        client.get("/positions/BTC-USD").json(),
        client.get("/pnl/").json(),
    ) == before
    rows = client.post("/reconciliation/", json={"venue": "binance", "trades": []}).json()["extra"]
    assert [r["trade_id"] for r in rows] == ["T-1001"]
    assert post(client, trade_id="failed-sale", side="SELL", quantity="3").status_code == 201


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "-1"),
        ("price", "0"),
        ("side", "HOLD"),
        ("timestamp", "2026-11-03T10:00:00"),
    ],
)
def test_external_rows_share_trade_validation(client, field, value):
    r = client.post(
        "/reconciliation/",
        json={"venue": "binance", "trades": [statement(**{field: value})]},
    )
    assert r.status_code == 422


def test_statement_compares_values_and_timestamp(client):
    post(client)
    match = statement(quantity="10.000", price="1e2", timestamp="2026-11-03T15:30:00+05:30")
    assert (
        client.post("/reconciliation/", json={"venue": "binance", "trades": [match]}).json()[
            "mismatched"
        ]
        == []
    )
    mismatch = {**match, "timestamp": "2026-11-03T10:00:01Z"}
    rows = client.post("/reconciliation/", json={"venue": "binance", "trades": [mismatch]}).json()[
        "mismatched"
    ]
    assert [d["field"] for d in rows[0]["mismatches"]] == ["timestamp"]


@pytest.mark.parametrize("different", [False, True])
def test_statement_duplicate_ids_are_rejected(client, different):
    a = statement()
    b = statement(price="101") if different else a
    assert (
        client.post("/reconciliation/", json={"venue": "binance", "trades": [a, b]}).status_code
        == 422
    )


def test_full_trade_objects_in_statement_validate_venue(client):
    post(client)
    assert (
        client.post(
            "/reconciliation/", json={"venue": "binance", "trades": [make_trade()]}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/reconciliation/",
            json={"venue": "binance", "trades": [make_trade(venue="kraken")]},
        ).status_code
        == 422
    )


def test_window_is_start_inclusive_end_exclusive(client):
    for key, t in [
        ("before", "2026-11-02T23:59:59.999999Z"),
        ("start", "2026-11-03T00:00:00Z"),
        ("inside", "2026-11-03T23:59:59.999999Z"),
        ("end", "2026-11-04T00:00:00Z"),
    ]:
        assert post(client, trade_id=key, timestamp=t).status_code == 201
    response = client.post(
        "/reconciliation/",
        json={
            "venue": "binance",
            "trades": [],
            "start": "2026-11-03T05:30:00+05:30",
            "end": "2026-11-04T00:00:00Z",
        },
    )
    assert {t["trade_id"] for t in response.json()["extra"]} == {"start", "inside"}


@pytest.mark.parametrize(
    "scope",
    [
        {"start": "2026-11-03T00:00:00Z"},
        {"start": "2026-11-04T00:00:00Z", "end": "2026-11-03T00:00:00Z"},
        {"start": "2026-11-03T00:00:00Z", "end": "2026-11-03T00:00:00Z"},
    ],
)
def test_invalid_reconciliation_scope(client, scope):
    assert (
        client.post(
            "/reconciliation/", json={"venue": "binance", "trades": [], **scope}
        ).status_code
        == 422
    )


def test_statement_size_limit(client):
    rows = [statement(trade_id=str(i)) for i in range(10001)]
    assert (
        client.post("/reconciliation/", json={"venue": "binance", "trades": rows}).status_code
        == 422
    )


def test_slash_symbol_can_be_read(client):
    assert post(client, symbol="BTC/USD").status_code == 201
    assert client.get("/positions/BTC/USD").status_code == 200


@pytest.mark.parametrize("seed", range(4))
def test_generated_average_cost_against_fraction_reference(client, seed):
    rng = Random(seed)
    quantity, basis, realized, cash = Fraction(0), Fraction(0), Fraction(0), Fraction(0)
    with localcontext() as ctx:
        ctx.prec = 160
        for i in range(35):
            buy = rng.randrange(2) == 0
            size, price = (
                Fraction(rng.randint(1, 9), 10),
                Fraction(rng.randint(1, 999), 10),
            )

            def text(value):
                return str(D(value.numerator) / D(value.denominator))

            r = post(
                client,
                trade_id=str(i),
                side="BUY" if buy else "SELL",
                quantity=text(size),
                price=text(price),
            )
            if not buy and size > quantity:
                assert r.status_code == 409
                continue
            assert r.status_code == 201
            if buy:
                quantity, basis, cash = (
                    quantity + size,
                    basis + size * price,
                    cash - size * price,
                )
            else:
                removed = basis * size / quantity
                quantity, basis = quantity - size, basis - removed
                realized, cash = realized + size * price - removed, cash + size * price
            report = client.get("/positions/BTC-USD").json()
            pnl = client.get("/pnl/", params={"symbol": "BTC-USD"}).json()[0]
            assert D(report["net_quantity"]) == D(quantity.numerator) / D(quantity.denominator)
            assert abs(D(report["total_cost"]) - D(basis.numerator) / D(basis.denominator)) < D(
                "1e-33"
            )
            assert abs(
                D(pnl["avg_cost_realized_pnl"]) - D(realized.numerator) / D(realized.denominator)
            ) < D("1e-33")
            # With no mark, conservation uses remaining basis and cumulative proceeds.
            assert D(pnl["avg_cost_realized_pnl"]) - D(report["total_cost"]) == D(
                cash.numerator
            ) / D(cash.denominator)


@pytest.mark.parametrize("limit", ["quantity", "basis"])
def test_capacity_failure_rolls_back_and_same_key_can_retry(client, monkeypatch, limit):
    from app import finance

    if limit == "quantity":
        monkeypatch.setattr(finance, "MAX_QUANTITY", D(10))
    else:
        monkeypatch.setattr(finance, "MAX_BALANCE", D(1001))
    assert post(client).status_code == 201
    before = client.get("/positions/BTC-USD").json(), client.get("/pnl/").json()
    rejected = post(client, trade_id="over-capacity", quantity="1")
    assert rejected.status_code == 409
    assert rejected.json()["error"] == "position_limit"
    assert (client.get("/positions/BTC-USD").json(), client.get("/pnl/").json()) == before
    rows = client.post("/reconciliation/", json={"venue": "binance", "trades": []}).json()["extra"]
    assert len(rows) == 1
    assert post(client, trade_id="reduce", side="SELL", quantity="1").status_code == 201
    assert post(client, trade_id="over-capacity", quantity="1").status_code == 201


def test_inconsistent_fifo_inventory_aborts_the_whole_sell(client):
    from app.database import get_db
    from app.models import FifoLot

    assert post(client).status_code == 201
    # Simulate a legacy projection discrepancy; the service must not partially
    # apply a new sale while discovering that the FIFO inventory is missing.
    generator = client.app.dependency_overrides[get_db]()
    db = next(generator)
    try:
        db.query(FifoLot).one().remaining_quantity = D(0)
        db.commit()
    finally:
        generator.close()
    before = client.get("/positions/BTC-USD").json(), client.get("/pnl/").json()
    response = post(client, trade_id="broken-sale", side="SELL", quantity="1")
    assert response.status_code == 409
    assert response.json()["error"] == "inconsistent_inventory"
    assert (client.get("/positions/BTC-USD").json(), client.get("/pnl/").json()) == before
    rows = client.post("/reconciliation/", json={"venue": "binance", "trades": []}).json()["extra"]
    assert len(rows) == 1
