from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import ASSET_CLASSES, BROKERS, Base, utcnow

# Outcome at the time of the import:
#   current  - stored and became the effective snapshot for its broker + asset class
#   history  - stored, but an equal-or-newer statement was already current (never effective)
#   failed   - recognised statement with row errors and no valid rows: no snapshot stored
#   rejected - not a supported statement (unsupported, ambiguous or invalid file): nothing stored
IMPORT_RESULTS = ("current", "history", "failed", "rejected")


class ImportAttempt(Base):
    """Import history: one row per import attempt, including failed and rejected ones.

    Separate from ImportBatch, which holds only successful holdings snapshots under the unique
    snapshot identity; an attempt never occupies or replaces a snapshot. batch_id links a stored
    attempt to its snapshot and becomes NULL when a later re-import replaces that snapshot.
    """

    __tablename__ = "import_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("import_batches.id", ondelete="SET NULL"))
    result: Mapped[str] = mapped_column(
        Enum(*IMPORT_RESULTS, name="import_result", native_enum=False, create_constraint=True)
    )
    broker: Mapped[str | None] = mapped_column(
        Enum(*BROKERS, name="broker", native_enum=False, create_constraint=True)
    )
    asset_class: Mapped[str | None] = mapped_column(
        Enum(*ASSET_CLASSES, name="asset_class", native_enum=False, create_constraint=True)
    )
    statement_date: Mapped[date | None] = mapped_column(Date)
    file_name: Mapped[str] = mapped_column(String(255))
    rows_succeeded: Mapped[int] = mapped_column(default=0)
    rows_failed: Mapped[int] = mapped_column(default=0)
    replaced_existing: Mapped[bool] = mapped_column(default=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
