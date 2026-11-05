"""Comprehensive PnL calculation tests."""

from decimal import Decimal


def make_trade(
    venue="binance",
    trade_id="T-1001",
    symbol="BTC-USD",
    side="BUY",
    quantity="10",
    price="100",
    timestamp="2026-11-03T10:00:00Z",
) -> dict:
    return {
        "venue": venue,
        "trade_id": trade_id,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
        "timestamp": timestamp,
    }


# ---------------------------------------------------------------------------
# Worked example from the spec
# BUY 10 @ 100, BUY 10 @ 110, SELL 5 @ 120, latest price 125
# Average-cost: qty=15, avg_cost=105, realized=75, unrealized=300
# FIFO:         qty=15, fifo_avg_cost≈106.67, realized=100, unrealized=275
# ---------------------------------------------------------------------------


def _ingest_worked_example(client):
    """Helper that ingests the three trades and sets the latest price."""
    trades = [
        make_trade(trade_id="T-1", side="BUY", quantity="10", price="100"),
        make_trade(trade_id="T-2", side="BUY", quantity="10", price="110"),
        make_trade(trade_id="T-3", side="SELL", quantity="5", price="120"),
    ]
    for t in trades:
        resp = client.post("/trades/", json=t)
        assert resp.status_code in (200, 201), resp.text

    resp = client.post("/pnl/set-price", json={"symbol": "BTC-USD", "price": "125"})
    assert resp.status_code == 204


def test_worked_example_average_cost(client):
    """Worked example — average-cost fields."""
    _ingest_worked_example(client)

    resp = client.get("/pnl/", params={"symbol": "BTC-USD"})
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    data = entries[0]

    assert data["symbol"] == "BTC-USD"
    assert Decimal(str(data["net_quantity"])) == Decimal("15")
    assert Decimal(str(data["avg_cost"])) == Decimal("105")
    assert Decimal(str(data["avg_cost_realized_pnl"])) == Decimal("75")
    assert Decimal(str(data["avg_cost_unrealized_pnl"])) == Decimal("300")
    assert Decimal(str(data["latest_price"])) == Decimal("125")


def test_worked_example_fifo(client):
    """Worked example — FIFO fields."""
    _ingest_worked_example(client)

    resp = client.get("/pnl/", params={"symbol": "BTC-USD"})
    assert resp.status_code == 200
    data = resp.json()[0]

    assert Decimal(str(data["net_quantity"])) == Decimal("15")
    # FIFO avg cost = (5*100 + 10*110) / 15 = 1600/15 ≈ 106.67
    assert round(Decimal(str(data["fifo_avg_cost"])), 2) == Decimal("106.67")
    assert Decimal(str(data["fifo_realized_pnl"])) == Decimal("100")
    assert Decimal(str(data["fifo_unrealized_pnl"])) == Decimal("275")


def test_no_trades_empty_pnl(client):
    """GET /pnl/ with no trades returns an empty list."""
    resp = client.get("/pnl/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_only_buys_no_realized_pnl(client):
    """A single BUY with no sells → realized = 0, unrealized = (price - cost) * qty."""
    client.post("/trades/", json=make_trade(trade_id="T-1", side="BUY", quantity="10", price="100"))
    client.post("/pnl/set-price", json={"symbol": "BTC-USD", "price": "120"})

    resp = client.get("/pnl/", params={"symbol": "BTC-USD"})
    assert resp.status_code == 200
    data = resp.json()[0]

    assert Decimal(str(data["net_quantity"])) == Decimal("10")
    assert Decimal(str(data["avg_cost_realized_pnl"])) == Decimal("0")
    assert Decimal(str(data["avg_cost_unrealized_pnl"])) == Decimal("200")
    assert Decimal(str(data["fifo_realized_pnl"])) == Decimal("0")
    assert Decimal(str(data["fifo_unrealized_pnl"])) == Decimal("200")


def test_set_latest_price(client):
    """POST /pnl/set-price returns 204 and the price appears in GET /pnl/."""
    client.post("/trades/", json=make_trade(trade_id="T-1"))
    resp = client.post("/pnl/set-price", json={"symbol": "BTC-USD", "price": "250"})
    assert resp.status_code == 204

    data = client.get("/pnl/", params={"symbol": "BTC-USD"}).json()[0]
    assert Decimal(str(data["latest_price"])) == Decimal("250")


def test_set_latest_price_update(client):
    """Setting the price twice — the second value wins."""
    client.post("/trades/", json=make_trade(trade_id="T-1"))
    client.post("/pnl/set-price", json={"symbol": "BTC-USD", "price": "200"})
    client.post("/pnl/set-price", json={"symbol": "BTC-USD", "price": "300"})

    data = client.get("/pnl/", params={"symbol": "BTC-USD"}).json()[0]
    assert Decimal(str(data["latest_price"])) == Decimal("300")


def test_unrealized_pnl_without_latest_price(client):
    """No price set → unrealized = 0 and latest_price is null."""
    client.post("/trades/", json=make_trade(trade_id="T-1", side="BUY", quantity="10", price="100"))

    data = client.get("/pnl/", params={"symbol": "BTC-USD"}).json()[0]
    assert data["latest_price"] is None
    assert Decimal(str(data["avg_cost_unrealized_pnl"])) == Decimal("0")
    assert Decimal(str(data["fifo_unrealized_pnl"])) == Decimal("0")


def test_pnl_filter_by_symbol(client):
    """Two symbols ingested; filtering returns only the requested one."""
    client.post("/trades/", json=make_trade(trade_id="T-1", symbol="BTC-USD"))
    client.post("/trades/", json=make_trade(trade_id="T-2", symbol="ETH-USD", price="50"))

    btc = client.get("/pnl/", params={"symbol": "BTC-USD"}).json()
    assert len(btc) == 1
    assert btc[0]["symbol"] == "BTC-USD"

    eth = client.get("/pnl/", params={"symbol": "ETH-USD"}).json()
    assert len(eth) == 1
    assert eth[0]["symbol"] == "ETH-USD"


def test_full_exit_position(client):
    """BUY 10 @ 100, SELL 10 @ 120 → qty=0, realized=200, unrealized=0."""
    client.post("/trades/", json=make_trade(trade_id="T-1", side="BUY", quantity="10", price="100"))
    client.post("/trades/", json=make_trade(trade_id="T-2", side="SELL", quantity="10", price="120"))
    client.post("/pnl/set-price", json={"symbol": "BTC-USD", "price": "130"})

    data = client.get("/pnl/", params={"symbol": "BTC-USD"}).json()[0]

    assert Decimal(str(data["net_quantity"])) == Decimal("0")
    assert Decimal(str(data["avg_cost_realized_pnl"])) == Decimal("200")
    assert Decimal(str(data["avg_cost_unrealized_pnl"])) == Decimal("0")
    assert Decimal(str(data["fifo_realized_pnl"])) == Decimal("200")
    assert Decimal(str(data["fifo_unrealized_pnl"])) == Decimal("0")


def test_multiple_sells_accumulated_realized(client):
    """BUY 20 @ 100, SELL 5 @ 110, SELL 5 @ 120 → accumulated realized PnL."""
    client.post("/trades/", json=make_trade(trade_id="T-1", side="BUY", quantity="20", price="100"))
    client.post("/trades/", json=make_trade(trade_id="T-2", side="SELL", quantity="5", price="110"))
    client.post("/trades/", json=make_trade(trade_id="T-3", side="SELL", quantity="5", price="120"))

    data = client.get("/pnl/", params={"symbol": "BTC-USD"}).json()[0]

    assert Decimal(str(data["net_quantity"])) == Decimal("10")
    # avg_cost realized = 5*(110-100) + 5*(120-100) = 50 + 100 = 150
    assert Decimal(str(data["avg_cost_realized_pnl"])) == Decimal("150")
    # FIFO realized = same here (single lot) = 150
    assert Decimal(str(data["fifo_realized_pnl"])) == Decimal("150")


def test_pnl_negative_realized(client):
    """BUY 10 @ 100, SELL 5 @ 80 → negative realized PnL (loss)."""
    client.post("/trades/", json=make_trade(trade_id="T-1", side="BUY", quantity="10", price="100"))
    client.post("/trades/", json=make_trade(trade_id="T-2", side="SELL", quantity="5", price="80"))

    data = client.get("/pnl/", params={"symbol": "BTC-USD"}).json()[0]

    assert Decimal(str(data["net_quantity"])) == Decimal("5")
    # avg_cost realized = 5*(80-100) = -100
    assert Decimal(str(data["avg_cost_realized_pnl"])) == Decimal("-100")
    # FIFO realized = same (single lot)
    assert Decimal(str(data["fifo_realized_pnl"])) == Decimal("-100")


def test_pnl_multiple_symbols_independent(client):
    """BTC and ETH positions are computed independently."""
    # BTC: BUY 10 @ 100
    client.post("/trades/", json=make_trade(trade_id="T-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    # ETH: BUY 20 @ 50
    client.post("/trades/", json=make_trade(trade_id="T-2", symbol="ETH-USD", side="BUY", quantity="20", price="50"))

    client.post("/pnl/set-price", json={"symbol": "BTC-USD", "price": "120"})
    client.post("/pnl/set-price", json={"symbol": "ETH-USD", "price": "60"})

    all_pnl = client.get("/pnl/").json()
    assert len(all_pnl) == 2

    by_symbol = {entry["symbol"]: entry for entry in all_pnl}

    btc = by_symbol["BTC-USD"]
    assert Decimal(str(btc["net_quantity"])) == Decimal("10")
    assert Decimal(str(btc["avg_cost"])) == Decimal("100")
    assert Decimal(str(btc["avg_cost_unrealized_pnl"])) == Decimal("200")

    eth = by_symbol["ETH-USD"]
    assert Decimal(str(eth["net_quantity"])) == Decimal("20")
    assert Decimal(str(eth["avg_cost"])) == Decimal("50")
    assert Decimal(str(eth["avg_cost_unrealized_pnl"])) == Decimal("200")
