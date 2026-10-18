from functools import cache
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import select
from sqlalchemy.orm import Session

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "alembic"


def database_is_reachable(session: Session) -> bool:
    return session.execute(select(1)).scalar_one() == 1


def applied_revision(session: Session) -> str | None:
    """The Alembic revision the database is at (None before any migration has run)."""
    return MigrationContext.configure(session.connection()).get_current_revision()


@cache
def latest_revision() -> str | None:
    """The newest revision shipped with this code, or None when the migration scripts are not deployed."""
    try:
        return ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    except CommandError:
        return None
