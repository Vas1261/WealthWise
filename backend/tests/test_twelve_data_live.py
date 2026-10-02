"""Opt-in live check against the real Twelve Data API (spec 4.5: demonstrate a real price).

Never part of the default run. Enable explicitly, from backend/, with the key only in backend/.env:

    RUN_LIVE_TWELVE_DATA=1 TWELVE_DATA_LIVE_SYMBOL=<NSE ticker> [TWELVE_DATA_LIVE_ISIN=<ISIN>] \\
        .venv/bin/pytest -m live_twelve_data -s tests/test_twelve_data_live.py

Uses about five API credits. Prints public market data only; the key is never printed.
"""

import os
from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.database import SessionLocal
from app.domain.market import PriceStatus, SymbolStatus, choose_candidate
from app.integrations.twelve_data import TwelveDataClient, TwelveDataError
from app.services import market_service
from app.services.market_service import InstrumentRef

SYMBOL = os.environ.get("TWELVE_DATA_LIVE_SYMBOL", "")
ISIN = os.environ.get("TWELVE_DATA_LIVE_ISIN", "")

pytestmark = [
    pytest.mark.live_twelve_data,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_TWELVE_DATA") != "1"
        or not settings.twelve_data_api_key.get_secret_value()
        or not SYMBOL,
        reason="live Twelve Data test is opt-in (RUN_LIVE_TWELVE_DATA=1, key in .env, TWELVE_DATA_LIVE_SYMBOL)",
    ),
]


def test_live_symbol_search_and_price():
    client = TwelveDataClient(settings.twelve_data_api_key)
    try:
        candidates = client.symbol_search(SYMBOL)
        print(f"\n/symbol_search?symbol={SYMBOL}: {len(candidates)} candidates")
        for c in candidates:
            if c.country == "India":
                print(f"  {c.symbol} | {c.exchange} ({c.mic_code}) | {c.currency} | {c.instrument_type}"
                      f" | {c.instrument_name}")
        choice = choose_candidate(candidates, ticker=SYMBOL)
        print(f"selected: {choice.status.value} {choice.candidate}")
        assert choice.status == SymbolStatus.RESOLVED
        price = client.price(choice.candidate.symbol, choice.candidate.mic_code)
        print(f"/price?symbol={choice.candidate.symbol}&mic_code={choice.candidate.mic_code}: {price} "
              f"{choice.candidate.currency} (fetched {datetime.now(UTC).isoformat(timespec='seconds')})")
        assert price > 0
    except TwelveDataError as error:
        pytest.fail(f"Twelve Data live call failed: {error.issue.value} ({error})")
    finally:
        client.close()


@pytest.mark.usefixtures("migrated_db")
def test_live_market_service_resolves_prices_and_caches():
    ref = InstrumentRef(ISIN or None, SYMBOL, "equity")
    if not ref.isin:
        pytest.skip("set TWELVE_DATA_LIVE_ISIN to exercise ISIN resolution through the service")
    with SessionLocal() as db:
        (first,) = market_service.get_prices(db, [ref])
        (second,) = market_service.get_prices(db, [ref])
        mapping = market_service.market_repository.get_symbol_mapping(db, ref.isin)
    print(f"\nISIN mapping: {mapping}")
    print(f"first call:  {first}")
    print(f"second call: {second}")
    assert first.status == PriceStatus.FRESH, f"{first.status.value}: {first.issue}"
    assert (second.status, second.price, second.as_of) == (PriceStatus.FRESH, first.price, first.as_of)  # from cache
