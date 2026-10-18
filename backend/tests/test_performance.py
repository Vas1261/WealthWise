"""Timed checks of NFR-01 and NFR-02 (opt-in: they are slow and depend on the machine).

    RUN_PERFORMANCE=1 .venv/bin/pytest -m performance -s tests/test_performance.py

NFR-01: the dashboard's data within 3 s for a large account. WealthWise imports holdings snapshots, not
transactions, so "5,000 transactions" is read as 5,000 income/expense entries plus a 500-holding portfolio
whose mutual funds carry 5,000 dated purchases (the order-history rows behind them).
NFR-02: a 10,000-row broker statement uploaded and stored within 30 s.
Prices and exchange rates come from in-memory stand-ins, so the timings measure WealthWise, not the network.
"""

import os
import time
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.core.database import SessionLocal
from app.domain.holdings import CostDate, NormalizedHolding
from app.models import FinanceEntry
from app.services import fx_service, market_service
from tests import test_groww_mutual_fund_adapter as groww_mf
from tests import test_zerodha_equity_adapter as zerodha
from tests.fake_frankfurter import FakeFrankfurter
from tests.portfolio_support import seed, write_xlsx
from tests.test_auth import csrf_headers, logged_in_client
from tests.test_imports_api import upload
from tests.test_portfolio_service import RecordingMarket, fresh

pytestmark = [
    pytest.mark.performance,
    pytest.mark.usefixtures("migrated_db"),
    pytest.mark.skipif(os.environ.get("RUN_PERFORMANCE") != "1", reason="set RUN_PERFORMANCE=1 to run"),
]

DASHBOARD = ["/api/finance/net-worth", "/api/portfolio", "/api/finance/budgets/summary?month=2026-10", "/api/imports"]


def isin(n: int) -> str:
    return f"INE{n:08d}0"


def timed(work) -> tuple[float, object]:
    start = time.perf_counter()
    result = work()
    return time.perf_counter() - start, result


def test_nfr01_dashboard_data_for_a_large_account_within_3_seconds(monkeypatch):
    fake = FakeFrankfurter()
    monkeypatch.setattr(fx_service, "default_client", fake.client)
    monkeypatch.setattr(market_service, "get_prices",
                        RecordingMarket({isin(n): fresh("110") for n in range(450)}))
    client = logged_in_client("large@example.com")
    client.patch("/api/auth/me", json={"reporting_currency": "EUR"}, headers=csrf_headers(client))
    user_id = client.get("/api/auth/me").json()["id"]
    first = date(2019, 4, 1)
    with SessionLocal() as db:
        seed(db, user_id, "zerodha", "equity", date(2026, 9, 30), [
            NormalizedHolding(f"SYNTH {n}", isin(n), f"S{n}", "equity", Decimal(10), Decimal(100), "INR", "zerodha", "")
            for n in range(450)])
        seed(db, user_id, "groww", "mutual_fund", date(2026, 9, 30), [
            NormalizedHolding(f"Fund {n}", None, None, "mutual_fund", Decimal(50), Decimal(20), "INR", "groww", "",
                              tuple(CostDate(first + timedelta(days=30 * k + n), Decimal(10)) for k in range(100)))
            for n in range(50)])  # 50 funds x 100 monthly purchases = 5,000 dated purchases
        db.add_all(FinanceEntry(user_id=user_id, kind="expense" if n % 4 else "income", category=f"Cat {n % 25}",
                                amount=Decimal("12.50"), currency="EUR", entry_date=date(2026, 10, 1 + n % 28))
                   for n in range(5000))
        db.commit()

    cold, _ = timed(lambda: [client.get(path) for path in DASHBOARD])  # first view also pins the purchase-date rates
    warm, responses = timed(lambda: [client.get(path) for path in DASHBOARD])
    assert all(r.status_code == 200 for r in responses)
    assert responses[1].json()["reporting_totals"]["currency_gain"] is not None
    print(f"\nNFR-01 dashboard data, 5,000 entries + 500 holdings: first view {cold:.2f} s, next view {warm:.2f} s")
    assert cold < 3 and warm < 3


@pytest.mark.parametrize("statement", ["zerodha equity", "groww mutual fund orders"])
def test_nfr02_a_10000_row_upload_is_stored_within_30_seconds(tmp_path, statement):
    client = logged_in_client("upload@example.com")
    if statement == "zerodha equity":
        rows = [zerodha.holding(Symbol=f"S{n}", ISIN=isin(n)) for n in range(10_000)]
        path = write_xlsx(tmp_path / "big.xlsx", {"Combined": zerodha.sheet_rows(rows)}, column_offset=1)
    else:
        rows = [groww_mf.tx(scheme=f"Fund {n % 100}", units="1.5", nav="20.25", when=f"{1 + n % 28:02d} Jan 2026")
                for n in range(10_000)]
        path = write_xlsx(tmp_path / "big.xlsx", {"Transactions": groww_mf.statement_rows(rows)})
    seconds, response = timed(lambda: upload(client, path))
    assert response.status_code == 201, response.text
    assert response.json()["rows_succeeded"] == (10_000 if statement == "zerodha equity" else 100)
    print(f"\nNFR-02 {statement}: 10,000 rows ({os.path.getsize(path) // 1024} KB) imported in {seconds:.2f} s")
    assert seconds < 30
