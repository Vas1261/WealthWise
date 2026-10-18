from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.repositories import health_repository


def get_health(session: Session) -> dict:
    """ok only when the database answers and its schema is the one this code expects.

    A database that is behind the migrations (new code, `alembic upgrade head` not run yet) answers
    queries but lacks tables, so every route that needs them fails; report that instead of "ok".
    """
    try:
        database_ok = health_repository.database_is_reachable(session)
        applied = health_repository.applied_revision(session) if database_ok else None
    except SQLAlchemyError:
        database_ok, applied = False, None
    if not database_ok:
        return {"status": "degraded", "database": "unreachable"}
    expected = health_repository.latest_revision()
    if expected is not None and applied != expected:
        return {"status": "degraded", "database": "migration_pending"}
    return {"status": "ok", "database": "ok"}
