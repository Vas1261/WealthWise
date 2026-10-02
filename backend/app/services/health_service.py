from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.repositories import health_repository


def get_health(session: Session) -> dict:
    try:
        database_ok = health_repository.database_is_reachable(session)
    except SQLAlchemyError:
        database_ok = False
    return {"status": "ok" if database_ok else "degraded", "database": "ok" if database_ok else "unreachable"}
