"""Concurrent valuations: one provider request per price, and no cache races (regression for a dashboard 500).

The dashboard loads GET /api/finance/net-worth and GET /api/portfolio in parallel; both value the same
holdings. Within one process the Market Service makes them take turns, so the second finds the first one's
prices in the cache and makes no request. Between worker processes nothing but the unique constraint keeps
two inserts of the same ISIN apart; the insert_gate test switches the in-process lock off and forces that
interleaving with a before_flush hook that holds each session about to INSERT a price until both have got
that far.
"""

import contextlib
import threading
import time
from datetime import date
from decimal import Decimal

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.domain.market import PriceStatus
from app.integrations.upstox import UpstoxClient
from app.models import MarketPrice
from app.services import market_service
from tests.portfolio_support import nh, seed
from tests.test_auth import logged_in_client
from tests.test_market_service import EQUITY_A, FAKE_TOKEN, ISIN_A, ISIN_B, T0, FakeUpstox

pytestmark = pytest.mark.usefixtures("migrated_db")

WAIT_SECONDS = 10


class SlowUpstox(FakeUpstox):
    """Every request takes long enough for the other concurrent requests to catch up with it."""

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        time.sleep(0.2)
        return super().handle(request)


def client_for(market: FakeUpstox) -> UpstoxClient:
    return UpstoxClient(SecretStr(FAKE_TOKEN), transport=httpx2.MockTransport(market.handle))


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


def price_rows() -> int:
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(MarketPrice)).scalar_one()


def value_a(market):
    def job():
        client = client_for(market)
        try:
            with SessionLocal() as db:
                return market_service.get_prices(db, [EQUITY_A], client=client, now=T0)
        finally:
            client.close()
    return job


# --- One process: requests take turns and share the result --------------------------------------------

@pytest.mark.parametrize("requests", [2, 4], ids=["dashboard", "dashboard-in-dev-strict-mode"])
def test_concurrent_valuations_fetch_each_price_once(requests):
    market = SlowUpstox()
    results, errors = run_together(*[value_a(market)] * requests)
    assert errors == []
    assert [(r.status, r.price) for (r,) in results] == [(PriceStatus.FRESH, Decimal("101.25"))] * requests
    assert len(market.calls) == 1
    assert price_rows() == 1


def test_simultaneous_dashboard_requests_share_one_price_request(monkeypatch):
    market = SlowUpstox()
    monkeypatch.setattr(market_service, "default_client", lambda: client_for(market))
    portfolio_client = logged_in_client("race@example.com")
    net_worth_client = TestClient(portfolio_client.app, cookies=portfolio_client.cookies)
    with SessionLocal() as db:
        user_id = portfolio_client.get("/api/auth/me").json()["id"]
        seed(db, user_id, "zerodha", "equity", date(2026, 7, 31), [
            nh(isin=ISIN_A, symbol="SYNTHA", qty="10", avg="100"),
            nh(name="SYNTHB", isin=ISIN_B, symbol="SYNTHB", qty="2", avg="50"),
        ])

    (portfolio, net_worth), errors = run_together(
        lambda: portfolio_client.get("/api/portfolio"), lambda: net_worth_client.get("/api/finance/net-worth"))

    assert errors == []
    assert (portfolio.status_code, net_worth.status_code) == (200, 200)
    assert [h["price_status"] for h in portfolio.json()["holdings"]] == ["fresh", "fresh"]
    assert portfolio.json()["reporting_totals"]["current_value"] == "1123.50"  # 10 x 101.25 + 2 x 55.50
    assert net_worth.json()["portfolio_value"] == "1123.50" and net_worth.json()["unpriced_holdings"] == 0
    assert len(market.calls) == 1
    assert price_rows() == 2


# --- Separate processes: only the database keeps inserts apart ----------------------------------------

@pytest.fixture
def insert_gate(monkeypatch):
    """Make every session about to insert a price wait for one other such session."""
    monkeypatch.setattr(market_service, "_fetch_lock", contextlib.nullcontext())  # as between two workers
    barrier = threading.Barrier(2, timeout=WAIT_SECONDS)
    reached: list[str] = []

    def hold(session, _context, _instances):
        if any(isinstance(obj, MarketPrice) for obj in session.new):
            reached.append("MarketPrice")
            try:
                barrier.wait()
            except threading.BrokenBarrierError:  # only one writer got here: nothing to race with
                pass

    event.listen(Session, "before_flush", hold)
    yield reached
    event.remove(Session, "before_flush", hold)


def test_concurrent_first_inserts_from_two_processes_keep_one_row(insert_gate):
    market = FakeUpstox()
    results, errors = run_together(value_a(market), value_a(market))
    assert errors == []
    assert insert_gate.count("MarketPrice") == 2  # both really raced to insert
    assert [(r.status, r.price) for (r,) in results] == [(PriceStatus.FRESH, Decimal("101.25"))] * 2
    assert price_rows() == 1
