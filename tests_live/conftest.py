import json
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import httpx
import pytest


class API:
    def __init__(self):
        self.url = os.environ["BASE_URL"]
        token = uuid4().hex[:16]
        self.venue, self.symbol = "audit_" + token, "AUDIT-" + token.upper()
        self.client = httpx.Client(base_url=self.url, timeout=20, trust_env=False)

    def payload(self, **changes):
        return {
            "venue": self.venue,
            "trade_id": "T1",
            "symbol": self.symbol,
            "side": "BUY",
            "quantity": "10",
            "price": "100",
            "timestamp": "2026-11-03T10:00:00Z",
            **changes,
        }

    def post(self, **changes):
        return self.client.post("/trades/", json=self.payload(**changes))

    def buy(self, **changes):
        r = self.post(**changes)
        assert r.status_code == 201, r.text
        return r.json()

    def position(self):
        r = self.client.get("/positions/" + self.symbol)
        assert r.status_code == 200, r.text
        return r.json()

    def pnl(self):
        r = self.client.get("/pnl/", params={"symbol": self.symbol})
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        return r.json()[0]

    def mark(self, price="125"):
        r = self.client.post("/pnl/set-price", json={"symbol": self.symbol, "price": price})
        assert r.status_code == 204, r.text

    def statement(self, **changes):
        row = self.payload(**changes)
        row.pop("venue")
        return row

    def reconcile(self, rows):
        return self.client.post("/reconciliation/", json={"venue": self.venue, "trades": rows})

    def ledger(self):
        r = self.reconcile([])
        assert r.status_code == 200, r.text
        return r.json()["extra"]

    def parallel(self, payloads):
        gate = Barrier(len(payloads))

        def send(payload):
            with httpx.Client(base_url=self.url, timeout=20, trust_env=False) as c:
                gate.wait(timeout=20)
                return c.post("/trades/", json=payload)

        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            responses = list(pool.map(send, payloads))
        if os.environ.get("EVIDENCE_PATH"):
            ledger = self.ledger()
            net = sum(
                (Decimal(r["quantity"]) * (1 if r["side"] == "BUY" else -1) for r in ledger),
                Decimal(0),
            )
            position = self.position()
            data = {
                "symbol": self.symbol,
                "request_count": len(payloads),
                "request_side": payloads[0]["side"],
                "same_key": len({(p["venue"], p["trade_id"]) for p in payloads}) == 1,
                "status_codes": [r.status_code for r in responses],
                "ledger_count": len(ledger),
                "ledger_net_quantity": str(net),
                "position_net_quantity": position["net_quantity"],
            }
            with Path(os.environ["EVIDENCE_PATH"]).open("a") as output:
                output.write(json.dumps(data) + "\n")
        return responses

    def balance_equals_ledger(self):
        rows = self.ledger()
        net = sum(
            (Decimal(r["quantity"]) * (1 if r["side"] == "BUY" else -1) for r in rows),
            Decimal(0),
        )
        observed = Decimal(self.position()["net_quantity"])
        assert observed == net, f"position={observed}, ledger_net={net}, rows={rows}"
        assert net >= 0, f"committed SELLs exceeded inventory: ledger_net={net}"


@pytest.fixture
def api():
    obj = API()
    assert obj.client.get("/health").status_code == 200
    yield obj
    obj.client.close()
