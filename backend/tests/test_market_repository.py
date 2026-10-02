"""Market-data repositories against the migrated SQLite test database."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal, engine
from app.domain.market import SymbolCandidate, SymbolChoice, SymbolStatus
from app.models import InstrumentSymbol, MarketPrice
from app.repositories import market_repository

pytestmark = pytest.mark.usefixtures("migrated_db")

NOW = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)
CANDIDATE = SymbolCandidate("SYNTHA", "Synthetic Alpha Ltd", "NSE", "XNSE", "India", "INR", "Common Stock")
ISIN = "INE000D01019"


def test_market_tables_hold_no_user_data():
    for table in ("instrument_symbols", "market_prices"):
        columns = {c["name"] for c in inspect(engine).get_columns(table)}
        assert not any("user" in name for name in columns)
        assert inspect(engine).get_foreign_keys(table) == []


def test_symbol_mapping_round_trip_with_aware_timestamp():
    with SessionLocal() as db:
        market_repository.save_symbol_mapping(db, ISIN, SymbolChoice(SymbolStatus.RESOLVED, CANDIDATE), NOW)
        db.commit()
    with SessionLocal() as db:
        mapping = market_repository.get_symbol_mapping(db, ISIN)
    assert (mapping.status, mapping.symbol, mapping.mic_code, mapping.currency) == (
        SymbolStatus.RESOLVED, "SYNTHA", "XNSE", "INR")
    assert mapping.checked_at == NOW and mapping.checked_at.tzinfo is not None


def test_symbol_mapping_upserts_one_row_per_isin():
    with SessionLocal() as db:
        market_repository.save_symbol_mapping(db, ISIN, SymbolChoice(SymbolStatus.NOT_FOUND), NOW)
        market_repository.save_symbol_mapping(
            db, ISIN, SymbolChoice(SymbolStatus.RESOLVED, CANDIDATE), NOW + timedelta(days=8))
        db.commit()
        rows = db.execute(select(InstrumentSymbol)).scalars().all()
    assert len(rows) == 1 and rows[0].status == "resolved"


def test_failed_lookup_stores_no_listing():
    with SessionLocal() as db:
        market_repository.save_symbol_mapping(db, ISIN, SymbolChoice(SymbolStatus.AMBIGUOUS), NOW)
        db.commit()
        mapping = market_repository.get_symbol_mapping(db, ISIN)
    assert (mapping.status, mapping.symbol, mapping.mic_code) == (SymbolStatus.AMBIGUOUS, None, None)


def test_unknown_isin_returns_none():
    with SessionLocal() as db:
        assert market_repository.get_symbol_mapping(db, "INE999Z01011") is None
        assert market_repository.get_cached_price(db, "NOPE", "XNSE") is None


def test_database_enforces_unique_isin_and_resolved_listing():
    with SessionLocal() as db:
        db.add_all([InstrumentSymbol(isin=ISIN, status="not_found", checked_at=NOW),
                    InstrumentSymbol(isin=ISIN, status="not_found", checked_at=NOW)])
        with pytest.raises(IntegrityError):
            db.commit()
    with SessionLocal() as db:
        db.add(InstrumentSymbol(isin=ISIN, status="resolved", checked_at=NOW))  # resolved without a listing
        with pytest.raises(IntegrityError):
            db.commit()
    with SessionLocal() as db:
        db.add(InstrumentSymbol(isin=ISIN, status="guessed", checked_at=NOW))
        with pytest.raises(IntegrityError):
            db.commit()


def test_price_round_trip_is_exact_and_aware():
    with SessionLocal() as db:
        market_repository.save_price(db, "SYNTHA", "XNSE", Decimal("1234.567891"), "INR", NOW)
        db.commit()
    with SessionLocal() as db:
        cached = market_repository.get_cached_price(db, "SYNTHA", "XNSE")
    assert cached.price == Decimal("1234.567891") and type(cached.price) is Decimal
    assert cached.fetched_at == NOW and cached.fetched_at.tzinfo is not None
    assert cached.currency == "INR"


def test_price_upserts_one_row_per_listing_and_listings_are_separate():
    with SessionLocal() as db:
        market_repository.save_price(db, "SYNTHA", "XNSE", Decimal("10"), "INR", NOW)
        market_repository.save_price(db, "SYNTHA", "XNSE", Decimal("11"), "INR", NOW + timedelta(minutes=11))
        market_repository.save_price(db, "SYNTHA", "XBOM", Decimal("12"), "INR", NOW)
        db.commit()
        rows = db.execute(select(MarketPrice).order_by(MarketPrice.mic_code)).scalars().all()
    assert [(r.mic_code, r.price) for r in rows] == [("XBOM", Decimal("12")), ("XNSE", Decimal("11"))]


def test_database_enforces_unique_listing():
    with SessionLocal() as db:
        db.add_all([MarketPrice(symbol="SYNTHA", mic_code="XNSE", price=Decimal("1"), currency="INR", fetched_at=NOW),
                    MarketPrice(symbol="SYNTHA", mic_code="XNSE", price=Decimal("2"), currency="INR", fetched_at=NOW)])
        with pytest.raises(IntegrityError):
            db.commit()
