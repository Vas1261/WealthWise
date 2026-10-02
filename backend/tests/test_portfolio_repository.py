"""Effective-snapshot selection and user isolation (repository against the migrated test database)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models import Holding
from app.repositories import portfolio_repository
from tests.portfolio_support import make_user, nh, seed

pytestmark = pytest.mark.usefixtures("migrated_db")

D1, D2, D3 = date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30)


@pytest.fixture
def users():
    with SessionLocal() as db:
        return make_user(db, "alice@example.com").id, make_user(db, "bob@example.com").id


def positions(user_id):
    with SessionLocal() as db:
        return portfolio_repository.get_effective_positions(db, user_id)


def test_latest_snapshot_per_broker_and_asset_class(users):
    alice, _ = users
    with SessionLocal() as db:
        seed(db, alice, "zerodha", "equity", D1, [nh("OLDZ")])
        seed(db, alice, "zerodha", "equity", D3, [nh("NEWZ")])
        seed(db, alice, "zerodha", "equity", D2, [nh("MIDZ")])
        seed(db, alice, "groww", "equity", D2, [nh("GROWWEQ", broker="groww", symbol=None)])
        seed(db, alice, "groww", "mutual_fund", D1, [nh("FUND", None, None, asset_class="mutual_fund", broker="groww")])
    result = positions(alice)
    assert [(p.source_broker, p.asset_class, p.instrument_name, p.statement_date) for p in result] == [
        ("groww", "equity", "GROWWEQ", D2),
        ("groww", "mutual_fund", "FUND", D1),  # an older MF date does not hide or get hidden by equity
        ("zerodha", "equity", "NEWZ", D3),
    ]


def test_same_isin_at_two_brokers_stays_two_positions(users):
    alice, _ = users
    with SessionLocal() as db:
        seed(db, alice, "zerodha", "equity", D1, [nh("SYNTHA", qty="1", avg="100")])
        seed(db, alice, "groww", "equity", D1, [nh("Synthetic Alpha Ltd", symbol=None, qty="4", avg="110",
                                                   broker="groww")])
    result = positions(alice)
    assert len(result) == 2 and {p.isin for p in result} == {"INE000D01019"}
    assert {(p.source_broker, p.quantity) for p in result} == {("zerodha", Decimal("1")), ("groww", Decimal("4"))}


def test_duplicate_isin_within_one_snapshot_kept_as_separate_rows(users):
    alice, _ = users
    with SessionLocal() as db:
        seed(db, alice, "zerodha", "equity", D1, [nh(qty="1"), nh(qty="2")])
    assert [p.quantity for p in positions(alice)] == [Decimal("1"), Decimal("2")]


def test_users_are_isolated_in_selection_and_results(users):
    alice, bob = users
    with SessionLocal() as db:
        seed(db, alice, "zerodha", "equity", D1, [nh("ALICE")])
        # Bob's newer snapshot for the same broker + asset class must not hide Alice's or leak into it.
        seed(db, bob, "zerodha", "equity", D3, [nh("BOB")])
    assert [p.instrument_name for p in positions(alice)] == ["ALICE"]
    assert [p.instrument_name for p in positions(bob)] == ["BOB"]


def test_other_user_with_same_statement_date_does_not_leak(users):
    alice, bob = users
    with SessionLocal() as db:
        seed(db, alice, "groww", "equity", D2, [nh("ALICE", broker="groww")])
        seed(db, bob, "groww", "equity", D2, [nh("BOB", broker="groww")])
    assert [p.instrument_name for p in positions(alice)] == ["ALICE"]


def test_user_without_imports_has_no_positions(users):
    assert positions(users[0]) == []


def test_positions_carry_exact_decimals_and_snapshot_details(users):
    alice, _ = users
    with SessionLocal() as db:
        batch_id = seed(db, alice, "zerodha", "equity", D1, [nh(qty="12.345678", avg="0.000001")])
    (p,) = positions(alice)
    assert (p.quantity, p.average_buy_price, p.import_batch_id, p.statement_date) == (
        Decimal("12.345678"), Decimal("0.000001"), batch_id, D1)
    assert type(p.quantity) is Decimal


@pytest.mark.parametrize(("qty", "avg"), [("0", "1"), ("-1", "1"), ("1", "-0.5")])
def test_database_rejects_invalid_quantities_and_prices(users, qty, avg):
    alice, _ = users
    with SessionLocal() as db:
        batch_id = seed(db, alice, "zerodha", "equity", D1, [nh()])
        db.add(Holding(import_batch_id=batch_id, instrument_name="X", isin=None, symbol=None, asset_class="equity",
                       quantity=Decimal(qty), average_buy_price=Decimal(avg), native_currency="INR",
                       source_broker="zerodha"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_database_accepts_zero_average_buy_price(users):
    alice, _ = users
    with SessionLocal() as db:
        seed(db, alice, "zerodha", "equity", D1, [nh(avg="0")])
    assert positions(alice)[0].average_buy_price == Decimal("0")
