"""Opt-in live net-worth demonstration with a non-INR reporting currency (Module 9, decision P14).

Never part of the default run. Enable explicitly, from backend/:

    RUN_LIVE_FRANKFURTER=1 [RUN_LIVE_TWELVE_DATA=1 TWELVE_DATA_LIVE_ISIN=<ISIN> TWELVE_DATA_LIVE_SYMBOL=<ticker>] \\
        .venv/bin/pytest -m live_frankfurter -s tests/test_finance_live.py

Frankfurter is always real here. With the Twelve Data variables set, the synthetic holding (10 units at an
average of INR 1000, using that public ISIN) is priced live too; otherwise it stays unpriced and the
response shows it as excluded (complete=false). All amounts are synthetic; the key is never printed.
"""

import json
import os
from datetime import date
from decimal import Decimal

import pytest

from app.core.database import SessionLocal
from app.repositories import user_repository
from tests.portfolio_support import nh, seed
from tests.test_auth import csrf_headers, logged_in_client

ISIN = os.environ.get("TWELVE_DATA_LIVE_ISIN", "") if os.environ.get("RUN_LIVE_TWELVE_DATA") == "1" else ""
SYMBOL = os.environ.get("TWELVE_DATA_LIVE_SYMBOL", "") if ISIN else ""

pytestmark = [
    pytest.mark.live_frankfurter,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_FRANKFURTER") != "1",
                       reason="live net-worth demo is opt-in (RUN_LIVE_FRANKFURTER=1)"),
]


@pytest.mark.usefixtures("migrated_db")
def test_live_net_worth_in_eur():
    client = logged_in_client("live-networth@example.com")
    headers = csrf_headers(client)

    # Cash recorded while the reporting currency is INR stays INR for good.
    for kind, amount in (("income", "50000"), ("expense", "12500.75")):
        response = client.post("/api/finance/entries", headers=headers, json={
            "kind": kind, "category": "Synthetic", "amount": amount, "date": "2026-10-01"})
        assert response.status_code == 201
    response = client.patch("/api/auth/me", json={"reporting_currency": "EUR"}, headers=headers)
    print(f"\nPATCH /api/auth/me EUR -> {response.status_code}")
    assert response.status_code == 200
    response = client.post("/api/finance/entries", headers=headers, json={
        "kind": "income", "category": "Synthetic", "amount": "100", "date": "2026-10-02"})
    assert response.status_code == 201 and response.json()["currency"] == "EUR"

    with SessionLocal() as db:
        user = user_repository.get_by_email(db, "live-networth@example.com")
        seed(db, user.id, "zerodha", "equity", date(2026, 7, 31),
             [nh("SYNTHETIC HOLDING", ISIN or "INE000D01019", SYMBOL or "SYNTH", qty="10", avg="1000")])

    response = client.get("/api/finance/net-worth")
    body = response.json()
    print(f"GET /api/finance/net-worth -> {response.status_code}")
    print(json.dumps(body, indent=2))
    assert response.status_code == 200 and body["reporting_currency"] == "EUR"
    rates = {r["currency"]: r for r in body["fx_rates"]}
    assert rates["INR"]["status"] == "fresh" and rates["EUR"]["status"] == "identity"
    inr_eur = Decimal(rates["INR"]["rate"])
    expected_cash = Decimal("37499.25") * inr_eur + Decimal("100")
    assert abs(Decimal(body["cash_position"]) - expected_cash) <= Decimal("0.01") + Decimal("37499.25") * Decimal("5e-7")
    # Each figure is rounded from the exact total, so the rounded parts may differ from the sum by a cent.
    assert abs(Decimal(body["net_worth"]) - Decimal(body["portfolio_value"]) - Decimal(body["cash_position"])) <= (
        Decimal("0.01"))
    if ISIN:
        assert body["complete"] is True and Decimal(body["portfolio_value"]) > 0
    else:
        assert (body["complete"], body["unpriced_holdings"], body["portfolio_value"]) == (False, 1, "0.00")
