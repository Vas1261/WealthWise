"""User-scoped read of the effective holdings (SQLAlchemy only; no calculations)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.domain.holdings import CostDate
from app.domain.portfolio import HoldingPosition
from app.models import Holding, ImportBatch


def get_effective_positions(db: Session, user_id: int) -> list[HoldingPosition]:
    """Holdings from the user's latest snapshot per (broker, asset class).

    The user filter is applied both when choosing each snapshot and to the rows returned, so no
    other user's batch can influence the selection or leak into the result. Positions from different
    brokers stay separate even for the same ISIN.
    """
    latest = (
        select(
            ImportBatch.broker,
            ImportBatch.asset_class,
            func.max(ImportBatch.statement_date).label("statement_date"),
        )
        .where(ImportBatch.user_id == user_id)
        .group_by(ImportBatch.broker, ImportBatch.asset_class)
        .subquery()
    )
    rows = db.execute(
        select(Holding, ImportBatch.statement_date)
        .options(selectinload(Holding.cost_dates))
        .join(ImportBatch, Holding.import_batch_id == ImportBatch.id)
        .join(
            latest,
            (ImportBatch.broker == latest.c.broker)
            & (ImportBatch.asset_class == latest.c.asset_class)
            & (ImportBatch.statement_date == latest.c.statement_date),
        )
        .where(ImportBatch.user_id == user_id)
        .order_by(ImportBatch.broker, ImportBatch.asset_class, Holding.id)
    ).all()
    return [
        HoldingPosition(
            holding_id=holding.id,
            instrument_name=holding.instrument_name,
            isin=holding.isin,
            symbol=holding.symbol,
            asset_class=holding.asset_class,
            source_broker=holding.source_broker,
            native_currency=holding.native_currency,
            quantity=holding.quantity,
            average_buy_price=holding.average_buy_price,
            statement_date=statement_date,
            import_batch_id=holding.import_batch_id,
            cost_dates=tuple(CostDate(d.on_date, d.amount) for d in holding.cost_dates),
        )
        for holding, statement_date in rows
    ]
