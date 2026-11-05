"""Comprehensive idempotency tests for the trade ingestion endpoint."""

from tests.conftest import make_trade


def test_duplicate_trade_returns_200(client):
    """Same (venue, trade_id) sent twice → second returns 200 with duplicate=true."""
    trade = make_trade(venue="binance", trade_id="T-1001")

    r1 = client.post("/trades/", json=trade)
    assert r1.status_code == 201

    r2 = client.post("/trades/", json=trade)
    assert r2.status_code == 200
    assert r2.json()["duplicate"] is True


def test_duplicate_trade_same_body(client):
    """Response body is identical for both requests except the duplicate flag."""
    trade = make_trade(venue="binance", trade_id="T-1001")

    r1 = client.post("/trades/", json=trade)
    r2 = client.post("/trades/", json=trade)

    body1 = r1.json()
    body2 = r2.json()

    # Remove the duplicate flag before comparing the rest of the payload
    dup1 = body1.pop("duplicate", None)
    dup2 = body2.pop("duplicate", None)

    assert dup2 is True
    # First insert should either omit the flag or set it to False/None
    assert dup1 is None or dup1 is False

    assert body1 == body2


def test_duplicate_does_not_double_count_position(client):
    """Position quantity is 10, not 20, after sending the same trade twice."""
    trade = make_trade(venue="binance", trade_id="T-1001", symbol="BTC-USD",
                       side="BUY", quantity="10", price="100")

    client.post("/trades/", json=trade)
    client.post("/trades/", json=trade)

    positions = client.get("/positions/").json()
    btc_positions = [p for p in positions if p["symbol"] == "BTC-USD"]
    assert len(btc_positions) == 1
    assert float(btc_positions[0]["quantity"]) == 10


def test_different_venue_same_trade_id_both_accepted(client):
    """(binance, T-1) and (coinbase, T-1) are distinct trades — both return 201."""
    trade_binance = make_trade(venue="binance", trade_id="T-1", symbol="BTC-USD",
                               side="BUY", quantity="10", price="100")
    trade_coinbase = make_trade(venue="coinbase", trade_id="T-1", symbol="BTC-USD",
                                side="BUY", quantity="10", price="100")

    r1 = client.post("/trades/", json=trade_binance)
    r2 = client.post("/trades/", json=trade_coinbase)

    assert r1.status_code == 201
    assert r2.status_code == 201

    positions = client.get("/positions/").json()
    btc_positions = [p for p in positions if p["symbol"] == "BTC-USD"]
    total_qty = sum(float(p["quantity"]) for p in btc_positions)
    assert total_qty == 20


def test_different_trade_id_same_venue_both_accepted(client):
    """(binance, T-1) and (binance, T-2) are distinct trades — both return 201."""
    trade1 = make_trade(venue="binance", trade_id="T-1", symbol="BTC-USD",
                        side="BUY", quantity="10", price="100")
    trade2 = make_trade(venue="binance", trade_id="T-2", symbol="BTC-USD",
                        side="BUY", quantity="10", price="100")

    r1 = client.post("/trades/", json=trade1)
    r2 = client.post("/trades/", json=trade2)

    assert r1.status_code == 201
    assert r2.status_code == 201


def test_duplicate_sell_not_double_counted(client):
    """BUY 10, SELL 5, replay SELL 5 → position should be 5, not 0."""
    buy = make_trade(venue="binance", trade_id="T-BUY", symbol="BTC-USD",
                     side="BUY", quantity="10", price="100")
    sell = make_trade(venue="binance", trade_id="T-SELL", symbol="BTC-USD",
                      side="SELL", quantity="5", price="100")

    r_buy = client.post("/trades/", json=buy)
    assert r_buy.status_code == 201

    r_sell1 = client.post("/trades/", json=sell)
    assert r_sell1.status_code == 201

    r_sell2 = client.post("/trades/", json=sell)
    assert r_sell2.status_code == 200
    assert r_sell2.json()["duplicate"] is True

    positions = client.get("/positions/").json()
    btc_positions = [p for p in positions if p["symbol"] == "BTC-USD"]
    assert len(btc_positions) == 1
    assert float(btc_positions[0]["quantity"]) == 5


def test_many_duplicates(client):
    """Send the same trade 10 times → first returns 201, rest return 200; position=10."""
    trade = make_trade(venue="binance", trade_id="T-MANY", symbol="BTC-USD",
                       side="BUY", quantity="10", price="100")

    responses = [client.post("/trades/", json=trade) for _ in range(10)]

    assert responses[0].status_code == 201
    for r in responses[1:]:
        assert r.status_code == 200
        assert r.json()["duplicate"] is True

    positions = client.get("/positions/").json()
    btc_positions = [p for p in positions if p["symbol"] == "BTC-USD"]
    total_qty = sum(float(p["quantity"]) for p in btc_positions)
    assert total_qty == 10


def test_duplicate_returns_original_id(client):
    """Second send returns the same `id` as the first (original record id)."""
    trade = make_trade(venue="binance", trade_id="T-1001")

    r1 = client.post("/trades/", json=trade)
    r2 = client.post("/trades/", json=trade)

    assert r1.status_code == 201
    assert r2.status_code == 200

    assert "id" in r1.json()
    assert "id" in r2.json()
    assert r1.json()["id"] == r2.json()["id"]
