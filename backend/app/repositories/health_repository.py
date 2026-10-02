from sqlalchemy import select
from sqlalchemy.orm import Session


def database_is_reachable(session: Session) -> bool:
    return session.execute(select(1)).scalar_one() == 1
