"""New multi-process regressions beyond the original failing audit."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D

import httpx
import pytest

pytestmark = pytest.mark.core


def test_concurrent_price_creation_uses_atomic_upsert(api):
    def set_price(i):
        with httpx.Client(base_url=api.url, timeout=20, trust_env=False) as client:
            return client.post("/pnl/set-price", json={"symbol": api.symbol, "price": str(100 + i)})

    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(set_price, range(12)))
    assert all(r.status_code == 204 for r in responses)
    api.buy()
    assert D(api.pnl()["latest_price"]) in {D(100 + i) for i in range(12)}


def test_concurrent_valid_sales_keep_both_cost_methods_consistent(api):
    api.buy()
    api.buy(trade_id="B2", price="110")
    payloads = [
        api.payload(trade_id="S" + str(i), side="SELL", quantity="1", price="120")
        for i in range(12)
    ]
    assert all(r.status_code == 201 for r in api.parallel(payloads))
    api.mark()
    p = api.pnl()
    assert D(p["net_quantity"]) == 8
    assert D(p["avg_cost_realized_pnl"]) == 180
    assert D(p["avg_cost_unrealized_pnl"]) == 160
    assert D(p["fifo_realized_pnl"]) == 220
    assert D(p["fifo_unrealized_pnl"]) == 120
    api.balance_equals_ledger()


def test_concurrent_duplicate_closing_sales_still_succeed(api):
    api.buy()
    payload = api.payload(trade_id="close", side="SELL")
    responses = api.parallel([payload] * 12)
    assert sorted(r.status_code for r in responses) == [200] * 11 + [201]
    assert D(api.position()["net_quantity"]) == 0
    api.balance_equals_ledger()


def test_all_failed_sales_leave_identity_available_after_top_up(api):
    api.buy()
    payload = api.payload(trade_id="sale", side="SELL", quantity="11")
    responses = api.parallel([payload] * 8)
    assert all(r.status_code == 409 for r in responses)
    assert len(api.ledger()) == 1
    api.buy(trade_id="topup", quantity="1")
    assert api.client.post("/trades/", json=payload).status_code == 201
    api.balance_equals_ledger()
