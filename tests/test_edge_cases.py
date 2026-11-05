"""
Edge-case and boundary tests for the trading system.

These tests probe decimal precision, boundary values, cross-symbol isolation,
idempotency semantics, and input-validation guardrails — the kind of corners
that blow up in production at 3 AM.
"""

from decimal import Decimal

from tests.conftest import make_trade


# ── 1. Smoke / Health ────────────────────────────────────────────────────────

def test_health_endpoint(client):
    """GET /health returns 200 with {"status": "ok"}."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── 2-5. Boundary quantities & prices ────────────────────────────────────────

def test_very_small_quantity(client):
    """The system must accept sub-satoshi quantities (8 decimal places)."""
    trade = make_trade(trade_id="edge-small-qty", quantity="0.00000001")
    resp = client.post("/trades/", json=trade)
    assert resp.status_code == 201


def test_very_large_quantity(client):
    """Large notional sizes must not overflow or truncate."""
    trade = make_trade(trade_id="edge-large-qty", quantity="99999999999.99")
    resp = client.post("/trades/", json=trade)
    assert resp.status_code == 201


def test_very_small_price(client):
    """Penny-priced assets (e.g. penny stocks, meme coins) are valid."""
    trade = make_trade(trade_id="edge-small-px", price="0.01")
    resp = client.post("/trades/", json=trade)
    assert resp.status_code == 201


def test_very_large_price(client):
    """High-priced assets (BRK.A territory) must be representable."""
    trade = make_trade(trade_id="edge-large-px", price="999999999.99")
    resp = client.post("/trades/", json=trade)
    assert resp.status_code == 201


# ── 6. Decimal precision ─────────────────────────────────────────────────────

def test_decimal_precision_not_float(client):
    """
    3 × 0.1 must equal exactly 0.3 — NOT the IEEE-754 float artifact
    0.30000000000000004.  This is a classic trading-system footgun;
    money math requires Decimal / fixed-point.
    """
    for i in range(3):
        trade = make_trade(trade_id=f"dec-prec-{i}", quantity="1", price="0.1")
        resp = client.post("/trades/", json=trade)
        assert resp.status_code == 201

    pos = client.get("/positions/BTC-USD").json()
    total_cost = Decimal(str(pos["total_cost"]))
    assert total_cost == Decimal("0.3"), (
        f"Expected exactly 0.3, got {total_cost} — likely using float arithmetic"
    )


# ── 7. Sell-then-buy lifecycle ────────────────────────────────────────────────

def test_sell_then_buy_same_symbol(client):
    """BUY 10 → SELL 10 (flat) → BUY 5 → position should be 5."""
    client.post("/trades/", json=make_trade(trade_id="stb-buy1", quantity="10", side="BUY"))
    client.post("/trades/", json=make_trade(trade_id="stb-sell1", quantity="10", side="SELL"))

    # Position should be flat after full exit
    pos = client.get("/positions/BTC-USD").json()
    assert Decimal(str(pos["quantity"])) == Decimal("0")

    # Re-enter the position
    client.post("/trades/", json=make_trade(trade_id="stb-buy2", quantity="5", side="BUY"))
    pos = client.get("/positions/BTC-USD").json()
    assert Decimal(str(pos["quantity"])) == Decimal("5")


# ── 8. Cross-symbol isolation ─────────────────────────────────────────────────

def test_multiple_symbols_no_cross_contamination(client):
    """
    Operations on BTC-USD must never leak into ETH-USD positions or PnL.
    A broken aggregate key would merge them silently.
    """
    client.post("/trades/", json=make_trade(trade_id="iso-btc", symbol="BTC-USD",
                                            quantity="10", price="100"))
    client.post("/trades/", json=make_trade(trade_id="iso-eth", symbol="ETH-USD",
                                            quantity="5", price="200"))

    btc = client.get("/positions/BTC-USD").json()
    eth = client.get("/positions/ETH-USD").json()

    assert Decimal(str(btc["quantity"])) == Decimal("10")
    assert Decimal(str(eth["quantity"])) == Decimal("5")

    # Set price only for BTC and verify PnL doesn't bleed
    client.post("/pnl/set-price", json={"symbol": "BTC-USD", "price": "150"})
    client.post("/pnl/set-price", json={"symbol": "ETH-USD", "price": "200"})

    pnl = client.get("/pnl/").json()
    # Find per-symbol entries
    btc_pnl = next((p for p in pnl if p["symbol"] == "BTC-USD"), None)
    eth_pnl = next((p for p in pnl if p["symbol"] == "ETH-USD"), None)

    assert btc_pnl is not None
    assert eth_pnl is not None
    # ETH was bought and marked at the same price → zero unrealized
    assert Decimal(str(eth_pnl["unrealized_pnl"])) == Decimal("0")
    # BTC should have positive unrealized (150-100)*10 = 500
    assert Decimal(str(btc_pnl["unrealized_pnl"])) == Decimal("500")


# ── 9. Multi-venue aggregation ────────────────────────────────────────────────

def test_buy_from_many_venues_same_symbol(client):
    """
    Positions aggregate across venues.  5 venues × 10 qty = 50.
    A per-venue position bug would show 10 instead.
    """
    venues = ["binance", "coinbase", "kraken", "ftx", "bybit"]
    for i, venue in enumerate(venues):
        trade = make_trade(trade_id=f"mv-{venue}-{i}", venue=venue,
                           quantity="10", price="100")
        resp = client.post("/trades/", json=trade)
        assert resp.status_code == 201

    pos = client.get("/positions/BTC-USD").json()
    assert Decimal(str(pos["quantity"])) == Decimal("50")


# ── 10. Timestamp formats ────────────────────────────────────────────────────

def test_timestamp_utc_and_offset_both_work(client):
    """Both Zulu ('Z') and explicit UTC-offset timestamps must be accepted."""
    utc_trade = make_trade(trade_id="ts-utc", timestamp="2026-01-01T00:00:00Z")
    offset_trade = make_trade(trade_id="ts-offset", timestamp="2026-01-01T05:30:00+05:30")

    resp_utc = client.post("/trades/", json=utc_trade)
    resp_off = client.post("/trades/", json=offset_trade)

    assert resp_utc.status_code == 201
    assert resp_off.status_code == 201


# ── 11-12. Price validation on set-price ──────────────────────────────────────

def test_set_price_negative_rejected(client):
    """Negative mark prices are nonsensical and must be rejected (422)."""
    resp = client.post("/pnl/set-price", json={"symbol": "BTC-USD", "price": "-10"})
    assert resp.status_code == 422


def test_set_price_zero_rejected(client):
    """A zero mark price would zero out all unrealized PnL — reject it."""
    resp = client.post("/pnl/set-price", json={"symbol": "BTC-USD", "price": "0"})
    assert resp.status_code == 422


# ── 13. PnL after full exit ──────────────────────────────────────────────────

def test_pnl_after_full_exit(client):
    """
    BUY 10 @ 100, SELL 10 @ 150 → realized = (150-100)*10 = 500, qty = 0.
    The system must crystallize realized PnL even when the position is flat.
    """
    client.post("/trades/", json=make_trade(trade_id="exit-buy", quantity="10",
                                            price="100", side="BUY"))
    client.post("/trades/", json=make_trade(trade_id="exit-sell", quantity="10",
                                            price="150", side="SELL"))

    pos = client.get("/positions/BTC-USD").json()
    assert Decimal(str(pos["quantity"])) == Decimal("0")

    client.post("/pnl/set-price", json={"symbol": "BTC-USD", "price": "150"})
    pnl = client.get("/pnl/").json()
    btc_pnl = next(p for p in pnl if p["symbol"] == "BTC-USD")
    assert Decimal(str(btc_pnl["realized_pnl"])) == Decimal("500")


# ── 14. High-volume trade ingestion ──────────────────────────────────────────

def test_position_with_many_trades(client):
    """
    100 sequential buys of 1 unit must sum to exactly 100.
    Catches off-by-one errors, accumulator drift, and precision loss
    over many additions.
    """
    for i in range(100):
        trade = make_trade(trade_id=f"bulk-{i:04d}", quantity="1", price="50")
        resp = client.post("/trades/", json=trade)
        assert resp.status_code == 201

    pos = client.get("/positions/BTC-USD").json()
    assert Decimal(str(pos["quantity"])) == Decimal("100")


# ── 15-16. Special characters in identifiers ─────────────────────────────────

def test_special_chars_in_venue(client):
    """Venue names with hyphens / underscores must not break routing."""
    trade = make_trade(trade_id="sc-venue", venue="kraken-futures_perp")
    resp = client.post("/trades/", json=trade)
    assert resp.status_code == 201


def test_special_chars_in_trade_id(client):
    """
    Trade IDs with slashes and hyphens (common in FIX/exchange formats)
    must not be rejected or mis-parsed.
    """
    trade = make_trade(trade_id="ORD-2026/11/03-001")
    resp = client.post("/trades/", json=trade)
    assert resp.status_code == 201


# ── 17. Idempotency after a sell ──────────────────────────────────────────────

def test_idempotent_replay_after_sell(client):
    """
    Replaying a SELL that was already applied must be idempotent (200 dup),
    NOT reduce the position a second time.

    Scenario: BUY 10 → SELL 5 → replay same SELL → position must stay 5.
    A naive implementation that re-applies the sell would leave position = 0.
    """
    client.post("/trades/", json=make_trade(trade_id="idem-buy", quantity="10", side="BUY"))

    sell_trade = make_trade(trade_id="idem-sell", quantity="5", side="SELL")
    resp1 = client.post("/trades/", json=sell_trade)
    assert resp1.status_code == 201

    # Replay the exact same sell
    resp2 = client.post("/trades/", json=sell_trade)
    assert resp2.status_code == 200  # duplicate acknowledged, not re-applied

    pos = client.get("/positions/BTC-USD").json()
    assert Decimal(str(pos["quantity"])) == Decimal("5"), (
        "Duplicate sell was re-applied — idempotency is broken"
    )
