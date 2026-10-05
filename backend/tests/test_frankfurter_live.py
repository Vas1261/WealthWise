"""Opt-in live check against the real Frankfurter API (spec 4.5 / 5 step 8: non-INR reporting currency).

Never part of the default run. Enable explicitly, from backend/ (no API key exists or is needed):

    RUN_LIVE_FRANKFURTER=1 .venv/bin/pytest -m live_frankfurter -s tests/test_frankfurter_live.py

Prints public FX data only. Market prices stay offline (the Twelve Data key is blank in tests), so the
converted totals shown are cost totals; the FX path is the same for current value.
"""

import os
from datetime import date
from decimal import Decimal

import pytest

from app.core.database import SessionLocal
from app.domain.fx import FxStatus
from app.integrations.frankfurter import FrankfurterClient
from app.repositories import user_repository
from app.services import fx_service
from tests.portfolio_support import nh, seed
from tests.test_auth import csrf_headers, logged_in_client

pytestmark = [
    pytest.mark.live_frankfurter,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_FRANKFURTER") != "1",
                       reason="live Frankfurter test is opt-in (RUN_LIVE_FRANKFURTER=1)"),
]


def test_live_rate_and_currency_list():
    client = FrankfurterClient()
    try:
        quote = client.rate("USD", "INR")
        print(f"\nGET /v2/rate/USD/INR -> base={quote.base} quote={quote.quote} rate={quote.rate} "
              f"date={quote.rate_date}")
        codes = client.currencies()
        present = {"INR", "EUR", "USD"} <= codes
        print(f"GET /v2/currencies -> {len(codes)} codes; INR/EUR/USD present: {present}")
    finally:
        client.close()
    assert type(quote.rate) is Decimal and quote.rate > 0
    assert {"INR", "EUR", "USD"} <= codes


@pytest.mark.usefixtures("migrated_db")
def test_live_end_to_end_non_inr_reporting_currency():
    client = logged_in_client("live-fx@example.com")
    response = client.patch("/api/auth/me", json={"reporting_currency": "EUR"}, headers=csrf_headers(client))
    print(f"\nPATCH /api/auth/me EUR -> {response.status_code} {response.json()}")
    assert response.status_code == 200 and response.json()["reporting_currency"] == "EUR"
    with SessionLocal() as db:
        user = user_repository.get_by_email(db, "live-fx@example.com")
        seed(db, user.id, "zerodha", "equity", date(2026, 7, 31), [nh(qty="10", avg="1000")])  # synthetic INR 10,000
    body = client.get("/api/portfolio").json()
    reporting = body["reporting_totals"]
    print(f"native INR totals: {body['totals_by_currency']['INR']['invested_value']} invested")
    print(f"reporting totals: {reporting['currency']} {reporting['invested_value']} invested; "
          f"fx_rates={reporting['fx_rates']}; fx_stale={reporting['fx_stale']}; "
          f"fx_unavailable={body['fx_unavailable']}")
    rate = Decimal(reporting["fx_rates"][0]["rate"])
    assert reporting["currency"] == "EUR" and reporting["fx_rates"][0]["status"] == FxStatus.FRESH.value
    assert Decimal(reporting["invested_value"]) == (Decimal("10000") * rate).quantize(Decimal("0.01"))
    assert fx_service.get_rate("INR", "EUR").status == FxStatus.FRESH  # served from the cache now
