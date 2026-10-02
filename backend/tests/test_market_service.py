"""Market Service: real database + real client over a deterministic fake Twelve Data (no network)."""

import inspect
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx2
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.database import SessionLocal
from app.domain.market import PriceIssue, PriceStatus, RequestBudget, SymbolStatus
from app.integrations.twelve_data import TwelveDataClient
from app.models import MarketPrice
from app.repositories import market_repository
from app.services import market_service
from app.services.market_service import InstrumentRef

pytestmark = pytest.mark.usefixtures("migrated_db")

FAKE_KEY = "unit-test-key-not-a-real-secret-0002"
T0 = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)
ISIN_A, ISIN_B = "INE000D01019", "INE000E01025"
GROWW_A = InstrumentRef(ISIN_A, None, "equity")
ZERODHA_A = InstrumentRef(ISIN_A, "SYNTHA", "equity")
GROWW_B = InstrumentRef(ISIN_B, None, "equity")
FUND = InstrumentRef(None, None, "mutual_fund")


def listing(symbol, mic="XNSE"):
    return {"symbol": symbol, "instrument_name": f"{symbol} Ltd", "exchange": {"XNSE": "NSE", "XBOM": "BSE"}[mic],
            "mic_code": mic, "exchange_timezone": "Asia/Kolkata", "instrument_type": "Common Stock",
            "country": "India", "currency": "INR"}


def error(code, message="error"):
    return httpx2.Response(code, json={"code": code, "message": message, "status": "error"})


class FakeTwelveData:
    """search: query -> list of listings | Response; prices: (symbol, mic) -> price str | Response | Exception."""

    def __init__(self):
        self.search = {ISIN_A: [listing("SYNTHA"), listing("SYNTHA", "XBOM")], ISIN_B: [listing("SYNTHB")]}
        self.prices = {("SYNTHA", "XNSE"): "101.25000", ("SYNTHB", "XNSE"): "55.50000"}
        self.calls: list[tuple[str, dict]] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        params = dict(request.url.params)
        self.calls.append((request.url.path, params))
        if request.url.path == "/symbol_search":
            outcome = self.search.get(params["symbol"], [])
            return outcome if isinstance(outcome, httpx2.Response) else httpx2.Response(200, json={"data": outcome})
        outcome = self.prices.get((params["symbol"], params["mic_code"]))
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, httpx2.Response):
            return outcome
        return httpx2.Response(200, json={"price": outcome}) if outcome else error(404, "symbol not found")

    def count(self, path):
        return sum(1 for p, _ in self.calls if p == path)


@pytest.fixture
def market():
    return FakeTwelveData()


@pytest.fixture
def client(market):
    c = TwelveDataClient(SecretStr(FAKE_KEY), transport=httpx2.MockTransport(market.handle))
    yield c
    c.close()


def prices(client, instruments, *, at=T0, force=False, budget=None):
    with SessionLocal() as db:
        return market_service.get_prices(db, instruments, client=client, now=at, force_refresh=force,
                                         budget=budget or RequestBudget(1000))


# --- Symbol resolution -------------------------------------------------------------------------

def test_isin_resolved_to_nse_listing_and_cached(client, market):
    (result,) = prices(client, [GROWW_A])
    assert (result.status, result.price, result.currency, result.issue) == (
        PriceStatus.FRESH, Decimal("101.25000"), "INR", None)
    with SessionLocal() as db:
        mapping = market_repository.get_symbol_mapping(db, ISIN_A)
    assert (mapping.status, mapping.symbol, mapping.mic_code) == (SymbolStatus.RESOLVED, "SYNTHA", "XNSE")
    assert market.calls[0] == ("/symbol_search", {"symbol": ISIN_A, "show_plan": "true"})


def test_cached_symbol_is_not_searched_again(client, market):
    prices(client, [GROWW_A])
    prices(client, [GROWW_A], at=T0 + timedelta(hours=1))  # price stale, mapping permanent
    assert market.count("/symbol_search") == 1
    assert market.count("/price") == 2


@pytest.mark.parametrize("not_entitled", [error(403, "upgrade"), error(404, "available starting with the Grow plan")])
def test_isin_search_outside_plan_falls_back_to_broker_ticker(client, market, not_entitled):
    market.search[ISIN_A] = not_entitled
    market.search["SYNTHA"] = [listing("SYNTHAB"), listing("SYNTHA")]
    (result,) = prices(client, [ZERODHA_A])
    assert (result.status, result.price) == (PriceStatus.FRESH, Decimal("101.25000"))
    assert [p["symbol"] for _, p in market.calls if "show_plan" in p] == [ISIN_A, "SYNTHA"]


def test_ticker_fallback_also_used_when_isin_search_finds_nothing(client, market):
    market.search[ISIN_A] = []
    market.search["SYNTHA"] = [listing("SYNTHA")]
    assert prices(client, [ZERODHA_A])[0].status == PriceStatus.FRESH


def test_groww_holding_borrows_ticker_from_zerodha_holding_of_same_isin(client, market):
    market.search[ISIN_A] = error(403, "upgrade")
    market.search["SYNTHA"] = [listing("SYNTHA")]
    results = prices(client, [GROWW_A, ZERODHA_A])
    assert results[0] == results[1] and results[0].status == PriceStatus.FRESH
    assert (market.count("/symbol_search"), market.count("/price")) == (2, 1)  # one ISIN, priced once


def test_isin_search_outside_plan_without_ticker_is_not_cached(client, market):
    market.search[ISIN_A] = error(403, "upgrade")
    (result,) = prices(client, [GROWW_A])
    assert (result.status, result.issue) == (PriceStatus.UNAVAILABLE, PriceIssue.NOT_ENTITLED)
    with SessionLocal() as db:
        assert market_repository.get_symbol_mapping(db, ISIN_A) is None


def test_no_candidate_is_cached_as_not_found_and_retried_after_seven_days(client, market):
    market.search[ISIN_A] = []
    assert prices(client, [GROWW_A])[0].issue == PriceIssue.SYMBOL_NOT_FOUND
    prices(client, [GROWW_A], at=T0 + timedelta(days=6))
    assert market.count("/symbol_search") == 1
    market.search[ISIN_A] = [listing("SYNTHA")]
    assert prices(client, [GROWW_A], at=T0 + timedelta(days=7))[0].status == PriceStatus.FRESH
    assert market.count("/symbol_search") == 2


def test_multiple_candidates_on_preferred_exchange_are_ambiguous(client, market):
    market.search[ISIN_A] = [listing("SYNTHA"), listing("SYNTHZ")]
    (result,) = prices(client, [GROWW_A])
    assert (result.status, result.issue) == (PriceStatus.UNAVAILABLE, PriceIssue.SYMBOL_AMBIGUOUS)
    assert market.count("/price") == 0
    with SessionLocal() as db:
        assert market_repository.get_symbol_mapping(db, ISIN_A).status == SymbolStatus.AMBIGUOUS


@pytest.mark.parametrize(
    ("response", "issue"),
    [(error(429), PriceIssue.RATE_LIMITED), (error(500), PriceIssue.SERVICE_UNAVAILABLE),
     (httpx2.Response(200, text="<html>"), PriceIssue.INVALID_RESPONSE)],
)
def test_transient_search_failure_is_not_cached(client, market, response, issue):
    market.search[ISIN_A] = response
    assert prices(client, [GROWW_A])[0].issue == issue
    with SessionLocal() as db:
        assert market_repository.get_symbol_mapping(db, ISIN_A) is None
    market.search[ISIN_A] = [listing("SYNTHA")]
    assert prices(client, [GROWW_A], at=T0 + timedelta(seconds=5))[0].status == PriceStatus.FRESH


# --- Price cache -------------------------------------------------------------------------------

def test_fresh_cache_hit_makes_no_request(client, market):
    first = prices(client, [GROWW_A])[0]
    second = prices(client, [GROWW_A], at=T0 + timedelta(minutes=9, seconds=59))[0]
    assert second == first and second.as_of == T0
    assert market.count("/price") == 1


def test_stale_cache_is_refreshed(client, market):
    prices(client, [GROWW_A])
    market.prices[("SYNTHA", "XNSE")] = "102.00000"
    later = T0 + timedelta(minutes=10)
    (result,) = prices(client, [GROWW_A], at=later)
    assert (result.status, result.price, result.as_of) == (PriceStatus.FRESH, Decimal("102.00000"), later)


def test_ttl_comes_from_configuration(client, market, monkeypatch):
    monkeypatch.setattr(settings, "market_price_ttl_minutes", 5)
    prices(client, [GROWW_A])
    prices(client, [GROWW_A], at=T0 + timedelta(minutes=5))
    assert market.count("/price") == 2


def test_forced_refresh_respects_sixty_second_floor(client, market):
    prices(client, [GROWW_A])
    prices(client, [GROWW_A], at=T0 + timedelta(seconds=30), force=True)
    assert market.count("/price") == 1
    market.prices[("SYNTHA", "XNSE")] = "103.00000"
    (result,) = prices(client, [GROWW_A], at=T0 + timedelta(seconds=60), force=True)
    assert (result.status, result.price, market.count("/price")) == (PriceStatus.FRESH, Decimal("103.00000"), 2)


@pytest.mark.parametrize(
    ("failure", "issue"),
    [
        (httpx2.ReadTimeout("timed out"), PriceIssue.SERVICE_UNAVAILABLE),
        (httpx2.ConnectError("refused"), PriceIssue.SERVICE_UNAVAILABLE),
        (error(500), PriceIssue.SERVICE_UNAVAILABLE),
        (error(429), PriceIssue.RATE_LIMITED),
        (error(401), PriceIssue.CREDENTIALS_REJECTED),
        (error(403), PriceIssue.NOT_ENTITLED),
        (httpx2.Response(200, text="not json"), PriceIssue.INVALID_RESPONSE),
        (httpx2.Response(200, json={}), PriceIssue.INVALID_RESPONSE),
        (httpx2.Response(200, json={"price": "abc"}), PriceIssue.INVALID_RESPONSE),
        (httpx2.Response(200, json={"price": "0"}), PriceIssue.INVALID_RESPONSE),
    ],
    ids=["timeout", "connection", "http-500", "rate-limit", "bad-key", "plan", "not-json", "no-price",
         "non-numeric", "zero"],
)
def test_failed_refresh_serves_last_price_as_stale_or_unavailable(client, market, failure, issue):
    later = T0 + timedelta(minutes=15)
    prices(client, [GROWW_A])
    market.prices[("SYNTHA", "XNSE")] = failure
    stale = prices(client, [GROWW_A], at=later)[0]
    assert (stale.status, stale.price, stale.as_of, stale.issue) == (PriceStatus.STALE, Decimal("101.25000"), T0, issue)

    # Same failure with no cached price: B's symbol is resolved and cached, but its price is not.
    assert prices(client, [GROWW_B], at=later)[0].status == PriceStatus.FRESH
    with SessionLocal() as db:
        db.query(MarketPrice).filter_by(symbol="SYNTHB").delete()
        db.commit()
    market.prices[("SYNTHB", "XNSE")] = failure
    (missing,) = prices(client, [GROWW_B], at=later + timedelta(minutes=1))
    assert (missing.status, missing.price, missing.as_of, missing.issue) == (PriceStatus.UNAVAILABLE, None, None, issue)


def test_stale_price_is_served_regardless_of_age(client, market):
    prices(client, [GROWW_A])
    market.prices[("SYNTHA", "XNSE")] = error(500)
    (result,) = prices(client, [GROWW_A], at=T0 + timedelta(days=30))
    assert (result.status, result.as_of) == (PriceStatus.STALE, T0)


def test_failed_refresh_does_not_overwrite_cache(client, market):
    prices(client, [GROWW_A])
    market.prices[("SYNTHA", "XNSE")] = error(500)
    prices(client, [GROWW_A], at=T0 + timedelta(minutes=20))
    with SessionLocal() as db:
        cached = market_repository.get_cached_price(db, "SYNTHA", "XNSE")
    assert (cached.price, cached.fetched_at) == (Decimal("101.25000"), T0)


# --- Request budget (rate guard) ---------------------------------------------------------------

def test_request_budget_defers_instead_of_exceeding_the_limit(client, market):
    budget = RequestBudget(3)  # A: search + price; B: search; B's price would be the 4th request
    results = prices(client, [GROWW_A, GROWW_B], budget=budget)
    assert [r.status for r in results] == [PriceStatus.FRESH, PriceStatus.UNAVAILABLE]
    assert results[1].issue == PriceIssue.REQUEST_BUDGET_EXHAUSTED
    assert len(market.calls) == 3
    (later_b,) = prices(client, [GROWW_B], at=T0 + timedelta(minutes=1), budget=budget)
    assert later_b.status == PriceStatus.FRESH


def test_exhausted_budget_still_serves_cached_prices(client, market):
    prices(client, [GROWW_A])
    (result,) = prices(client, [GROWW_A], at=T0 + timedelta(minutes=12), budget=RequestBudget(0 + 1))
    assert result.status == PriceStatus.FRESH  # one request allowed
    exhausted = RequestBudget(1)
    exhausted.try_spend(T0 + timedelta(minutes=30))
    (result,) = prices(client, [GROWW_A], at=T0 + timedelta(minutes=30), budget=exhausted)
    assert (result.status, result.issue) == (PriceStatus.STALE, PriceIssue.REQUEST_BUDGET_EXHAUSTED)


# --- Scope: mutual funds, missing ISIN, configuration, sharing ---------------------------------------

def test_mutual_funds_are_not_supported_and_never_requested(client, market):
    results = prices(client, [FUND, InstrumentRef(None, None, "mutual_fund")])
    assert all((r.status, r.issue) == (PriceStatus.NOT_SUPPORTED, PriceIssue.MUTUAL_FUND) for r in results)
    assert market.calls == []


def test_equity_without_isin_is_unavailable(client, market):
    (result,) = prices(client, [InstrumentRef(None, "SYNTHA", "equity")])
    assert (result.status, result.issue) == (PriceStatus.UNAVAILABLE, PriceIssue.MISSING_ISIN)
    assert market.calls == []


def test_results_follow_input_order_and_each_isin_is_priced_once(client, market):
    results = prices(client, [GROWW_B, FUND, GROWW_A, ZERODHA_A, GROWW_B])
    assert [r.price for r in results] == [Decimal("55.50000"), None, Decimal("101.25000"), Decimal("101.25000"),
                                          Decimal("55.50000")]
    assert market.count("/price") == 2


def test_failed_refresh_is_not_repeated_for_duplicate_isins(client, market):
    prices(client, [GROWW_A])
    market.prices[("SYNTHA", "XNSE")] = error(500)
    results = prices(client, [GROWW_A, ZERODHA_A, GROWW_A], at=T0 + timedelta(minutes=20))
    assert {r.status for r in results} == {PriceStatus.STALE}
    assert market.count("/price") == 2  # the initial fetch + one failed refresh, not three


def test_cache_is_global_market_data_not_per_user(client, market):
    # Two users holding the same ISIN share one cached price; the service takes no user identity at all.
    assert "user" not in " ".join(inspect.signature(market_service.get_prices).parameters)
    prices(client, [GROWW_A])
    prices(client, [ZERODHA_A], at=T0 + timedelta(minutes=1))
    assert market.count("/price") == 1


def test_without_api_key_nothing_is_requested_and_cache_still_served(market, monkeypatch, client):
    prices(client, [GROWW_A])  # cache a mapping and price first
    monkeypatch.setattr(settings, "twelve_data_api_key", SecretStr(""))
    assert market_service.default_client() is None
    with SessionLocal() as db:
        fresh, unknown = market_service.get_prices(db, [GROWW_A, GROWW_B], now=T0 + timedelta(minutes=1))
        stale = market_service.get_prices(db, [GROWW_A], now=T0 + timedelta(minutes=20))[0]
    assert fresh.status == PriceStatus.FRESH
    assert (unknown.status, unknown.issue) == (PriceStatus.UNAVAILABLE, PriceIssue.NOT_CONFIGURED)
    assert (stale.status, stale.issue) == (PriceStatus.STALE, PriceIssue.NOT_CONFIGURED)
    assert len(market.calls) == 2  # only the first, configured run made requests


def test_resolve_isins_for_import_service(client, market):
    market.search[ISIN_B] = []
    with SessionLocal() as db:
        resolved = market_service.resolve_isins(db, [GROWW_A, GROWW_B, FUND], client=client, now=T0,
                                                budget=RequestBudget(10))
    assert set(resolved) == {ISIN_A, ISIN_B}
    assert (resolved[ISIN_A].status, resolved[ISIN_A].symbol) == (SymbolStatus.RESOLVED, "SYNTHA")
    assert resolved[ISIN_B].status == SymbolStatus.NOT_FOUND
    assert market.count("/price") == 0


# --- Secret hygiene ----------------------------------------------------------------------------

def test_failures_log_issue_codes_only_never_the_key(client, market, caplog):
    caplog.set_level(logging.DEBUG)
    prices(client, [GROWW_A])
    market.prices[("SYNTHA", "XNSE")] = error(401, f"bad key {FAKE_KEY}")
    prices(client, [GROWW_A], at=T0 + timedelta(minutes=20))
    assert "credentials_rejected" in caplog.text
    assert FAKE_KEY not in caplog.text
    assert "bad key" not in caplog.text
