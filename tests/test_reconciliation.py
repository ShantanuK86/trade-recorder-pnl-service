"""Comprehensive reconciliation endpoint tests."""

from tests.conftest import make_trade


def _reconcile(client, venue: str, trades: list[dict]) -> dict:
    """Helper to call the reconciliation endpoint."""
    response = client.post("/reconciliation/", json={"venue": venue, "trades": trades})
    assert response.status_code == 200
    return response.json()


def _venue_trade(trade_id: str, symbol: str, side: str, quantity: str, price: str, timestamp: str) -> dict:
    """Build a venue-statement trade dict."""
    return {
        "trade_id": trade_id,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
        "timestamp": timestamp,
    }


# ---------------------------------------------------------------------------
# 1. Spec example
# ---------------------------------------------------------------------------
def test_spec_example_reconciliation(client):
    """The exact example from the spec:
    Stored: T1 (BUY 10@100 BTC-USD), T2 (BUY 5@101 BTC-USD)
    Venue:  T1 (10@100), T2 (6@101), T3 (1@99)
    Expected: T2 quantity mismatch (stored=5, venue=6), T3 missing.
    """
    # Ingest stored trades
    client.post("/trades/", json=make_trade(venue="binance", trade_id="T1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    client.post("/trades/", json=make_trade(venue="binance", trade_id="T2", symbol="BTC-USD", side="BUY", quantity="5", price="101"))

    # Venue statement
    venue_trades = [
        _venue_trade("T1", "BTC-USD", "BUY", "10", "100", "2026-11-03T10:00:00Z"),
        _venue_trade("T2", "BTC-USD", "BUY", "6", "101", "2026-11-03T10:00:00Z"),
        _venue_trade("T3", "BTC-USD", "BUY", "1", "99", "2026-11-03T10:00:00Z"),
    ]

    result = _reconcile(client, "binance", venue_trades)

    assert result["venue"] == "binance"

    # T3 is missing from our records
    missing_ids = [t["trade_id"] for t in result["missing"]]
    assert "T3" in missing_ids

    # No extra trades (all stored trades appear in venue statement)
    assert result["extra"] == []

    # T2 has a quantity mismatch
    mismatched_ids = [t["trade_id"] for t in result["mismatched"]]
    assert "T2" in mismatched_ids
    t2_entry = next(t for t in result["mismatched"] if t["trade_id"] == "T2")
    qty_mismatch = next(m for m in t2_entry["mismatches"] if m["field"] == "quantity")
    assert qty_mismatch["stored"] == "5"
    assert qty_mismatch["venue"] == "6"


# ---------------------------------------------------------------------------
# 2. Perfect match
# ---------------------------------------------------------------------------
def test_perfect_match(client):
    """Stored and venue match exactly → all lists empty."""
    client.post("/trades/", json=make_trade(venue="binance", trade_id="PM-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))

    venue_trades = [
        _venue_trade("PM-1", "BTC-USD", "BUY", "10", "100", "2026-11-03T10:00:00Z"),
    ]

    result = _reconcile(client, "binance", venue_trades)

    assert result["missing"] == []
    assert result["extra"] == []
    assert result["mismatched"] == []


# ---------------------------------------------------------------------------
# 3. Missing trade
# ---------------------------------------------------------------------------
def test_missing_trade(client):
    """Venue has a trade we don't have → appears in missing."""
    venue_trades = [
        _venue_trade("MISS-1", "ETH-USD", "SELL", "5", "200", "2026-11-03T10:00:00Z"),
    ]

    # Ingest an unrelated trade so the venue isn't compared against nothing
    client.post("/trades/", json=make_trade(venue="binance", trade_id="MISS-2", symbol="BTC-USD", side="BUY", quantity="1", price="50"))

    result = _reconcile(client, "binance", venue_trades)

    missing_ids = [t["trade_id"] for t in result["missing"]]
    assert "MISS-1" in missing_ids


# ---------------------------------------------------------------------------
# 4. Extra trade
# ---------------------------------------------------------------------------
def test_extra_trade(client):
    """We have a trade venue doesn't report → appears in extra."""
    client.post("/trades/", json=make_trade(venue="binance", trade_id="EX-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    client.post("/trades/", json=make_trade(venue="binance", trade_id="EX-2", symbol="ETH-USD", side="BUY", quantity="3", price="300"))

    # Venue only knows about EX-1
    venue_trades = [
        _venue_trade("EX-1", "BTC-USD", "BUY", "10", "100", "2026-11-03T10:00:00Z"),
    ]

    result = _reconcile(client, "binance", venue_trades)

    extra_ids = [t["trade_id"] for t in result["extra"]]
    assert "EX-2" in extra_ids
    assert result["missing"] == []


# ---------------------------------------------------------------------------
# 5. Quantity mismatch
# ---------------------------------------------------------------------------
def test_quantity_mismatch(client):
    """Same trade_id, different quantity → mismatched."""
    client.post("/trades/", json=make_trade(venue="binance", trade_id="QM-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))

    venue_trades = [
        _venue_trade("QM-1", "BTC-USD", "BUY", "7", "100", "2026-11-03T10:00:00Z"),
    ]

    result = _reconcile(client, "binance", venue_trades)

    assert result["missing"] == []
    assert result["extra"] == []
    assert len(result["mismatched"]) == 1
    entry = result["mismatched"][0]
    assert entry["trade_id"] == "QM-1"
    qty = next(m for m in entry["mismatches"] if m["field"] == "quantity")
    assert qty["stored"] == "10"
    assert qty["venue"] == "7"


# ---------------------------------------------------------------------------
# 6. Price mismatch
# ---------------------------------------------------------------------------
def test_price_mismatch(client):
    """Same trade_id, different price → mismatched."""
    client.post("/trades/", json=make_trade(venue="binance", trade_id="PRC-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))

    venue_trades = [
        _venue_trade("PRC-1", "BTC-USD", "BUY", "10", "999", "2026-11-03T10:00:00Z"),
    ]

    result = _reconcile(client, "binance", venue_trades)

    assert len(result["mismatched"]) == 1
    entry = result["mismatched"][0]
    assert entry["trade_id"] == "PRC-1"
    price_m = next(m for m in entry["mismatches"] if m["field"] == "price")
    assert price_m["stored"] == "100"
    assert price_m["venue"] == "999"


# ---------------------------------------------------------------------------
# 7. Side mismatch
# ---------------------------------------------------------------------------
def test_side_mismatch(client):
    """Stored BUY, venue says SELL → mismatched."""
    client.post("/trades/", json=make_trade(venue="binance", trade_id="SD-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))

    venue_trades = [
        _venue_trade("SD-1", "BTC-USD", "SELL", "10", "100", "2026-11-03T10:00:00Z"),
    ]

    result = _reconcile(client, "binance", venue_trades)

    assert len(result["mismatched"]) == 1
    entry = result["mismatched"][0]
    assert entry["trade_id"] == "SD-1"
    side_m = next(m for m in entry["mismatches"] if m["field"] == "side")
    assert side_m["stored"] == "BUY"
    assert side_m["venue"] == "SELL"


# ---------------------------------------------------------------------------
# 8. Multiple mismatches on a single trade
# ---------------------------------------------------------------------------
def test_multiple_mismatches_single_trade(client):
    """Both quantity and price differ on the same trade."""
    client.post("/trades/", json=make_trade(venue="binance", trade_id="MM-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))

    venue_trades = [
        _venue_trade("MM-1", "BTC-USD", "BUY", "20", "200", "2026-11-03T10:00:00Z"),
    ]

    result = _reconcile(client, "binance", venue_trades)

    assert len(result["mismatched"]) == 1
    entry = result["mismatched"][0]
    assert entry["trade_id"] == "MM-1"
    fields = {m["field"] for m in entry["mismatches"]}
    assert "quantity" in fields
    assert "price" in fields

    qty = next(m for m in entry["mismatches"] if m["field"] == "quantity")
    assert qty["stored"] == "10"
    assert qty["venue"] == "20"

    price_m = next(m for m in entry["mismatches"] if m["field"] == "price")
    assert price_m["stored"] == "100"
    assert price_m["venue"] == "200"


# ---------------------------------------------------------------------------
# 9. Empty venue statement
# ---------------------------------------------------------------------------
def test_empty_venue_statement(client):
    """Venue sends empty list → all our trades are extra."""
    client.post("/trades/", json=make_trade(venue="binance", trade_id="EV-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    client.post("/trades/", json=make_trade(venue="binance", trade_id="EV-2", symbol="ETH-USD", side="BUY", quantity="5", price="300"))

    result = _reconcile(client, "binance", [])

    assert result["missing"] == []
    extra_ids = {t["trade_id"] for t in result["extra"]}
    assert extra_ids == {"EV-1", "EV-2"}
    assert result["mismatched"] == []


# ---------------------------------------------------------------------------
# 10. No stored trades
# ---------------------------------------------------------------------------
def test_no_stored_trades(client):
    """We have nothing stored → all venue trades are missing."""
    venue_trades = [
        _venue_trade("NS-1", "BTC-USD", "BUY", "10", "100", "2026-11-03T10:00:00Z"),
        _venue_trade("NS-2", "ETH-USD", "SELL", "5", "200", "2026-11-03T10:00:00Z"),
    ]

    result = _reconcile(client, "binance", venue_trades)

    missing_ids = {t["trade_id"] for t in result["missing"]}
    assert missing_ids == {"NS-1", "NS-2"}
    assert result["extra"] == []
    assert result["mismatched"] == []


# ---------------------------------------------------------------------------
# 11. Venue filtering
# ---------------------------------------------------------------------------
def test_venue_filtering(client):
    """Reconcile for binance doesn't include coinbase trades."""
    # Ingest trades on two different venues
    client.post("/trades/", json=make_trade(venue="binance", trade_id="VF-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    client.post("/trades/", json=make_trade(venue="coinbase", trade_id="VF-2", symbol="ETH-USD", side="BUY", quantity="5", price="300"))

    # Reconcile only binance — venue statement matches the binance trade exactly
    venue_trades = [
        _venue_trade("VF-1", "BTC-USD", "BUY", "10", "100", "2026-11-03T10:00:00Z"),
    ]

    result = _reconcile(client, "binance", venue_trades)

    # The coinbase trade (VF-2) must NOT appear as extra
    extra_ids = [t["trade_id"] for t in result["extra"]]
    assert "VF-2" not in extra_ids

    assert result["missing"] == []
    assert result["extra"] == []
    assert result["mismatched"] == []


# ---------------------------------------------------------------------------
# 12. Mixed results
# ---------------------------------------------------------------------------
def test_mixed_results(client):
    """Some missing, some extra, some mismatched all at once."""
    # MX-1: will match a venue trade with wrong quantity → mismatched
    client.post("/trades/", json=make_trade(venue="binance", trade_id="MX-1", symbol="BTC-USD", side="BUY", quantity="10", price="100"))
    # MX-2: stored but NOT in venue statement → extra
    client.post("/trades/", json=make_trade(venue="binance", trade_id="MX-2", symbol="ETH-USD", side="BUY", quantity="5", price="300"))

    venue_trades = [
        # MX-1 with different quantity
        _venue_trade("MX-1", "BTC-USD", "BUY", "99", "100", "2026-11-03T10:00:00Z"),
        # MX-3 not in our records → missing
        _venue_trade("MX-3", "SOL-USD", "BUY", "50", "25", "2026-11-03T10:00:00Z"),
    ]

    result = _reconcile(client, "binance", venue_trades)

    # Missing
    missing_ids = {t["trade_id"] for t in result["missing"]}
    assert "MX-3" in missing_ids

    # Extra
    extra_ids = {t["trade_id"] for t in result["extra"]}
    assert "MX-2" in extra_ids

    # Mismatched
    mismatched_ids = {t["trade_id"] for t in result["mismatched"]}
    assert "MX-1" in mismatched_ids
    mx1 = next(t for t in result["mismatched"] if t["trade_id"] == "MX-1")
    qty = next(m for m in mx1["mismatches"] if m["field"] == "quantity")
    assert qty["stored"] == "10"
    assert qty["venue"] == "99"
