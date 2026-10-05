"""Concurrent valuations must not race in the market cache (regression for a 500 seen in the dashboard).

The dashboard loads GET /api/finance/net-worth and GET /api/portfolio in parallel; both value the same
holdings. Each cache write looks for an existing row and inserts one when there is none, so two requests
that both look before either writes collide on the unique constraint. These tests force exactly that
interleaving: a before_flush hook holds every session that is about to INSERT a mapping or price until
both concurrent sessions have got that far, then lets them write.
"""

import threading
from datetime import date
from decimal import Decimal

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.domain.market import PriceStatus, RequestBudget, SymbolStatus
from app.integrations.twelve_data import TwelveDataClient
from app.models import InstrumentSymbol, MarketPrice
from app.repositories import market_repository
from app.services import market_service
from tests.portfolio_support import nh, seed
from tests.test_auth import logged_in_client
from tests.test_market_service import FAKE_KEY, GROWW_A, ISIN_A, T0, FakeTwelveData

pytestmark = pytest.mark.usefixtures("migrated_db")

WAIT_SECONDS = 10


class InsertGate(list):
    """Records which inserts reached the gate; arm() starts holding them (after any single-threaded setup)."""

    armed = False

    def arm(self) -> "InsertGate":
        self.armed = True
        return self


@pytest.fixture
def insert_gate():
    """Once armed, make every session about to insert a mapping or price wait for one other such session."""
    gates = {InstrumentSymbol: threading.Barrier(2, timeout=WAIT_SECONDS),
             MarketPrice: threading.Barrier(2, timeout=WAIT_SECONDS)}
    reached = InsertGate()

    def hold(session, _context, _instances):
        if not reached.armed:
            return
        for model, barrier in gates.items():
            if any(isinstance(obj, model) for obj in session.new):
                reached.append(model.__name__)
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:  # only one writer got here: nothing to race with
                    pass

    event.listen(Session, "before_flush", hold)
    yield reached
    event.remove(Session, "before_flush", hold)


def gated_client(market: FakeTwelveData) -> TwelveDataClient:
    return TwelveDataClient(SecretStr(FAKE_KEY), transport=httpx2.MockTransport(market.handle))


def run_together(*jobs):
    """Run each job in its own thread at the same time; return results and any exceptions."""
    results: list = [None] * len(jobs)
    errors: list = []
    start = threading.Barrier(len(jobs))

    def run(i, job):
        start.wait()
        try:
            results[i] = job()
        except Exception as error:  # noqa: BLE001 - the test reports whatever escaped
            errors.append(error)

    threads = [threading.Thread(target=run, args=(i, job)) for i, job in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=WAIT_SECONDS * 3)
    assert not any(t.is_alive() for t in threads), "a concurrent valuation hung"
    return results, errors


def rows(model) -> int:
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(model)).scalar_one()


def test_concurrent_first_valuations_share_one_mapping_and_one_price(insert_gate):
    market = FakeTwelveData()
    budget = RequestBudget(1000)
    insert_gate.arm()

    def value():
        client = gated_client(market)
        try:
            with SessionLocal() as db:
                return market_service.get_prices(db, [GROWW_A], client=client, now=T0, budget=budget)
        finally:
            client.close()

    results, errors = run_together(value, value)

    assert errors == []
    assert insert_gate.count("InstrumentSymbol") == 2 and insert_gate.count("MarketPrice") == 2  # both raced
    assert [(r.status, r.price, r.currency) for (r,) in results] == [
        (PriceStatus.FRESH, Decimal("101.25000"), "INR")] * 2
    assert (rows(InstrumentSymbol), rows(MarketPrice)) == (1, 1)
    with SessionLocal() as db:
        mapping = market_repository.get_symbol_mapping(db, ISIN_A)
        cached = market_repository.get_cached_price(db, "SYNTHA", "XNSE")
    assert (mapping.status, mapping.symbol, mapping.mic_code) == (SymbolStatus.RESOLVED, "SYNTHA", "XNSE")
    assert (cached.price, cached.fetched_at) == (Decimal("101.25"), T0)


def test_concurrent_price_inserts_for_an_already_resolved_listing(insert_gate):
    market = FakeTwelveData()
    budget = RequestBudget(1000)
    with SessionLocal() as db:  # the mapping exists already: only the price rows can race
        market_service.resolve_isins(db, [GROWW_A], client=gated_client(market), now=T0, budget=budget)
    insert_gate.arm()

    def value():
        client = gated_client(market)
        try:
            with SessionLocal() as db:
                return market_service.get_prices(db, [GROWW_A], client=client, now=T0, budget=budget)
        finally:
            client.close()

    results, errors = run_together(value, value)
    assert errors == []
    assert insert_gate.count("MarketPrice") == 2
    assert [r.status for (r,) in results] == [PriceStatus.FRESH] * 2
    assert rows(MarketPrice) == 1


def test_simultaneous_dashboard_requests_both_succeed(insert_gate, monkeypatch):
    """The real trigger: net worth and portfolio requested at the same moment by the dashboard."""
    market = FakeTwelveData()
    monkeypatch.setattr(market_service, "default_client", lambda: gated_client(market))
    monkeypatch.setattr(market_service, "_shared_budget", RequestBudget(1000))
    portfolio_client = logged_in_client("race@example.com")
    net_worth_client = TestClient(portfolio_client.app, cookies=portfolio_client.cookies)
    with SessionLocal() as db:
        user_id = portfolio_client.get("/api/auth/me").json()["id"]
        seed(db, user_id, "zerodha", "equity", date(2026, 7, 31), [nh(isin=ISIN_A, symbol="SYNTHA", qty="10", avg="100")])
    insert_gate.arm()

    (portfolio, net_worth), errors = run_together(
        lambda: portfolio_client.get("/api/portfolio"), lambda: net_worth_client.get("/api/finance/net-worth"))

    assert errors == []
    assert (portfolio.status_code, net_worth.status_code) == (200, 200)
    assert insert_gate.count("InstrumentSymbol") == 2  # the race really happened
    assert portfolio.json()["reporting_totals"]["current_value"] == "1012.50"
    assert net_worth.json()["portfolio_value"] == "1012.50" == net_worth.json()["net_worth"]
    assert portfolio.json()["holdings"][0]["price_status"] == "fresh"
    assert (rows(InstrumentSymbol), rows(MarketPrice)) == (1, 1)
