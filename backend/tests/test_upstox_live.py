"""Opt-in live check against the real Upstox API (spec 4.5: demonstrate a real price).

Never part of the default run. Enable explicitly, from backend/, with the token only in backend/.env:

    RUN_LIVE_UPSTOX=1 [UPSTOX_LIVE_ISIN=<ISIN>] .venv/bin/pytest -m live_upstox -s tests/test_upstox_live.py

Without UPSTOX_LIVE_ISIN it asks for Reliance Industries (INE002A01018, listed on NSE and BSE).
Prints public market data only; the token is never printed.
"""

import os
from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.database import SessionLocal
from app.domain.market import PriceStatus, choose_quote
from app.integrations.upstox import UpstoxClient, UpstoxError
from app.services import market_service
from app.services.market_service import InstrumentRef

ISIN = os.environ.get("UPSTOX_LIVE_ISIN", "INE002A01018")

pytestmark = [
    pytest.mark.live_upstox,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_UPSTOX") != "1" or not settings.upstox_analytics_token.get_secret_value(),
        reason="live Upstox test is opt-in (RUN_LIVE_UPSTOX=1 and UPSTOX_ANALYTICS_TOKEN in .env)",
    ),
]


def test_live_last_price_on_nse_and_bse():
    client = UpstoxClient(settings.upstox_analytics_token)
    try:
        quotes = client.last_prices([ISIN])
    except UpstoxError as error:
        pytest.fail(f"Upstox live call failed: {error.issue.value} ({error})")
    finally:
        client.close()
    fetched = datetime.now(UTC).isoformat(timespec="seconds")
    for exchange, quote in quotes.get(ISIN, {}).items():
        print(f"\n{ISIN} on {exchange} ({quote.trading_symbol}): INR {quote.price} (fetched {fetched})")
    chosen = choose_quote(quotes.get(ISIN, {}))
    assert chosen is not None, f"no NSE or BSE price for {ISIN}"
    assert chosen.price > 0


@pytest.mark.usefixtures("migrated_db")
def test_live_market_service_prices_and_caches():
    ref = InstrumentRef(ISIN, "equity")
    with SessionLocal() as db:
        (first,) = market_service.get_prices(db, [ref])
        (second,) = market_service.get_prices(db, [ref])
    print(f"\nfirst call:  {first}")
    print(f"second call: {second}")
    assert first.status == PriceStatus.FRESH, f"{first.status.value}: {first.issue}"
    assert (second.status, second.price, second.as_of) == (PriceStatus.FRESH, first.price, first.as_of)  # from cache
