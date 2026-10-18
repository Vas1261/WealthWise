"""Monthly (FR-23) and portfolio (FR-24) reports: contents, CSV and PDF downloads, scoping, CSV injection."""

import csv
import io
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.domain.finance import BudgetSummary, EntryRow
from app.domain.reports import CategoryTotal, MonthTotals, monthly_report
from app.main import app
from app.services import fx_service, market_service
from tests.fake_frankfurter import FakeFrankfurter
from tests.portfolio_support import nh, seed
from tests.test_auth import csrf_headers, logged_in_client
from tests.test_portfolio_service import RecordingMarket, fresh

OCT = date(2026, 10, 5)


def row(kind, category, amount, day=OCT, currency="INR", description=None):
    return EntryRow(kind, category, Decimal(amount), currency, day, description)


# --- Domain ------------------------------------------------------------------------------------------------

def test_monthly_report_totals_categories_and_entries_of_the_month_only():
    entries = [
        row("income", "Salary", "5000", date(2026, 10, 1)),
        row("expense", "Food", "120.50", date(2026, 10, 3)),
        row("expense", "Rent", "1500", date(2026, 10, 2)),
        row("expense", "Food", "30", date(2026, 10, 20)),
        row("expense", "Food", "999", date(2026, 9, 30)),  # September: not in the report
        row("income", "Gift", "50", date(2026, 10, 9), currency="EUR"),
    ]
    report = monthly_report(2026, 10, entries, BudgetSummary(2026, 10, [], []))
    assert report.totals == [
        MonthTotals("EUR", Decimal("50"), Decimal(0), Decimal("50")),
        MonthTotals("INR", Decimal("5000"), Decimal("1650.50"), Decimal("3349.50")),
    ]
    assert report.categories == [
        CategoryTotal("income", "Gift", "EUR", Decimal("50"), 1),
        CategoryTotal("income", "Salary", "INR", Decimal("5000"), 1),
        CategoryTotal("expense", "Rent", "INR", Decimal("1500"), 1),
        CategoryTotal("expense", "Food", "INR", Decimal("150.50"), 2),
    ]
    assert [e.entry_date.day for e in report.entries] == [1, 2, 3, 9, 20]


def test_monthly_report_refuses_a_budget_summary_of_another_month():
    with pytest.raises(ValueError):
        monthly_report(2026, 10, [], BudgetSummary(2026, 9, [], []))


# --- API ---------------------------------------------------------------------------------------------------

pytestmark_db = pytest.mark.usefixtures("migrated_db")


def read_csv(response) -> list[list[str]]:
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")  # BOM so Excel reads UTF-8
    return list(csv.reader(io.StringIO(text[1:])))


def section(rows: list[list[str]], title: str) -> list[list[str]]:
    """The header and data rows below a section title, up to the next blank row."""
    start = rows.index([title]) + 1
    end = next((i for i in range(start, len(rows)) if rows[i] == []), len(rows))
    return rows[start:end]


def add(client: TestClient, path: str, body: dict) -> None:
    assert client.post(path, json=body, headers=csrf_headers(client)).status_code == 201


@pytest.fixture
def market(monkeypatch):
    recorder = RecordingMarket({"INE000D01019": fresh("110")})
    monkeypatch.setattr(market_service, "get_prices", recorder)
    return recorder


@pytestmark_db
def test_reports_require_authentication():
    client = TestClient(app)
    assert client.get("/api/reports/monthly?month=2026-10").status_code == 401
    assert client.get("/api/reports/portfolio").status_code == 401


@pytestmark_db
def test_monthly_csv_has_the_three_summary_components_and_the_entries():
    alice = logged_in_client("alice@example.com")
    add(alice, "/api/finance/entries", {"kind": "income", "category": "Salary", "amount": "5000.00",
                                        "date": "2026-10-01", "description": "October pay"})
    add(alice, "/api/finance/entries", {"kind": "expense", "category": "Food", "amount": "250.00",
                                        "date": "2026-10-05", "description": "groceries"})
    add(alice, "/api/finance/entries", {"kind": "expense", "category": "Taxi", "amount": "40.00", "date": "2026-10-06"})
    add(alice, "/api/finance/budgets", {"month": "2026-10", "category": "Food", "amount": "200.00"})
    bob = logged_in_client("bob@example.com")
    add(bob, "/api/finance/entries", {"kind": "expense", "category": "Bob only", "amount": "1.00", "date": "2026-10-05"})

    response = alice.get("/api/reports/monthly?month=2026-10&format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="wealthwise-monthly-2026-10.csv"'
    assert response.headers["cache-control"] == "no-store"
    rows = read_csv(response)
    assert rows[0] == ["WealthWise monthly report: October 2026"]
    assert section(rows, "Summary") == [["Currency", "Income", "Expenses", "Net"], ["INR", "5000.00", "290.00", "4710.00"]]
    assert section(rows, "By category")[1:] == [
        ["Income", "Salary", "INR", "1", "5000.00"], ["Expense", "Food", "INR", "1", "250.00"],
        ["Expense", "Taxi", "INR", "1", "40.00"]]
    assert section(rows, "Budget vs actual")[1:] == [["Food", "INR", "200.00", "250.00", "-50.00", "125.00", "yes"]]
    assert section(rows, "Expenses without a budget")[1:] == [["Taxi", "INR", "40.00"]]
    assert section(rows, "Entries")[1:] == [
        ["2026-10-01", "Income", "Salary", "October pay", "INR", "5000.00"],
        ["2026-10-05", "Expense", "Food", "groceries", "INR", "250.00"],
        ["2026-10-06", "Expense", "Taxi", "", "INR", "40.00"]]
    assert b"Bob only" not in response.content


@pytestmark_db
def test_csv_never_lets_a_spreadsheet_run_typed_text_as_a_formula():
    alice = logged_in_client("alice@example.com")
    add(alice, "/api/finance/entries", {"kind": "expense", "category": "=HYPERLINK(\"x\")", "amount": "5.00",
                                        "date": "2026-10-05", "description": "@SUM(A1)"})
    entries = section(read_csv(alice.get("/api/reports/monthly?month=2026-10&format=csv")), "Entries")
    assert entries[1][2:4] == ["'=HYPERLINK(\"x\")", "'@SUM(A1)"]


@pytestmark_db
def test_monthly_pdf_download_with_text_that_looks_like_markup():
    alice = logged_in_client("alice@example.com")
    add(alice, "/api/finance/entries", {"kind": "expense", "category": "<b>Food & more</b>", "amount": "5.00",
                                        "date": "2026-10-05", "description": "a\nb <i>"})
    response = alice.get("/api/reports/monthly?month=2026-10")  # PDF is the default
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="wealthwise-monthly-2026-10.pdf"'
    assert response.content.startswith(b"%PDF-") and len(response.content) > 1000


@pytestmark_db
def test_an_empty_month_still_gives_a_report():
    alice = logged_in_client("alice@example.com")
    assert alice.get("/api/reports/monthly?month=2026-10").status_code == 200
    rows = read_csv(alice.get("/api/reports/monthly?month=2026-10&format=csv"))
    assert section(rows, "Entries") == [["Date", "Type", "Category", "Description", "Currency", "Amount"]]


@pytestmark_db
@pytest.mark.parametrize("query", ["month=2026-13", "month=oct", "month=2026-10&format=xls"])
def test_invalid_requests_are_refused(query):
    alice = logged_in_client("alice@example.com")
    response = alice.get(f"/api/reports/monthly?{query}")
    assert response.status_code == 422
    if "month=2026-10" not in query:
        assert response.json()["code"] == "invalid_month"


@pytestmark_db
def test_portfolio_csv_lists_holdings_with_valuation_and_pnl(market):
    alice = logged_in_client("alice@example.com")
    with SessionLocal() as db:
        seed(db, alice.get("/api/auth/me").json()["id"], "zerodha", "equity", date(2026, 7, 31),
             [nh(name="=SYNTHA", qty="10", avg="100")])
    response = alice.get("/api/reports/portfolio?format=csv")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith('attachment; filename="wealthwise-portfolio-')
    rows = read_csv(response)
    totals = dict(section(rows, "Totals in INR")[1:])
    assert (totals["Current value (priced holdings)"], totals["Unrealised P&L"], totals["Unrealised P&L %"]) == (
        "1100.00", "100.00", "10.00")
    header, holding = section(rows, "Holdings (values in each holding's own currency)")
    record = dict(zip(header, holding, strict=True))
    assert (record["Instrument"], record["ISIN"], record["Quantity"], record["Invested"], record["Price"]) == (
        "'=SYNTHA", "INE000D01019", "10.000000", "1000.00", "110.0000")
    assert (record["Current value"], record["Unrealised P&L"], record["P&L %"], record["Price status"]) == (
        "1100.00", "100.00", "10.00", "Live")
    assert "Cost dates from" not in header  # everything is in the reporting currency


@pytestmark_db
def test_portfolio_report_in_another_currency_includes_the_gain_split(market, monkeypatch):
    fake = FakeFrankfurter()
    fake.history[("INR", "EUR")] = {"2026-07-31": "0.0100"}
    monkeypatch.setattr(fx_service, "default_client", fake.client)
    alice = logged_in_client("alice@example.com")
    alice.patch("/api/auth/me", json={"reporting_currency": "EUR"}, headers=csrf_headers(alice))
    with SessionLocal() as db:
        seed(db, alice.get("/api/auth/me").json()["id"], "zerodha", "equity", date(2026, 7, 31), [nh()])
    rows = read_csv(alice.get("/api/reports/portfolio?format=csv"))
    totals = dict(section(rows, "Totals in EUR")[1:])
    assert (totals["Unrealised P&L (market gain)"], totals["Currency gain (exchange-rate change)"],
            totals["Total gain"], totals["Total invested at purchase-time rates"]) == ("1.05", "0.50", "1.55", "10.00")
    header, holding = section(rows, "Holdings (values in each holding's own currency)")
    record = dict(zip(header, holding, strict=True))
    assert (record["Pinned rate into EUR"], record["Cost in EUR at purchase-time rates"],
            record["Cost dates from"]) == ("0.010000", "10.00", "2026-07-31")
    pdf = alice.get("/api/reports/portfolio?format=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")


@pytestmark_db
def test_portfolio_pdf_with_no_holdings(market):
    alice = logged_in_client("alice@example.com")
    response = alice.get("/api/reports/portfolio")
    assert response.status_code == 200 and response.content.startswith(b"%PDF-")


# --- Monthly totals (dashboard chart) -------------------------------------------------------------------------

def test_month_series_covers_every_month_oldest_first_across_a_year_end():
    from app.domain.reports import month_series
    entries = [row("income", "Salary", "100", date(2025, 12, 31)), row("expense", "Food", "40", date(2026, 2, 1))]
    points = month_series(2026, 2, 3, entries)
    assert [(p.year, p.month) for p in points] == [(2025, 12), (2026, 1), (2026, 2)]
    assert [p.totals for p in points] == [
        [MonthTotals("INR", Decimal("100"), Decimal(0), Decimal("100"))], [],
        [MonthTotals("INR", Decimal(0), Decimal("40"), Decimal("-40"))]]


@pytestmark_db
def test_monthly_totals_endpoint_is_scoped_and_validated():
    alice = logged_in_client("alice@example.com")
    add(alice, "/api/finance/entries", {"kind": "income", "category": "Salary", "amount": "5000.00", "date": "2026-09-01"})
    add(alice, "/api/finance/entries", {"kind": "expense", "category": "Food", "amount": "250.50", "date": "2026-10-05"})
    add(alice, "/api/finance/entries", {"kind": "expense", "category": "Old", "amount": "9.00", "date": "2026-07-31"})
    bob = logged_in_client("bob@example.com")
    add(bob, "/api/finance/entries", {"kind": "expense", "category": "Bob", "amount": "1.00", "date": "2026-10-05"})
    body = alice.get("/api/finance/monthly-totals?end=2026-10&months=2").json()
    assert body == [
        {"month": "2026-09", "totals": [{"currency": "INR", "income": "5000.00", "expenses": "0.00", "net": "5000.00"}]},
        {"month": "2026-10", "totals": [{"currency": "INR", "income": "0.00", "expenses": "250.50", "net": "-250.50"}]},
    ]
    assert len(alice.get("/api/finance/monthly-totals?end=2026-10").json()) == 12  # default
    assert alice.get("/api/finance/monthly-totals?end=2026-13").json()["code"] == "invalid_month"
    assert alice.get("/api/finance/monthly-totals?end=2026-10&months=37").status_code == 422
    assert TestClient(app).get("/api/finance/monthly-totals?end=2026-10").status_code == 401
