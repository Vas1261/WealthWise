from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal, engine
from app.models import Holding, ImportBatch, User

EXPECTED_TABLES = {
    "users", "import_batches", "import_attempts", "holdings", "sessions", "market_prices",
    "finance_entries", "budgets", "holding_cost_dates", "historical_fx_rates",
}
PINNING_TABLES = {"holding_cost_dates", "historical_fx_rates"}  # 0008


def table_names() -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def test_upgrade_creates_expected_tables(migrated_db):
    assert table_names() == EXPECTED_TABLES


def test_downgrade_upgrade_round_trip(alembic_config):
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    assert table_names() == set()
    command.upgrade(alembic_config, "head")
    assert table_names() == EXPECTED_TABLES
    command.downgrade(alembic_config, "base")


def test_finance_migration_downgrades_to_0005_alone(alembic_config):
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0005")
    # 0005 still had the Twelve Data symbol cache (dropped again by 0007).
    assert table_names() == EXPECTED_TABLES - {"finance_entries", "budgets"} - PINNING_TABLES | {"instrument_symbols"}
    command.upgrade(alembic_config, "head")
    assert table_names() == EXPECTED_TABLES
    command.downgrade(alembic_config, "base")


def test_upstox_price_cache_migration_replaces_the_twelve_data_caches(alembic_config):
    command.upgrade(alembic_config, "0006")
    assert {"instrument_symbols", "market_prices"} <= table_names()
    assert {c["name"] for c in inspect(engine).get_columns("market_prices")} >= {"symbol", "mic_code"}
    command.upgrade(alembic_config, "head")
    assert "instrument_symbols" not in table_names()
    assert {c["name"] for c in inspect(engine).get_columns("market_prices")} == {
        "id", "isin", "exchange", "trading_symbol", "price", "currency", "fetched_at"}
    command.downgrade(alembic_config, "0006")
    assert {"instrument_symbols", "market_prices"} <= table_names()
    command.downgrade(alembic_config, "base")


def test_models_match_migrations(migrated_db, alembic_config):
    # Fails if the ORM models have drifted from the migration history.
    command.check(alembic_config)


def test_holding_round_trips_decimals_exactly(migrated_db):
    with SessionLocal() as session:
        user = User(email="a@example.com", password_hash="x")
        session.add(user)
        session.flush()
        batch = ImportBatch(
            user_id=user.id, broker="zerodha", asset_class="equity", statement_date=date(2026, 9, 2), file_name="h.xlsx"
        )
        batch.holdings.append(
            Holding(
                instrument_name="SYNTHETIC CORP", isin="INE000C01018", symbol="SYNTH", asset_class="equity",
                quantity=Decimal("17.583"), average_buy_price=Decimal("528.6173"),
                native_currency="INR", source_broker="zerodha",
            )
        )
        session.add(batch)
        session.commit()

    with SessionLocal() as session:
        holding = session.query(Holding).one()
        assert holding.quantity == Decimal("17.583")
        assert holding.average_buy_price == Decimal("528.6173")
        assert user.reporting_currency == "INR"


def test_rejects_unknown_broker(migrated_db):
    with SessionLocal() as session:
        user = User(email="b@example.com", password_hash="x")
        session.add(user)
        session.flush()
        session.add(ImportBatch(
            user_id=user.id, broker="upstox", asset_class="equity", statement_date=date(2026, 9, 2), file_name="x.xlsx"
        ))
        with pytest.raises(IntegrityError):
            session.commit()


def _batch(user_id: int, asset_class: str = "equity", statement_date: date = date(2026, 9, 1)) -> ImportBatch:
    return ImportBatch(
        user_id=user_id, broker="groww", asset_class=asset_class, statement_date=statement_date, file_name="f.xlsx"
    )


def test_snapshot_identity_is_unique(migrated_db):
    with SessionLocal() as session:
        user = User(email="c@example.com", password_hash="x")
        session.add(user)
        session.flush()
        session.add_all([_batch(user.id), _batch(user.id)])
        with pytest.raises(IntegrityError, match="UNIQUE"):
            session.commit()


def test_snapshot_identity_allows_other_asset_class_and_dates(migrated_db):
    with SessionLocal() as session:
        user = User(email="d@example.com", password_hash="x")
        session.add(user)
        session.flush()
        session.add_all([
            _batch(user.id, "equity"),
            _batch(user.id, "mutual_fund"),  # Groww equity and Groww MF are separate snapshots
            _batch(user.id, "equity", date(2026, 10, 1)),  # newer statement for the same broker/asset class
        ])
        session.commit()
        assert session.query(ImportBatch).count() == 3
