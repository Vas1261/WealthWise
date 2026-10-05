"""Finance Service orchestration (offline: fake Frankfurter, recorded market prices)."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.database import SessionLocal
from app.domain.finance import FinanceIssue, FinanceValidationError
from app.domain.fx import FxStatus
from app.repositories import user_repository
from app.services import finance_service, fx_service, market_service, portfolio_service
from tests.fake_frankfurter import FakeFrankfurter
from tests.portfolio_support import make_user, nh, seed
from tests.test_portfolio_service import RecordingMarket, fresh

pytestmark = pytest.mark.usefixtures("migrated_db")


@pytest.fixture
def frankfurter(monkeypatch):
    fake = FakeFrankfurter()
    monkeypatch.setattr(fx_service, "default_client", fake.client)
    return fake


@pytest.fixture
def market(monkeypatch):
    recorder = RecordingMarket({"INE000D01019": fresh("110")})
    monkeypatch.setattr(market_service, "get_prices", recorder)
    return recorder


def entry(db, user, amount="10", kind="expense", category="Food", day="2026-10-05", description=None):
    return finance_service.create_entry(db, user, kind=kind, category=category, amount=amount, entry_date=day,
                                        description=description)


def set_currency(db, user, currency):
    user_repository.set_reporting_currency(db, user, currency)
    db.commit()


def test_create_normalises_and_stamps_reporting_currency():
    with SessionLocal() as db:
        user = make_user(db, "a@example.com", reporting_currency="EUR")
        e = entry(db, user, "12.5", category="  Eating   out ", description="  team lunch ")
        assert (e.category, e.amount, e.currency, e.entry_date, e.description) == (
            "Eating out", Decimal("12.5"), "EUR", date(2026, 10, 5), "team lunch")
        budget = finance_service.create_budget(db, user, month="2026-10", category=" Eating  out", amount="80")
        assert (budget.category, budget.amount, budget.currency, budget.year, budget.month) == (
            "Eating out", Decimal("80"), "EUR", 2026, 10)


def test_invalid_input_is_not_stored():
    with SessionLocal() as db:
        user = make_user(db, "a@example.com")
        with pytest.raises(FinanceValidationError) as raised:
            entry(db, user, "1.001")
        assert raised.value.issue == FinanceIssue.INVALID_AMOUNT
        with pytest.raises(FinanceValidationError):
            entry(db, user, day="2026-02-30")
        assert finance_service.list_entries(db, user) == []


def test_reporting_currency_change_never_reinterprets_rows():
    with SessionLocal() as db:
        user = make_user(db, "a@example.com")
        old = entry(db, user, "100", kind="income")
        budget = finance_service.create_budget(db, user, month="2026-10", category="Food", amount="50")
        set_currency(db, user, "EUR")
        new = entry(db, user, "5", kind="income")
        replaced = finance_service.replace_entry(db, user, old.id, kind="income", category="Gift", amount="200",
                                                 entry_date="2026-10-06", description=None)
        changed = finance_service.set_budget_amount(db, user, budget.id, "75")
        assert (old.currency, new.currency, replaced.currency, changed.currency) == ("INR", "EUR", "INR", "INR")
        assert (replaced.amount, changed.amount) == (Decimal("200"), Decimal("75"))


def test_not_found_for_missing_and_other_users_rows():
    with SessionLocal() as db:
        alice, bob = make_user(db, "a@example.com"), make_user(db, "b@example.com")
        e = entry(db, alice)
        b = finance_service.create_budget(db, alice, month="2026-10", category="Food", amount="50")
        for call in (
            lambda: finance_service.get_entry(db, bob, e.id),
            lambda: finance_service.replace_entry(db, bob, e.id, kind="income", category="X", amount="1",
                                                  entry_date="2026-10-05", description=None),
            lambda: finance_service.delete_entry(db, bob, e.id),
            lambda: finance_service.get_entry(db, alice, 999),
        ):
            with pytest.raises(finance_service.EntryNotFound):
                call()
        for call in (
            lambda: finance_service.set_budget_amount(db, bob, b.id, "1"),
            lambda: finance_service.delete_budget(db, bob, b.id),
        ):
            with pytest.raises(finance_service.BudgetNotFound):
                call()
        assert finance_service.get_entry(db, alice, e.id).amount == Decimal("10")


def test_duplicate_budget_is_refused_and_session_stays_usable():
    with SessionLocal() as db:
        user = make_user(db, "a@example.com")
        finance_service.create_budget(db, user, month="2026-10", category="Food", amount="50")
        with pytest.raises(finance_service.BudgetExists):
            finance_service.create_budget(db, user, month="2026-10", category=" Food ", amount="60")
        assert [b.amount for b in finance_service.list_budgets(db, user, "2026-10")] == [Decimal("50")]
        entry(db, user)  # the session still works after the rollback


def test_list_filters_validate_before_querying():
    with SessionLocal() as db:
        user = make_user(db, "a@example.com")
        entry(db, user, category="Food", day="2026-10-05")
        assert len(finance_service.list_entries(db, user, category="  Food ")) == 1
        with pytest.raises(FinanceValidationError) as raised:
            finance_service.list_entries(db, user, start="2026-10-06", end="2026-10-05")
        assert raised.value.issue == FinanceIssue.INVALID_DATE_RANGE


def test_budget_summary_uses_the_month():
    with SessionLocal() as db:
        user = make_user(db, "a@example.com")
        finance_service.create_budget(db, user, month="2026-10", category="Food", amount="100")
        entry(db, user, "30", day="2026-10-31")
        entry(db, user, "40", day="2026-11-01")
        entry(db, user, "5", category="Travel")
        summary = finance_service.budget_summary(db, user, "2026-10")
        assert [(b.category, b.actual) for b in summary.budgets] == [("Food", Decimal("30"))]
        assert [(u.category, u.actual) for u in summary.unbudgeted] == [("Travel", Decimal("5"))]


def test_net_worth_for_inr_user_needs_no_fx(frankfurter, market):
    with SessionLocal() as db:
        user = make_user(db, "a@example.com")
        seed(db, user.id, "zerodha", "equity", date(2026, 7, 31), [nh(qty="10", avg="100")])
        entry(db, user, "1000", kind="income")
        entry(db, user, "250.50")
        n = finance_service.get_net_worth(db, user)
    assert (n.portfolio_value, n.cash_position, n.net_worth, n.complete) == (
        Decimal("1100"), Decimal("749.50"), Decimal("1849.50"), True)
    assert frankfurter.calls == []
    assert [force for _, force in market.calls] == [False]  # never forces a price refresh


def test_net_worth_converts_mixed_currency_cash(frankfurter, market):
    with SessionLocal() as db:
        user = make_user(db, "a@example.com")
        seed(db, user.id, "zerodha", "equity", date(2026, 7, 31), [nh(qty="10", avg="100")])
        seed(db, user.id, "groww", "mutual_fund", date(2026, 7, 31),
             [nh("Fund", None, None, "5", "20", asset_class="mutual_fund", broker="groww")])
        entry(db, user, "1000", kind="income")  # INR
        set_currency(db, user, "EUR")
        entry(db, user, "4", kind="income")  # EUR
        n = finance_service.get_net_worth(db, user)
    rate = Decimal("0.0105")
    assert n.portfolio_value == Decimal("1100") * rate
    assert n.cash_position == Decimal("1000") * rate + Decimal("4")
    assert n.net_worth == n.portfolio_value + n.cash_position
    assert n.cost_floor_value == Decimal("100") * rate  # shown, never added
    assert (n.complete, n.unpriced_holdings) == (False, 1)
    assert n.cash_by_currency == {"INR": Decimal("1000"), "EUR": Decimal("4")}
    assert frankfurter.rate_calls() == ["/v2/rate/INR/EUR"]  # cached between portfolio and cash
    assert n.fx_rates["EUR"].status == FxStatus.IDENTITY


def test_net_worth_without_rate_has_no_total(frankfurter, market):
    frankfurter.rates.pop(("INR", "EUR"))
    with SessionLocal() as db:
        user = make_user(db, "a@example.com")
        entry(db, user, "100", kind="income")
        set_currency(db, user, "EUR")
        n = finance_service.get_net_worth(db, user)
    assert (n.net_worth, n.cash_position, n.fx_unavailable, n.complete) == (None, None, ["INR"], False)


def test_net_worth_passes_portfolio_data_errors_through(monkeypatch):
    def broken(db, user, **_):
        raise portfolio_service.PortfolioDataError("zerodha", "equity")

    monkeypatch.setattr(portfolio_service, "get_portfolio", broken)
    with SessionLocal() as db:
        user = make_user(db, "a@example.com")
        with pytest.raises(portfolio_service.PortfolioDataError):
            finance_service.get_net_worth(db, user)
