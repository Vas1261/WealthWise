"""Persistence of imported holdings snapshots (SQLAlchemy only; no parsing or business rules)."""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.holdings import NormalizedHolding
from app.models import Holding, ImportAttempt, ImportBatch


def get_batch(db: Session, user_id: int, broker: str, asset_class: str, statement_date: date) -> ImportBatch | None:
    """The batch with exactly this snapshot identity (user + broker + asset class + statement date)."""
    return db.execute(
        select(ImportBatch).where(
            ImportBatch.user_id == user_id,
            ImportBatch.broker == broker,
            ImportBatch.asset_class == asset_class,
            ImportBatch.statement_date == statement_date,
        )
    ).scalar_one_or_none()


def latest_statement_date(db: Session, user_id: int, broker: str, asset_class: str) -> date | None:
    return db.execute(
        select(func.max(ImportBatch.statement_date)).where(
            ImportBatch.user_id == user_id,
            ImportBatch.broker == broker,
            ImportBatch.asset_class == asset_class,
        )
    ).scalar_one_or_none()


def delete_batch(db: Session, batch: ImportBatch) -> None:
    db.delete(batch)  # holdings are removed with it (cascade)
    db.flush()


def create_batch(
    db: Session,
    *,
    batch_id: str,
    user_id: int,
    broker: str,
    asset_class: str,
    statement_date: date,
    file_name: str,
    rows_succeeded: int,
    rows_failed: int,
    holdings: Sequence[NormalizedHolding],
) -> ImportBatch:
    batch = ImportBatch(
        id=batch_id,
        user_id=user_id,
        broker=broker,
        asset_class=asset_class,
        statement_date=statement_date,
        file_name=file_name,
        rows_succeeded=rows_succeeded,
        rows_failed=rows_failed,
    )
    batch.holdings = [
        Holding(
            instrument_name=h.instrument_name,
            isin=h.isin,
            symbol=h.symbol,
            asset_class=h.asset_class,
            quantity=h.quantity,
            average_buy_price=h.average_buy_price,
            native_currency=h.native_currency,
            source_broker=h.source_broker,
        )
        for h in holdings
    ]
    db.add(batch)
    db.flush()
    return batch


def record_attempt(
    db: Session,
    *,
    user_id: int,
    result: str,
    file_name: str,
    broker: str | None = None,
    asset_class: str | None = None,
    statement_date: date | None = None,
    rows_succeeded: int = 0,
    rows_failed: int = 0,
    replaced_existing: bool = False,
    batch_id: str | None = None,
) -> ImportAttempt:
    """Append one row to the user's import history (never touches the snapshot table)."""
    attempt = ImportAttempt(
        user_id=user_id,
        result=result,
        file_name=file_name,
        broker=broker,
        asset_class=asset_class,
        statement_date=statement_date,
        rows_succeeded=rows_succeeded,
        rows_failed=rows_failed,
        replaced_existing=replaced_existing,
        batch_id=batch_id,
    )
    db.add(attempt)
    db.flush()
    return attempt


def list_attempts(db: Session, user_id: int) -> list[ImportAttempt]:
    return list(
        db.execute(
            select(ImportAttempt).where(ImportAttempt.user_id == user_id).order_by(ImportAttempt.id)
        ).scalars()
    )
