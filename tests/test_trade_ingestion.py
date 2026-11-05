"""Comprehensive tests for trade ingestion validation via POST /trades/."""

from tests.conftest import make_trade


class TestValidTradeIngestion:
    """Tests for successfully ingesting valid trades."""

    def test_ingest_valid_buy_trade(self, client):
        """Valid BUY trade returns 201 with correct fields."""
        payload = make_trade()
        response = client.post("/trades/", json=payload)

        assert response.status_code == 201
        body = response.json()
        assert body["venue"] == "binance"
        assert body["trade_id"] == "T-1001"
        assert body["symbol"] == "BTC-USD"
        assert body["side"] == "BUY"
        assert body["quantity"] == "10"
        assert body["price"] == "100"
        assert body["timestamp"] == "2026-11-03T10:00:00Z"

    def test_ingest_valid_sell_trade(self, client):
        """Ingesting a BUY then a SELL trade both succeed."""
        buy_payload = make_trade(trade_id="T-2001", side="BUY")
        sell_payload = make_trade(trade_id="T-2002", side="SELL")

        buy_response = client.post("/trades/", json=buy_payload)
        assert buy_response.status_code == 201
        assert buy_response.json()["side"] == "BUY"

        sell_response = client.post("/trades/", json=sell_payload)
        assert sell_response.status_code == 201
        assert sell_response.json()["side"] == "SELL"


class TestMissingRequiredFields:
    """Tests that missing required fields are rejected with 422."""

    def test_reject_missing_venue(self, client):
        """Missing venue field returns 422."""
        payload = make_trade()
        del payload["venue"]
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422

    def test_reject_missing_trade_id(self, client):
        """Missing trade_id field returns 422."""
        payload = make_trade()
        del payload["trade_id"]
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422

    def test_reject_missing_symbol(self, client):
        """Missing symbol field returns 422."""
        payload = make_trade()
        del payload["symbol"]
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422

    def test_reject_missing_side(self, client):
        """Missing side field returns 422."""
        payload = make_trade()
        del payload["side"]
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422


class TestInvalidFieldValues:
    """Tests that invalid field values are rejected with 422."""

    def test_reject_invalid_side(self, client):
        """side='HOLD' is not a valid side and returns 422."""
        payload = make_trade(side="HOLD")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body

    def test_reject_zero_quantity(self, client):
        """quantity='0' is not valid and returns 422."""
        payload = make_trade(quantity="0")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body

    def test_reject_negative_quantity(self, client):
        """Negative quantity returns 422."""
        payload = make_trade(quantity="-5")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body

    def test_reject_zero_price(self, client):
        """price='0' is not valid and returns 422."""
        payload = make_trade(price="0")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body

    def test_reject_negative_price(self, client):
        """Negative price returns 422."""
        payload = make_trade(price="-100")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body

    def test_reject_non_numeric_quantity(self, client):
        """Non-numeric quantity='abc' returns 422."""
        payload = make_trade(quantity="abc")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body

    def test_reject_non_numeric_price(self, client):
        """Non-numeric price='abc' returns 422."""
        payload = make_trade(price="abc")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body


class TestTimestampValidation:
    """Tests for timestamp parsing and timezone awareness."""

    def test_reject_naive_timestamp(self, client):
        """Timestamp without timezone info returns 422."""
        payload = make_trade(timestamp="2026-11-03T10:00:00")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body

    def test_accept_timezone_aware_timestamp(self, client):
        """Timestamp with explicit +05:30 offset is accepted."""
        payload = make_trade(timestamp="2026-11-03T10:00:00+05:30")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 201
        body = response.json()
        assert "timestamp" in body


class TestFieldNormalization:
    """Tests that venue and symbol are normalized on ingestion."""

    def test_venue_normalized_to_lowercase(self, client):
        """Uppercase 'BINANCE' venue is normalized to 'binance'."""
        payload = make_trade(venue="BINANCE", trade_id="T-3001")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 201
        body = response.json()
        assert body["venue"] == "binance"

    def test_symbol_normalized_to_uppercase(self, client):
        """Lowercase 'btc-usd' symbol is normalized to 'BTC-USD'."""
        payload = make_trade(symbol="btc-usd", trade_id="T-3002")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 201
        body = response.json()
        assert body["symbol"] == "BTC-USD"


class TestEmptyFieldRejection:
    """Tests that blank/empty required string fields are rejected."""

    def test_reject_empty_venue(self, client):
        """Whitespace-only venue returns 422."""
        payload = make_trade(venue=" ")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body

    def test_reject_empty_trade_id(self, client):
        """Empty string trade_id returns 422."""
        payload = make_trade(trade_id="")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body

    def test_reject_empty_symbol(self, client):
        """Empty string symbol returns 422."""
        payload = make_trade(symbol="")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body


class TestEdgeCases:
    """Tests for edge-case quantities, prices, and cross-venue semantics."""

    def test_multiple_venues_same_trade_id(self, client):
        """Same trade_id on different venues should be treated as separate trades."""
        binance_payload = make_trade(venue="binance", trade_id="T-1")
        coinbase_payload = make_trade(venue="coinbase", trade_id="T-1")

        resp_binance = client.post("/trades/", json=binance_payload)
        assert resp_binance.status_code == 201
        body_binance = resp_binance.json()
        assert body_binance["venue"] == "binance"
        assert body_binance["trade_id"] == "T-1"

        resp_coinbase = client.post("/trades/", json=coinbase_payload)
        assert resp_coinbase.status_code == 201
        body_coinbase = resp_coinbase.json()
        assert body_coinbase["venue"] == "coinbase"
        assert body_coinbase["trade_id"] == "T-1"

    def test_fractional_quantity_accepted(self, client):
        """Fractional quantity='0.001' is accepted."""
        payload = make_trade(quantity="0.001", trade_id="T-4001")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 201
        body = response.json()
        assert body["quantity"] == "0.001"

    def test_large_quantity_accepted(self, client):
        """Very large quantity='999999999.99' is accepted."""
        payload = make_trade(quantity="999999999.99", trade_id="T-4002")
        response = client.post("/trades/", json=payload)

        assert response.status_code == 201
        body = response.json()
        assert body["quantity"] == "999999999.99"
