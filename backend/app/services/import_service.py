"""Import persistence (Module 7): broker file -> adapter -> holdings snapshot + import history.

Parsing is entirely the adapters' job; this service selects the adapter by content and applies the
snapshot rules (decision B2) for user + broker + asset class + statement date:
- the same identity imported again replaces that snapshot;
- a newer statement becomes the current snapshot (the portfolio reads the latest per broker and
  asset class); an older statement is kept as history but never becomes current;
- a statement with row errors and no valid rows is never stored as a snapshot, so it can never
  replace or become holdings. A valid but empty statement (e.g. every fund redeemed) is a real
  snapshot and is stored.
Every attempt, including failed and rejected ones, is recorded in import history (ImportAttempt),
which is separate from the snapshot table. The user id always comes from the authenticated caller.
"""

import uuid
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from pathlib import PureWindowsPath
from typing import NoReturn

from sqlalchemy.orm import Session

from app.adapters.base import BrokerAdapter, StatementFormatError
from app.adapters.groww_equity import GrowwEquityAdapter
from app.adapters.groww_mutual_fund import GrowwMutualFundAdapter
from app.adapters.zerodha_equity import ZerodhaEquityAdapter
from app.domain.holdings import ASSET_CLASS_EQUITY, ASSET_CLASS_MUTUAL_FUND, RowError
from app.models import ImportAttempt
from app.repositories import import_repository

# Each adapter handles exactly one broker + asset class.
ADAPTERS: tuple[tuple[BrokerAdapter, str, str], ...] = (
    (GrowwEquityAdapter(), "groww", ASSET_CLASS_EQUITY),
    (ZerodhaEquityAdapter(), "zerodha", ASSET_CLASS_EQUITY),
    (GrowwMutualFundAdapter(), "groww", ASSET_CLASS_MUTUAL_FUND),
)
MAX_FILE_NAME_LENGTH = 255


class ImportResult(StrEnum):
    CURRENT = "current"  # stored; now the effective snapshot for its broker + asset class
    HISTORY = "history"  # stored; an equal-or-newer statement is current, so never effective
    FAILED = "failed"  # row errors and no valid rows: recorded in history, no snapshot stored
    REJECTED = "rejected"  # not a supported statement: recorded in history, nothing else stored


class RejectionReason(StrEnum):
    UNSUPPORTED = "unsupported_statement"
    AMBIGUOUS = "ambiguous_statement"
    INVALID = "invalid_statement"


class StatementRejectedError(Exception):
    """The file is not exactly one valid supported statement. Raised after recording the attempt."""

    def __init__(self, reason: RejectionReason, message: str, attempt_id: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.attempt_id = attempt_id


@dataclass(frozen=True)
class ImportOutcome:
    result: ImportResult
    broker: str
    asset_class: str
    statement_date: date
    file_name: str
    rows_succeeded: int
    rows_failed: int
    errors: list[RowError] = field(default_factory=list)
    replaced_existing: bool = False  # an earlier import of the same snapshot identity was replaced
    batch_id: str | None = None
    attempt_id: int | None = None

    @property
    def stored(self) -> bool:
        return self.result in (ImportResult.CURRENT, ImportResult.HISTORY)

    @property
    def current(self) -> bool:
        return self.result == ImportResult.CURRENT


def import_statement(db: Session, user_id: int, file_path: str, file_name: str) -> ImportOutcome:
    name = safe_file_name(file_name)
    try:
        adapter, broker, asset_class = _select_adapter(file_path)
    except StatementRejectedError as rejection:
        _reject(db, user_id, name, rejection)

    batch_id = str(uuid.uuid4())
    try:
        parsed = adapter.parse(file_path, batch_id)
    except StatementFormatError as error:
        _reject(db, user_id, name, StatementRejectedError(RejectionReason.INVALID, str(error)), broker, asset_class)

    outcome = ImportOutcome(
        result=ImportResult.FAILED,
        broker=broker,
        asset_class=asset_class,
        statement_date=parsed.statement_date,
        file_name=name,
        rows_succeeded=len(parsed.holdings),
        rows_failed=parsed.failed_row_count,
        errors=list(parsed.errors),
    )
    if not parsed.holdings and parsed.errors:  # completely failed: history only, never a snapshot
        attempt = _record(db, user_id, outcome)
        db.commit()
        return replace(outcome, attempt_id=attempt.id)

    existing = import_repository.get_batch(db, user_id, broker, asset_class, parsed.statement_date)
    if existing is not None:
        import_repository.delete_batch(db, existing)
    latest = import_repository.latest_statement_date(db, user_id, broker, asset_class)
    import_repository.create_batch(
        db,
        batch_id=batch_id,
        user_id=user_id,
        broker=broker,
        asset_class=asset_class,
        statement_date=parsed.statement_date,
        file_name=name,
        rows_succeeded=outcome.rows_succeeded,
        rows_failed=outcome.rows_failed,
        holdings=parsed.holdings,
    )
    is_current = latest is None or parsed.statement_date >= latest
    outcome = replace(
        outcome,
        result=ImportResult.CURRENT if is_current else ImportResult.HISTORY,
        replaced_existing=existing is not None,
        batch_id=batch_id,
    )
    attempt = _record(db, user_id, outcome)
    db.commit()
    return replace(outcome, attempt_id=attempt.id)


def list_history(db: Session, user_id: int) -> list[ImportAttempt]:
    """The user's import attempts, newest first (read-only; Module 10 import history)."""
    return list(reversed(import_repository.list_attempts(db, user_id)))


def safe_file_name(file_name: str | None) -> str:
    """Only the base name of a client-supplied file name; never a path (splits on both / and \\)."""
    return PureWindowsPath(file_name or "").name[:MAX_FILE_NAME_LENGTH]


def _record(db: Session, user_id: int, outcome: ImportOutcome) -> ImportAttempt:
    return import_repository.record_attempt(
        db,
        user_id=user_id,
        result=outcome.result.value,
        file_name=outcome.file_name,
        broker=outcome.broker,
        asset_class=outcome.asset_class,
        statement_date=outcome.statement_date,
        rows_succeeded=outcome.rows_succeeded,
        rows_failed=outcome.rows_failed,
        replaced_existing=outcome.replaced_existing,
        batch_id=outcome.batch_id,
    )


def _reject(
    db: Session,
    user_id: int,
    file_name: str,
    rejection: StatementRejectedError,
    broker: str | None = None,
    asset_class: str | None = None,
) -> NoReturn:
    attempt = import_repository.record_attempt(
        db, user_id=user_id, result=ImportResult.REJECTED.value, file_name=file_name,
        broker=broker, asset_class=asset_class,
    )
    db.commit()
    raise StatementRejectedError(rejection.reason, str(rejection), attempt.id) from None


def _select_adapter(file_path: str) -> tuple[BrokerAdapter, str, str]:
    """Route by content (spec 4.2): exactly one adapter must recognise the file."""
    matches = [entry for entry in ADAPTERS if entry[0].can_parse(file_path)]
    if not matches:
        raise StatementRejectedError(
            RejectionReason.UNSUPPORTED, "The file is not a supported Groww or Zerodha statement"
        )
    if len(matches) > 1:
        raise StatementRejectedError(RejectionReason.AMBIGUOUS, "The file matches more than one statement format")
    return matches[0]
