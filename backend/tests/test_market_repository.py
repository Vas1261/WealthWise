"""Market price cache repository against the migrated SQLite test database."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal, engine
from app.models import MarketPrice
from app.repositories import market_repository

pytestmark = pytest.mark.usefixtures("migrated_db")

NOW = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)
ISIN_A, ISIN_B = "INE000D01019", "INE000E01025"


def test_price_table_holds_no_user_data():
    columns = {c["name"] for c in inspect(engine).get_columns("market_prices")}
    assert not any("user" in name for name in columns)
    assert inspect(engine).get_foreign_keys("market_prices") == []


def test_price_round_trip_is_exact_and_aware():
    with SessionLocal() as db:
        market_repository.save_price(db, ISIN_A, "NSE", "SYNTHA", Decimal("1234.567891"), "INR", NOW)
        db.commit()
    with SessionLocal() as db:
        cached = market_repository.get_cached_prices(db, [ISIN_A])[ISIN_A]
    assert (cached.isin, cached.exchange, cached.trading_symbol, cached.currency) == (ISIN_A, "NSE", "SYNTHA", "INR")
    assert cached.price == Decimal("1234.567891") and type(cached.price) is Decimal
    assert cached.fetched_at == NOW and cached.fetched_at.tzinfo is not None


def test_price_upserts_one_row_per_isin():
    with SessionLocal() as db:
        market_repository.save_price(db, ISIN_A, "NSE", "SYNTHA", Decimal("10"), "INR", NOW)
        market_repository.save_price(db, ISIN_A, "BSE", None, Decimal("11"), "INR", NOW + timedelta(minutes=11))
        market_repository.save_price(db, ISIN_B, "BSE", "SYNTHB", Decimal("12"), "INR", NOW)
        db.commit()
        rows = db.execute(select(MarketPrice).order_by(MarketPrice.isin)).scalars().all()
    assert [(r.isin, r.exchange, r.trading_symbol, r.price) for r in rows] == [
        (ISIN_A, "BSE", None, Decimal("11")), (ISIN_B, "BSE", "SYNTHB", Decimal("12"))]


def test_get_cached_prices_returns_only_known_isins():
    with SessionLocal() as db:
        market_repository.save_price(db, ISIN_A, "NSE", "SYNTHA", Decimal("10"), "INR", NOW)
        db.commit()
        assert set(market_repository.get_cached_prices(db, [ISIN_A, ISIN_B, ISIN_A])) == {ISIN_A}
        assert market_repository.get_cached_prices(db, []) == {}


def test_database_enforces_one_price_per_isin():
    with SessionLocal() as db:
        db.add_all([MarketPrice(isin=ISIN_A, exchange="NSE", price=Decimal("1"), currency="INR", fetched_at=NOW),
                    MarketPrice(isin=ISIN_A, exchange="BSE", price=Decimal("2"), currency="INR", fetched_at=NOW)])
        with pytest.raises(IntegrityError):
            db.commit()
