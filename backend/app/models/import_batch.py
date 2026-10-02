import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import ASSET_CLASSES, BROKERS, Base, utcnow


class ImportBatch(Base):
    """One holdings snapshot; doubles as the import-history record.

    Snapshot identity (user_id, broker, asset_class, statement_date) is unique.
    The current snapshot for a (user_id, broker, asset_class) is the batch with the
    latest statement_date; superseding rules are enforced by the import service.
    """

    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "broker", "asset_class", "statement_date", name="uq_import_batches_snapshot_identity"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    broker: Mapped[str] = mapped_column(Enum(*BROKERS, name="broker", native_enum=False, create_constraint=True))
    asset_class: Mapped[str] = mapped_column(
        Enum(*ASSET_CLASSES, name="asset_class", native_enum=False, create_constraint=True)
    )
    statement_date: Mapped[date] = mapped_column(Date)
    file_name: Mapped[str] = mapped_column(String(255))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    rows_succeeded: Mapped[int] = mapped_column(default=0)
    rows_failed: Mapped[int] = mapped_column(default=0)

    holdings: Mapped[list["Holding"]] = relationship(back_populates="import_batch", cascade="all, delete-orphan")
