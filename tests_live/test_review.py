from decimal import Decimal as D
from decimal import localcontext

import pytest


@pytest.mark.core
def test_worked_example_average_and_fifo(api):
    api.buy()
    api.buy(trade_id="B2", price="110")
    api.buy(trade_id="S1", side="SELL", quantity="5", price="120")
    api.mark()
    p = api.pnl()
    for field, expected in {
        "net_quantity": "15",
        "avg_cost": "105",
        "avg_cost_realized_pnl": "75",
        "avg_cost_unrealized_pnl": "300",
        "fifo_realized_pnl": "100",
        "fifo_unrealized_pnl": "275",
    }.items():
        assert D(p[field]) == D(expected), p
    assert abs(D(p["fifo_avg_cost"]) - D(1600) / D(15)) < D("1e-20")


@pytest.mark.core
def test_sequential_idempotency_and_cross_venue_keys(api):
    api.buy()
    for _ in range(5):
        r = api.post()
        assert r.status_code == 200 and r.json()["duplicate"]
    assert len(api.ledger()) == 1
    api.buy(venue=api.venue + "_b")
    assert D(api.position()["net_quantity"]) == 20


@pytest.mark.core
@pytest.mark.parametrize("size", ["10.000000000000000001", "15", "1000"])
def test_oversell_keeps_ledger_position_and_pnl_unchanged(api, size):
    api.buy()
    api.mark()
    before = api.position(), api.pnl(), api.ledger()
    r = api.post(trade_id="bad-sell", side="SELL", quantity=size)
    assert r.status_code == 409, r.text
    assert (api.position(), api.pnl(), api.ledger()) == before


@pytest.mark.core
def test_empty_sell_then_retry_same_key_after_top_up(api):
    assert api.post(side="SELL").status_code == 409
    assert api.ledger() == []
    assert api.client.get("/positions/" + api.symbol).status_code == 404
    api.buy(trade_id="fund")
    api.buy(side="SELL")
    assert D(api.position()["net_quantity"]) == 0
    assert api.post(side="SELL").status_code == 200


@pytest.mark.core
def test_fractional_quantities_and_decimal_cost(api):
    api.buy(quantity="0.1", price="0.2")
    api.buy(trade_id="B2", quantity="0.2", price="0.2")
    api.mark("0.3")
    assert D(api.position()["net_quantity"]) == D("0.3")
    assert D(api.position()["total_cost"]) == D("0.06")
    assert D(api.pnl()["avg_cost_unrealized_pnl"]) == D("0.03")


@pytest.mark.core
def test_eighteen_decimal_trade_preserves_cost_basis(api):
    api.buy(quantity="0.000000000000000001", price="0.000000000000000001")
    with localcontext() as ctx:
        ctx.prec = 80
        assert D(api.position()["total_cost"]) == D("1e-36")
        assert D(api.position()["average_cost"]) == D("1e-18")


@pytest.mark.core
def test_large_valid_decimal_preserves_exact_cost_basis(api):
    q, p = "123456789.123456789123456789", "123456789.123456789"
    api.buy(quantity=q, price=p)
    with localcontext() as ctx:
        ctx.prec = 100
        assert D(api.position()["total_cost"]) == D(q) * D(p)


@pytest.mark.core
def test_reconcile_required_example(api):
    api.buy()
    api.buy(trade_id="T2", quantity="5", price="101")
    r = api.reconcile(
        [
            api.statement(),
            api.statement(trade_id="T2", quantity="6", price="101"),
            api.statement(trade_id="T3", quantity="1", price="99"),
        ]
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [t["trade_id"] for t in body["missing"]] == ["T3"]
    assert body["extra"] == []
    assert [t["trade_id"] for t in body["mismatched"]] == ["T2"], body
    assert [m["field"] for m in body["mismatched"][0]["mismatches"]] == ["quantity"], body


@pytest.mark.core
@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "11"),
        ("price", "101"),
        ("symbol", "OTHER-USD"),
        ("side", "SELL"),
        ("timestamp", "2026-11-03T10:00:01Z"),
    ],
)
def test_reconciliation_compares_each_payload_field(api, field, value):
    api.buy()
    r = api.reconcile([api.statement(**{field: value})])
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["mismatched"]) == 1, body
    changed = {d["field"] for d in body["mismatched"][0]["mismatches"]}
    assert field in changed, body


@pytest.mark.core
@pytest.mark.parametrize("quantity,price", [("10", "100"), ("10.0", "100.00"), ("1e1", "1e2")])
def test_reconciliation_compares_numeric_values_not_decimal_format(api, quantity, price):
    api.buy()
    r = api.reconcile([api.statement(quantity=quantity, price=price)])
    assert r.status_code == 200, r.text
    assert r.json()["mismatched"] == [], r.text


@pytest.mark.core
def test_reconciliation_missing_extra_venue_isolation_and_no_mutation(api):
    api.buy()
    api.buy(trade_id="local-only")
    api.buy(venue=api.venue + "_other", trade_id="external-only")
    before = api.position(), api.pnl()
    r = api.reconcile([api.statement(), api.statement(trade_id="external-only")])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [t["trade_id"] for t in body["missing"]] == ["external-only"]
    assert [t["trade_id"] for t in body["extra"]] == ["local-only"]
    assert (api.position(), api.pnl()) == before


@pytest.mark.core
@pytest.mark.parametrize("run", range(3))
def test_concurrent_duplicate_first_buy_is_counted_once_without_server_error(api, run):
    responses = api.parallel([api.payload()] * 12)
    codes = sorted(r.status_code for r in responses)
    assert codes == [200] * 11 + [201], codes
    assert len(api.ledger()) == 1
    api.balance_equals_ledger()


@pytest.mark.core
@pytest.mark.parametrize("run", range(3))
def test_concurrent_distinct_buys_cannot_lose_inventory(api, run):
    api.buy(quantity="1", trade_id="seed")
    responses = api.parallel([api.payload(trade_id=str(i), quantity="1") for i in range(12)])
    codes = [r.status_code for r in responses]
    assert codes == [201] * 12, codes
    assert len(api.ledger()) == 13
    api.balance_equals_ledger()
    assert D(api.position()["net_quantity"]) == 13


@pytest.mark.core
@pytest.mark.parametrize("run", range(5))
def test_concurrent_sells_cannot_oversell_or_diverge_from_ledger(api, run):
    api.buy()
    responses = api.parallel(
        [api.payload(trade_id="S" + str(i), side="SELL", quantity="7") for i in range(8)]
    )
    codes = sorted(r.status_code for r in responses)
    assert codes == [201] + [409] * 7, codes
    api.balance_equals_ledger()
    assert D(api.position()["net_quantity"]) == 3


@pytest.mark.robustness
@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "0"),
        ("quantity", "-1"),
        ("price", "0"),
        ("price", "-1"),
        ("quantity", "NaN"),
        ("price", "Infinity"),
        ("side", "buy"),
        ("timestamp", "2026-11-03T10:00:00"),
    ],
)
def test_invalid_trade_is_rejected_without_effect(api, field, value):
    r = api.post(**{field: value})
    assert r.status_code == 422, r.text
    assert api.ledger() == []


@pytest.mark.robustness
@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "-1"),
        ("price", "0"),
        ("side", "HOLD"),
        ("timestamp", "2026-11-03T10:00:00"),
    ],
)
def test_invalid_external_statement_row_is_rejected(api, field, value):
    r = api.reconcile([api.statement(**{field: value})])
    assert r.status_code == 422, r.text


@pytest.mark.robustness
@pytest.mark.parametrize("conflict", [False, True])
def test_duplicate_external_statement_keys_are_rejected(api, conflict):
    api.buy()
    second = api.statement(quantity="11") if conflict else api.statement()
    r = api.reconcile([api.statement(), second])
    assert r.status_code == 422, r.text


@pytest.mark.policy
@pytest.mark.parametrize(
    "field,value",
    [("quantity", "11"), ("price", "101"), ("side", "SELL"), ("symbol", "OTHER-USD")],
)
def test_changed_payload_for_existing_key_is_not_silently_ignored(api, field, value):
    api.buy()
    r = api.post(**{field: value})
    assert r.status_code == 409, (
        f"Expected explicit identity conflict, received {r.status_code}: {r.text}"
    )
