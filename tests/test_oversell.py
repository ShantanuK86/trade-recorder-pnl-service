"""Comprehensive oversell protection tests."""

import pytest
from tests.conftest import make_trade


class TestOversellProtection:
    """Tests for oversell protection on POST /trades/."""

    def test_sell_without_position_rejected(self, client):
        """SELL with no prior buys should be rejected with 409."""
        trade = make_trade(side="SELL", quantity="5", trade_id="T-1")
        resp = client.post("/trades/", json=trade)
        assert resp.status_code == 409
        assert "oversell_rejected" in resp.json()["detail"]

    def test_sell_exceeding_position_rejected(self, client):
        """BUY 10, then SELL 15 should be rejected."""
        buy = make_trade(side="BUY", quantity="10", trade_id="T-1")
        assert client.post("/trades/", json=buy).status_code == 201

        sell = make_trade(side="SELL", quantity="15", trade_id="T-2")
        resp = client.post("/trades/", json=sell)
        assert resp.status_code == 409
        assert "oversell_rejected" in resp.json()["detail"]

    def test_sell_exactly_equal_to_position(self, client):
        """BUY 10, SELL 10 should succeed (boundary case)."""
        buy = make_trade(side="BUY", quantity="10", trade_id="T-1")
        assert client.post("/trades/", json=buy).status_code == 201

        sell = make_trade(side="SELL", quantity="10", trade_id="T-2")
        resp = client.post("/trades/", json=sell)
        assert resp.status_code == 201

    def test_position_unchanged_after_rejected_sell(self, client):
        """BUY 10, attempt SELL 15 (rejected), position should still be 10."""
        buy = make_trade(side="BUY", quantity="10", trade_id="T-1")
        assert client.post("/trades/", json=buy).status_code == 201

        sell = make_trade(side="SELL", quantity="15", trade_id="T-2")
        resp = client.post("/trades/", json=sell)
        assert resp.status_code == 409

        pos_resp = client.get("/positions/BTC-USD")
        position = pos_resp.json()
        assert float(position["current_position"]) == 10

    def test_sell_within_position_succeeds(self, client):
        """BUY 10, SELL 3 should succeed."""
        buy = make_trade(side="BUY", quantity="10", trade_id="T-1")
        assert client.post("/trades/", json=buy).status_code == 201

        sell = make_trade(side="SELL", quantity="3", trade_id="T-2")
        resp = client.post("/trades/", json=sell)
        assert resp.status_code == 201

    def test_multiple_sells_exhaust_position(self, client):
        """BUY 10, SELL 5, SELL 5 — both succeed, position=0."""
        buy = make_trade(side="BUY", quantity="10", trade_id="T-1")
        assert client.post("/trades/", json=buy).status_code == 201

        sell1 = make_trade(side="SELL", quantity="5", trade_id="T-2")
        assert client.post("/trades/", json=sell1).status_code == 201

        sell2 = make_trade(side="SELL", quantity="5", trade_id="T-3")
        assert client.post("/trades/", json=sell2).status_code == 201

        pos_resp = client.get("/positions/BTC-USD")
        position = pos_resp.json()
        assert float(position["current_position"]) == 0

    def test_multiple_sells_exceed_position(self, client):
        """BUY 10, SELL 5, SELL 6 — second sell rejected."""
        buy = make_trade(side="BUY", quantity="10", trade_id="T-1")
        assert client.post("/trades/", json=buy).status_code == 201

        sell1 = make_trade(side="SELL", quantity="5", trade_id="T-2")
        assert client.post("/trades/", json=sell1).status_code == 201

        sell2 = make_trade(side="SELL", quantity="6", trade_id="T-3")
        resp = client.post("/trades/", json=sell2)
        assert resp.status_code == 409
        assert "oversell_rejected" in resp.json()["detail"]

    def test_oversell_error_contains_details(self, client):
        """Verify error response has symbol, current_position, attempted_sell."""
        buy = make_trade(side="BUY", quantity="10", trade_id="T-1")
        assert client.post("/trades/", json=buy).status_code == 201

        sell = make_trade(side="SELL", quantity="15", trade_id="T-2")
        resp = client.post("/trades/", json=sell)
        assert resp.status_code == 409
        body = resp.json()
        assert "symbol" in body
        assert "current_position" in body
        assert "attempted_sell" in body

    def test_oversell_different_symbols_independent(self, client):
        """BUY 10 BTC-USD, SELL 15 ETH-USD → 409 (positions are independent)."""
        buy_btc = make_trade(symbol="BTC-USD", side="BUY", quantity="10", trade_id="T-1")
        assert client.post("/trades/", json=buy_btc).status_code == 201

        sell_eth = make_trade(symbol="ETH-USD", side="SELL", quantity="15", trade_id="T-2")
        resp = client.post("/trades/", json=sell_eth)
        assert resp.status_code == 409
        assert "oversell_rejected" in resp.json()["detail"]

    def test_position_zero_then_sell_rejected(self, client):
        """BUY 10, SELL 10 (pos=0), then SELL 1 → 409."""
        buy = make_trade(side="BUY", quantity="10", trade_id="T-1")
        assert client.post("/trades/", json=buy).status_code == 201

        sell1 = make_trade(side="SELL", quantity="10", trade_id="T-2")
        assert client.post("/trades/", json=sell1).status_code == 201

        sell2 = make_trade(side="SELL", quantity="1", trade_id="T-3")
        resp = client.post("/trades/", json=sell2)
        assert resp.status_code == 409
        assert "oversell_rejected" in resp.json()["detail"]

    def test_sell_fractional_oversell(self, client):
        """BUY 1.5, SELL 1.6 → 409."""
        buy = make_trade(side="BUY", quantity="1.5", trade_id="T-1")
        assert client.post("/trades/", json=buy).status_code == 201

        sell = make_trade(side="SELL", quantity="1.6", trade_id="T-2")
        resp = client.post("/trades/", json=sell)
        assert resp.status_code == 409
        assert "oversell_rejected" in resp.json()["detail"]

    def test_sell_fractional_exact(self, client):
        """BUY 1.5, SELL 1.5 → success."""
        buy = make_trade(side="BUY", quantity="1.5", trade_id="T-1")
        assert client.post("/trades/", json=buy).status_code == 201

        sell = make_trade(side="SELL", quantity="1.5", trade_id="T-2")
        resp = client.post("/trades/", json=sell)
        assert resp.status_code == 201

    def test_buy_after_rejected_sell_works(self, client):
        """BUY 5, attempt SELL 10 (rejected), BUY 5, now SELL 10 succeeds."""
        buy1 = make_trade(side="BUY", quantity="5", trade_id="T-1")
        assert client.post("/trades/", json=buy1).status_code == 201

        sell_fail = make_trade(side="SELL", quantity="10", trade_id="T-2")
        resp = client.post("/trades/", json=sell_fail)
        assert resp.status_code == 409

        buy2 = make_trade(side="BUY", quantity="5", trade_id="T-3")
        assert client.post("/trades/", json=buy2).status_code == 201

        sell_ok = make_trade(side="SELL", quantity="10", trade_id="T-4")
        resp = client.post("/trades/", json=sell_ok)
        assert resp.status_code == 201
