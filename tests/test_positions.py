from decimal import Decimal

from tests.conftest import make_trade


def test_no_positions_initially(client):
    resp = client.get("/positions/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_single_buy_creates_position(client):
    client.post("/trades/", json=make_trade(trade_id="T-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    resp = client.get("/positions/")
    assert resp.status_code == 200
    positions = resp.json()
    assert len(positions) == 1
    pos = positions[0]
    assert pos["symbol"] == "BTC-USD"
    assert Decimal(pos["net_quantity"]) == Decimal("10")
    assert Decimal(pos["average_cost"]) == Decimal("100")


def test_two_buys_accumulate(client):
    client.post("/trades/", json=make_trade(trade_id="T-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    client.post("/trades/", json=make_trade(trade_id="T-2", symbol="BTC-USD", side="BUY", quantity="10", price="110"))
    resp = client.get("/positions/")
    assert resp.status_code == 200
    positions = resp.json()
    assert len(positions) == 1
    pos = positions[0]
    assert Decimal(pos["net_quantity"]) == Decimal("20")
    assert Decimal(pos["average_cost"]) == Decimal("105")


def test_buy_then_sell_reduces_position(client):
    client.post("/trades/", json=make_trade(trade_id="T-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    client.post("/trades/", json=make_trade(trade_id="T-2", symbol="BTC-USD", side="SELL", quantity="5", price="120"))
    resp = client.get("/positions/")
    assert resp.status_code == 200
    pos = resp.json()[0]
    assert Decimal(pos["net_quantity"]) == Decimal("5")


def test_average_cost_unchanged_after_sell(client):
    client.post("/trades/", json=make_trade(trade_id="T-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    client.post("/trades/", json=make_trade(trade_id="T-2", symbol="BTC-USD", side="SELL", quantity="5", price="120"))
    resp = client.get("/positions/")
    assert resp.status_code == 200
    pos = resp.json()[0]
    assert Decimal(pos["average_cost"]) == Decimal("100")


def test_multiple_symbols_independent(client):
    client.post("/trades/", json=make_trade(trade_id="T-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    client.post("/trades/", json=make_trade(trade_id="T-2", symbol="ETH-USD", side="BUY", quantity="20", price="50"))
    resp = client.get("/positions/")
    assert resp.status_code == 200
    positions = resp.json()
    assert len(positions) == 2
    symbols = {p["symbol"] for p in positions}
    assert symbols == {"BTC-USD", "ETH-USD"}


def test_get_position_by_symbol(client):
    client.post("/trades/", json=make_trade(trade_id="T-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    resp = client.get("/positions/BTC-USD")
    assert resp.status_code == 200
    pos = resp.json()
    assert pos["symbol"] == "BTC-USD"
    assert Decimal(pos["net_quantity"]) == Decimal("10")
    assert Decimal(pos["average_cost"]) == Decimal("100")


def test_get_position_symbol_not_found(client):
    resp = client.get("/positions/DOGE-USD")
    assert resp.status_code == 404


def test_position_across_venues(client):
    client.post("/trades/", json=make_trade(trade_id="T-1", venue="binance", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    client.post("/trades/", json=make_trade(trade_id="T-2", venue="coinbase", symbol="BTC-USD", side="BUY", quantity="5", price="100"))
    resp = client.get("/positions/BTC-USD")
    assert resp.status_code == 200
    pos = resp.json()
    assert Decimal(pos["net_quantity"]) == Decimal("15")


def test_full_exit_position_shows_zero(client):
    client.post("/trades/", json=make_trade(trade_id="T-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    client.post("/trades/", json=make_trade(trade_id="T-2", symbol="BTC-USD", side="SELL", quantity="10", price="110"))
    resp = client.get("/positions/BTC-USD")
    assert resp.status_code == 200
    pos = resp.json()
    assert Decimal(pos["net_quantity"]) == Decimal("0")


def test_average_cost_with_different_prices(client):
    client.post("/trades/", json=make_trade(trade_id="T-1", symbol="BTC-USD", side="BUY", quantity="5", price="100"))
    client.post("/trades/", json=make_trade(trade_id="T-2", symbol="BTC-USD", side="BUY", quantity="15", price="200"))
    resp = client.get("/positions/BTC-USD")
    assert resp.status_code == 200
    pos = resp.json()
    assert Decimal(pos["average_cost"]) == Decimal("175")


def test_symbol_case_insensitive(client):
    client.post("/trades/", json=make_trade(trade_id="T-1", symbol="btc-usd", side="BUY", quantity="10", price="100"))
    resp = client.get("/positions/BTC-USD")
    assert resp.status_code == 200
    pos = resp.json()
    assert pos["symbol"] == "BTC-USD"
    assert Decimal(pos["net_quantity"]) == Decimal("10")
