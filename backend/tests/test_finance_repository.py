"""Finance repository: every operation is scoped to its user; database constraints back the domain rules."""

import random
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DataError, IntegrityError, StatementError
from sqlalchemy.schema import CreateTable

from app.core.database import SessionLocal, engine
from app.domain.finance import EntryRow, cash_by_currency
from app.models import Budget, FinanceEntry
from app.repositories import finance_repository as repo
from app.repositories import user_repository
from tests.portfolio_support import make_user

pytestmark = pytest.mark.usefixtures("migrated_db")

D = date(2026, 10, 5)
# Constraint errors are worded differently by each engine ("CHECK constraint failed" on SQLite,
# "violates check constraint" on PostgreSQL); both name the kind of constraint.
CHECK_VIOLATION = "(?i)check constraint"
UNIQUE_VIOLATION = "(?i)unique constraint"


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def add_entry(db, user_id, amount="10", *, kind="expense", category="Food", day=D, currency="INR",
              description=None):
    return repo.create_entry(db, user_id, kind=kind, category=category, amount=Decimal(amount), currency=currency,
                             entry_date=day, description=description)


def add_budget(db, user_id, amount="100", *, category="Food", year=2026, month=10, currency="INR"):
    return repo.create_budget(db, user_id, category=category, year=year, month=month, amount=Decimal(amount),
                              currency=currency)


@pytest.fixture
def two_users():
    with SessionLocal() as db:
        alice, bob = make_user(db, "alice@example.com"), make_user(db, "bob@example.com")
        return alice.id, bob.id


def test_entries_are_invisible_to_other_users(two_users):
    alice, bob = two_users
    with SessionLocal() as db:
        entry = add_entry(db, alice)
        db.commit()
        assert repo.get_entry(db, alice, entry.id) is entry
        assert repo.get_entry(db, bob, entry.id) is None
        assert repo.list_entries(db, bob) == []
        assert repo.list_cash_rows(db, bob) == []
        assert repo.list_expenses_between(db, bob, date(2026, 10, 1), date(2026, 11, 1)) == []
        assert repo.replace_entry(db, bob, entry.id, kind="income", category="X", amount=Decimal("1"),
                                  entry_date=D, description=None) is None
        assert repo.delete_entry(db, bob, entry.id) is False
        db.commit()
    with SessionLocal() as db:
        unchanged = repo.get_entry(db, alice, entry.id)
        assert (unchanged.kind, unchanged.amount) == ("expense", Decimal("10.00"))


def test_budgets_are_invisible_to_other_users(two_users):
    alice, bob = two_users
    with SessionLocal() as db:
        budget = add_budget(db, alice)
        db.commit()
        assert repo.get_budget(db, alice, budget.id) is budget
        assert repo.get_budget(db, bob, budget.id) is None
        assert repo.list_budgets(db, bob, 2026, 10) == []
        assert repo.set_budget_amount(db, bob, budget.id, Decimal("1")) is None
        assert repo.delete_budget(db, bob, budget.id) is False
        db.commit()
    with SessionLocal() as db:
        assert repo.get_budget(db, alice, budget.id).amount == Decimal("100.00")


def test_list_entries_filters_and_orders_newest_first(two_users):
    alice, bob = two_users
    with SessionLocal() as db:
        a = add_entry(db, alice, day=date(2026, 10, 1))
        b = add_entry(db, alice, day=date(2026, 10, 3), kind="income", category="Salary")
        c = add_entry(db, alice, day=date(2026, 10, 3))
        d = add_entry(db, alice, day=date(2026, 10, 9), category="food")
        add_entry(db, bob, day=date(2026, 10, 3))
        db.commit()
        ids = lambda **kw: [e.id for e in repo.list_entries(db, alice, **kw)]  # noqa: E731
        assert ids() == [d.id, c.id, b.id, a.id]
        assert ids(start=date(2026, 10, 3), end=date(2026, 10, 3)) == [c.id, b.id]  # inclusive bounds
        assert ids(start=date(2026, 10, 4)) == [d.id]
        assert ids(end=date(2026, 10, 2)) == [a.id]
        assert ids(kind="income") == [b.id]
        assert ids(category="Food") == [c.id, a.id]  # exact, case-sensitive


def test_cash_and_expense_rows(two_users):
    alice, _ = two_users
    with SessionLocal() as db:
        add_entry(db, alice, "1.10", kind="income", description="private note")
        add_entry(db, alice, "2.20", day=date(2026, 10, 31))
        add_entry(db, alice, "3.30", day=date(2026, 11, 1))
        add_entry(db, alice, "4.40", day=date(2026, 9, 30))
        db.commit()
        cash = repo.list_cash_rows(db, alice)
        assert [r.amount for r in cash] == [Decimal("1.10"), Decimal("2.20"), Decimal("3.30"), Decimal("4.40")]
        assert all(isinstance(r, EntryRow) for r in cash)
        expenses = repo.list_expenses_between(db, alice, date(2026, 10, 1), date(2026, 11, 1))
        assert [r.amount for r in expenses] == [Decimal("2.20")]  # half-open month, income excluded
        assert len(repo.list_expenses_between(db, alice, date(2026, 10, 1), None)) == 2


def test_replace_entry_keeps_currency_and_creation_time(two_users):
    alice, _ = two_users
    with SessionLocal() as db:
        entry = add_entry(db, alice, currency="EUR", description="old")
        db.commit()
        created = entry.created_at
        repo.replace_entry(db, alice, entry.id, kind="income", category="Gift", amount=Decimal("5.5"),
                           entry_date=date(2026, 1, 2), description=None)
        db.commit()
    with SessionLocal() as db:
        e = repo.get_entry(db, alice, entry.id)
        assert (e.kind, e.category, e.amount, e.entry_date, e.description) == (
            "income", "Gift", Decimal("5.50"), date(2026, 1, 2), None)
        # SQLite drops the timezone on read, PostgreSQL returns it: compare the stored instant.
        assert (e.currency, as_utc(e.created_at), e.user_id) == ("EUR", as_utc(created), alice)


def test_set_budget_amount_changes_only_the_amount(two_users):
    alice, _ = two_users
    with SessionLocal() as db:
        budget = add_budget(db, alice, currency="EUR")
        db.commit()
        repo.set_budget_amount(db, alice, budget.id, Decimal("42.5"))
        db.commit()
    with SessionLocal() as db:
        b = repo.get_budget(db, alice, budget.id)
        assert (b.amount, b.currency, b.category, b.year, b.month) == (Decimal("42.50"), "EUR", "Food", 2026, 10)


def test_amounts_round_trip_exactly(two_users):
    alice, _ = two_users
    with SessionLocal() as db:
        largest = add_entry(db, alice, "999999999999999999.99")  # the column's maximum (20 digits)
        smallest = add_entry(db, alice, "0.01")
        tenth = add_entry(db, alice, "0.10")
        db.commit()
    with SessionLocal() as db:
        assert repo.get_entry(db, alice, largest.id).amount == Decimal("999999999999999999.99")
        assert repo.get_entry(db, alice, smallest.id).amount == Decimal("0.01")
        assert str(repo.get_entry(db, alice, tenth.id).amount) == "0.10"


@pytest.mark.parametrize("amount", ["0", "0.00", "-1"])
def test_database_rejects_non_positive_entry_amounts(two_users, amount):
    with SessionLocal() as db:
        db.add(FinanceEntry(user_id=two_users[0], kind="expense", category="Food", amount=Decimal(amount),
                            currency="INR", entry_date=D))
        with pytest.raises(IntegrityError, match=CHECK_VIOLATION):
            db.commit()


def test_database_rejects_unknown_kind(two_users):
    with SessionLocal() as db:
        db.add(FinanceEntry(user_id=two_users[0], kind="transfer", category="Food", amount=Decimal("1"),
                            currency="INR", entry_date=D))
        # SQLite: the CHECK constraint; PostgreSQL rejects it already as longer than the VARCHAR(7) column.
        with pytest.raises((IntegrityError, DataError)):
            db.commit()


@pytest.mark.parametrize(("amount", "year", "month"), [("0", 2026, 10), ("-5", 2026, 10), ("1", 2026, 0),
                                                       ("1", 2026, 13), ("1", 1899, 1), ("1", 10000, 1)])
def test_database_rejects_invalid_budgets(two_users, amount, year, month):
    with SessionLocal() as db:
        db.add(Budget(user_id=two_users[0], category="Food", year=year, month=month, amount=Decimal(amount),
                      currency="INR"))
        with pytest.raises(IntegrityError, match=CHECK_VIOLATION):
            db.commit()


def test_one_budget_per_user_category_and_month(two_users):
    alice, bob = two_users
    with SessionLocal() as db:
        add_budget(db, alice)
        add_budget(db, alice, category="food")  # different category (case-sensitive)
        add_budget(db, alice, month=11)
        add_budget(db, bob)  # other user
        db.commit()
        with pytest.raises(IntegrityError, match=UNIQUE_VIOLATION):
            add_budget(db, alice, currency="EUR")  # currency is not part of the identity


def test_deleting_a_user_deletes_their_finance_rows(two_users):
    alice, bob = two_users
    with SessionLocal() as db:
        add_entry(db, alice)
        add_budget(db, alice)
        add_entry(db, bob)
        db.commit()
        db.delete(user_repository.get_by_id(db, alice))
        db.commit()
        assert db.query(FinanceEntry).count() == 1 and db.query(Budget).count() == 0


# --- Exact money storage (ExactMoney: decimal text on SQLite, NUMERIC(20, 2) elsewhere) ------------------

def test_random_twenty_digit_amounts_round_trip_exactly(two_users):
    rng = random.Random(9)  # deterministic
    amounts = [Decimal(f"{rng.randrange(10**18)}.{rng.randrange(100):02d}") for _ in range(300)]
    amounts += [Decimal("999999999999999999.99"), Decimal("0.01"), Decimal("0.10"), Decimal("12345678901234567.8")]
    alice, _ = two_users
    with SessionLocal() as db:
        ids = [add_entry(db, alice, str(a)).id for a in amounts]
        budget_ids = [add_budget(db, alice, str(a), category=f"c{i}").id for i, a in enumerate(amounts[:50])]
        db.commit()
    with SessionLocal() as db:
        assert [repo.get_entry(db, alice, i).amount for i in ids] == amounts
        assert [repo.get_budget(db, alice, i).amount for i in budget_ids] == amounts[:50]
        assert all(r.amount.as_tuple().exponent == -2 for r in repo.list_cash_rows(db, alice))  # always 2 dp
        assert cash_by_currency(repo.list_cash_rows(db, alice))["INR"] == -sum(amounts, Decimal(0))


@pytest.mark.skipif(engine.dialect.name != "sqlite", reason="SQLite storage detail; PostgreSQL has NUMERIC")
def test_sqlite_stores_canonical_decimal_text_not_floats(two_users):
    with SessionLocal() as db:
        add_entry(db, two_users[0], "1499.5")
        add_entry(db, two_users[0], "999999999999999999.99")
        add_budget(db, two_users[0], "7")
        db.commit()
        entries = db.execute(text("SELECT typeof(amount), amount FROM finance_entries ORDER BY id")).all()
        budgets = db.execute(text("SELECT typeof(amount), amount FROM budgets")).all()
    assert entries == [("text", "1499.50"), ("text", "999999999999999999.99")]
    assert budgets == [("text", "7.00")]


@pytest.mark.parametrize("amount", [Decimal("1.005"), Decimal("0.001"), Decimal("NaN"), Decimal("Infinity"), 1.5, "1"])
def test_money_type_refuses_rounding_and_non_decimals(two_users, amount):
    with SessionLocal() as db:
        db.add(FinanceEntry(user_id=two_users[0], kind="expense", category="Food", amount=amount, currency="INR",
                            entry_date=D))
        with pytest.raises(StatementError, match="money must"):
            db.commit()


@pytest.mark.parametrize("stored", ["0", "0.00", "-1", "-0.01", "-999999999999999999.99"])
def test_database_check_compares_numerically(two_users, stored):
    with SessionLocal() as db:
        with pytest.raises(IntegrityError, match=CHECK_VIOLATION):
            db.execute(text("INSERT INTO finance_entries (user_id, kind, category, amount, currency, entry_date, "
                            "created_at, updated_at) VALUES (:u, 'expense', 'Food', :a, 'INR', '2026-10-05', "
                            "'2026-10-05', '2026-10-05')"), {"u": two_users[0], "a": stored})
        db.rollback()
        with pytest.raises(IntegrityError, match=CHECK_VIOLATION):
            db.execute(text("INSERT INTO budgets (user_id, category, year, month, amount, currency, created_at, "
                            "updated_at) VALUES (:u, 'Food', 2026, 10, :a, 'INR', '2026-10-05', '2026-10-05')"),
                       {"u": two_users[0], "a": stored})


def test_postgresql_gets_a_real_numeric_column():
    ddl = str(CreateTable(FinanceEntry.__table__).compile(dialect=postgresql.dialect()))
    assert "amount NUMERIC(20, 2) NOT NULL" in ddl and "CHECK (CAST(amount AS NUMERIC) > 0)" in ddl
    ddl = str(CreateTable(Budget.__table__).compile(dialect=postgresql.dialect()))
    assert "amount NUMERIC(20, 2) NOT NULL" in ddl
