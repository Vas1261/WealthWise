"""Market Service: real database + real client over a deterministic fake Upstox (no network)."""

import inspect
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx2
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.database import SessionLocal
from app.domain.market import PriceIssue, PriceStatus
from app.integrations.upstox import UpstoxClient
from app.models import MarketPrice
from app.repositories import market_repository
from app.services import market_service
from app.services.market_service import InstrumentRef

pytestmark = pytest.mark.usefixtures("migrated_db")

FAKE_TOKEN = "unit-test-token-not-a-real-secret-0002"
T0 = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)
ISIN_A, ISIN_B, ISIN_C = "INE000D01019", "INE000E01025", "INE000F01020"
EQUITY_A = InstrumentRef(ISIN_A, "equity")
EQUITY_B = InstrumentRef(ISIN_B, "equity")
EQUITY_C = InstrumentRef(ISIN_C, "equity")
FUND = InstrumentRef(None, "mutual_fund")


def error(code, message="error"):
    return httpx2.Response(code, json={"status": "error", "errors": [{"errorCode": "UDAPI", "message": message}]})


class FakeUpstox:
    """quotes: "SEGMENT|ISIN" -> (symbol, price) for listings that exist; failure: a Response or Exception
    returned for every request instead (or None)."""

    def __init__(self):
        self.quotes = {
            f"NSE_EQ|{ISIN_A}": ("SYNTHA", 101.25), f"BSE_EQ|{ISIN_A}": ("SYNTHA", 101.1),
            f"BSE_EQ|{ISIN_B}": ("SYNTHB", 55.5),  # B trades on BSE only
        }
        self.failure: httpx2.Response | Exception | None = None
        self.calls: list[list[str]] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        keys = request.url.params["instrument_key"].split(",")
        self.calls.append(keys)
        if isinstance(self.failure, Exception):
            raise self.failure
        if self.failure is not None:
            return self.failure
        data = {}
        for key in keys:
            if key in self.quotes:
                symbol, price = self.quotes[key]
                data[f"{key.split('|')[0]}:{symbol}"] = {"last_price": price, "instrument_token": key,
                                                          "ltq": 1, "volume": 10, "cp": price}
        return httpx2.Response(200, json={"status": "success", "data": data})

    def requested_isins(self) -> list[str]:
        return [key.split("|")[1] for call in self.calls for key in call if key.startswith("NSE_EQ")]


@pytest.fixture
def market():
    return FakeUpstox()


@pytest.fixture
def client(market):
    c = UpstoxClient(SecretStr(FAKE_TOKEN), transport=httpx2.MockTransport(market.handle))
    yield c
    c.close()


def prices(client, instruments, *, at=T0, force=False):
    with SessionLocal() as db:
        return market_service.get_prices(db, instruments, client=client, now=at, force_refresh=force)


# --- Listing choice ----------------------------------------------------------------------------------

def test_nse_price_preferred_and_cached_with_its_exchange(client, market):
    (result,) = prices(client, [EQUITY_A])
    assert (result.status, result.price, result.currency, result.as_of, result.issue) == (
        PriceStatus.FRESH, Decimal("101.25"), "INR", T0, None)
    assert market.calls == [[f"NSE_EQ|{ISIN_A}", f"BSE_EQ|{ISIN_A}"]]  # both exchanges, one request
    with SessionLocal() as db:
        cached = market_repository.get_cached_prices(db, [ISIN_A])[ISIN_A]
    assert (cached.exchange, cached.trading_symbol, cached.price) == ("NSE", "SYNTHA", Decimal("101.25"))


def test_bse_price_used_when_not_listed_on_nse(client, market):
    (result,) = prices(client, [EQUITY_B])
    assert (result.status, result.price) == (PriceStatus.FRESH, Decimal("55.5"))
    with SessionLocal() as db:
        assert market_repository.get_cached_prices(db, [ISIN_B])[ISIN_B].exchange == "BSE"


def test_isin_on_neither_exchange_is_not_listed(client, market):
    (result,) = prices(client, [EQUITY_C])
    assert (result.status, result.price, result.issue) == (PriceStatus.UNAVAILABLE, None, PriceIssue.NOT_LISTED)
    with SessionLocal() as db:
        assert market_repository.get_cached_prices(db, [ISIN_C]) == {}


def test_a_whole_portfolio_is_priced_in_one_request(client, market):
    results = prices(client, [EQUITY_B, FUND, EQUITY_A, EQUITY_C, EQUITY_A])
    assert len(market.calls) == 1
    assert market.requested_isins() == [ISIN_B, ISIN_A, ISIN_C]  # each ISIN once, in first-seen order
    assert [r.price for r in results] == [Decimal("55.5"), None, Decimal("101.25"), None, Decimal("101.25")]


# --- Price cache -------------------------------------------------------------------------------------

def test_fresh_cache_hit_makes_no_request(client, market):
    first = prices(client, [EQUITY_A])[0]
    second = prices(client, [EQUITY_A], at=T0 + timedelta(minutes=9, seconds=59))[0]
    assert (second.status, second.price, second.as_of) == (PriceStatus.FRESH, first.price, T0)
    assert len(market.calls) == 1


def test_only_isins_without_a_fresh_price_are_requested(client, market):
    prices(client, [EQUITY_A])
    prices(client, [EQUITY_A, EQUITY_B], at=T0 + timedelta(minutes=1))
    assert market.requested_isins() == [ISIN_A, ISIN_B]  # A once, then only B


def test_expired_price_is_refreshed(client, market):
    prices(client, [EQUITY_A])
    market.quotes[f"NSE_EQ|{ISIN_A}"] = ("SYNTHA", 102)
    later = T0 + timedelta(minutes=10)
    (result,) = prices(client, [EQUITY_A], at=later)
    assert (result.status, result.price, result.as_of) == (PriceStatus.FRESH, Decimal("102"), later)


def test_ttl_comes_from_configuration(client, market, monkeypatch):
    monkeypatch.setattr(settings, "market_price_ttl_minutes", 5)
    prices(client, [EQUITY_A])
    prices(client, [EQUITY_A], at=T0 + timedelta(minutes=5))
    assert len(market.calls) == 2


def test_forced_refresh_respects_sixty_second_floor(client, market):
    prices(client, [EQUITY_A])
    prices(client, [EQUITY_A], at=T0 + timedelta(seconds=30), force=True)
    assert len(market.calls) == 1
    market.quotes[f"NSE_EQ|{ISIN_A}"] = ("SYNTHA", 103)
    (result,) = prices(client, [EQUITY_A], at=T0 + timedelta(seconds=60), force=True)
    assert (result.status, result.price, len(market.calls)) == (PriceStatus.FRESH, Decimal("103"), 2)


@pytest.mark.parametrize(
    ("failure", "issue"),
    [
        (httpx2.ReadTimeout("timed out"), PriceIssue.SERVICE_UNAVAILABLE),
        (httpx2.ConnectError("refused"), PriceIssue.SERVICE_UNAVAILABLE),
        (error(500), PriceIssue.SERVICE_UNAVAILABLE),
        (error(429), PriceIssue.RATE_LIMITED),
        (error(401), PriceIssue.CREDENTIALS_REJECTED),
        (error(403), PriceIssue.NOT_ENTITLED),
        (error(400), PriceIssue.REQUEST_REJECTED),
        (httpx2.Response(200, text="not json"), PriceIssue.INVALID_RESPONSE),
        (httpx2.Response(200, json={"status": "success"}), PriceIssue.INVALID_RESPONSE),
    ],
    ids=["timeout", "connection", "http-500", "rate-limit", "bad-token", "no-access", "bad-request", "not-json",
         "no-data"],
)
def test_failed_refresh_serves_last_price_as_stale_or_unavailable(client, market, failure, issue):
    prices(client, [EQUITY_A])
    market.failure = failure
    later = T0 + timedelta(minutes=15)
    stale, missing = prices(client, [EQUITY_A, EQUITY_B], at=later)
    assert (stale.status, stale.price, stale.as_of, stale.issue) == (PriceStatus.STALE, Decimal("101.25"), T0, issue)
    assert (missing.status, missing.price, missing.as_of, missing.issue) == (PriceStatus.UNAVAILABLE, None, None, issue)


def test_listing_that_disappears_keeps_its_last_price_as_stale(client, market):
    prices(client, [EQUITY_A])
    del market.quotes[f"NSE_EQ|{ISIN_A}"], market.quotes[f"BSE_EQ|{ISIN_A}"]
    (result,) = prices(client, [EQUITY_A], at=T0 + timedelta(minutes=20))
    assert (result.status, result.price, result.issue) == (PriceStatus.STALE, Decimal("101.25"), PriceIssue.NOT_LISTED)


def test_stale_price_is_served_regardless_of_age(client, market):
    prices(client, [EQUITY_A])
    market.failure = error(500)
    (result,) = prices(client, [EQUITY_A], at=T0 + timedelta(days=30))
    assert (result.status, result.as_of) == (PriceStatus.STALE, T0)


def test_failed_refresh_does_not_overwrite_cache(client, market):
    prices(client, [EQUITY_A])
    market.failure = error(500)
    prices(client, [EQUITY_A], at=T0 + timedelta(minutes=20))
    with SessionLocal() as db:
        cached = market_repository.get_cached_prices(db, [ISIN_A])[ISIN_A]
    assert (cached.price, cached.fetched_at) == (Decimal("101.25"), T0)


# --- Scope: mutual funds, missing ISIN, configuration, sharing ----------------------------------------

def test_mutual_funds_are_not_supported_and_never_requested(client, market):
    results = prices(client, [FUND, InstrumentRef("INF000X01011", "mutual_fund")])
    assert all((r.status, r.issue) == (PriceStatus.NOT_SUPPORTED, PriceIssue.MUTUAL_FUND) for r in results)
    assert market.calls == []


def test_equity_without_isin_is_unavailable(client, market):
    (result,) = prices(client, [InstrumentRef(None, "equity")])
    assert (result.status, result.issue) == (PriceStatus.UNAVAILABLE, PriceIssue.MISSING_ISIN)
    assert market.calls == []


def test_cache_is_global_market_data_not_per_user(client, market):
    # Two users holding the same ISIN share one cached price; the service takes no user identity at all.
    assert "user" not in " ".join(inspect.signature(market_service.get_prices).parameters)
    prices(client, [EQUITY_A])
    prices(client, [EQUITY_A], at=T0 + timedelta(minutes=1))
    assert len(market.calls) == 1


def test_without_token_nothing_is_requested_and_cache_still_served(market, monkeypatch, client):
    prices(client, [EQUITY_A])  # cache a price first
    monkeypatch.setattr(settings, "upstox_analytics_token", SecretStr(""))
    assert market_service.default_client() is None
    with SessionLocal() as db:
        fresh, unknown = market_service.get_prices(db, [EQUITY_A, EQUITY_B], now=T0 + timedelta(minutes=1))
        stale = market_service.get_prices(db, [EQUITY_A], now=T0 + timedelta(minutes=20))[0]
    assert fresh.status == PriceStatus.FRESH
    assert (unknown.status, unknown.issue) == (PriceStatus.UNAVAILABLE, PriceIssue.NOT_CONFIGURED)
    assert (stale.status, stale.issue) == (PriceStatus.STALE, PriceIssue.NOT_CONFIGURED)
    assert len(market.calls) == 1  # only the first, configured run made a request


def test_configured_token_is_used_by_default(market, monkeypatch):
    monkeypatch.setattr(settings, "upstox_analytics_token", SecretStr(FAKE_TOKEN))
    real_client = UpstoxClient
    monkeypatch.setattr(market_service, "UpstoxClient",
                        lambda token: real_client(token, transport=httpx2.MockTransport(market.handle)))
    with SessionLocal() as db:
        (result,) = market_service.get_prices(db, [EQUITY_A], now=T0)
    assert result.status == PriceStatus.FRESH and len(market.calls) == 1


# --- Secret hygiene ----------------------------------------------------------------------------------

def test_failures_log_issue_codes_only_never_the_token(client, market, caplog):
    caplog.set_level(logging.DEBUG)
    market.failure = error(401, f"bad token {FAKE_TOKEN}")
    prices(client, [EQUITY_A])
    assert "credentials_rejected" in caplog.text
    assert FAKE_TOKEN not in caplog.text
    assert "bad token" not in caplog.text


def test_price_rows_hold_no_user_data(client, market):
    prices(client, [EQUITY_A])
    with SessionLocal() as db:
        row = db.query(MarketPrice).one()
    assert set(MarketPrice.__table__.columns.keys()) == {
        "id", "isin", "exchange", "trading_symbol", "price", "currency", "fetched_at"}
    assert row.isin == ISIN_A
