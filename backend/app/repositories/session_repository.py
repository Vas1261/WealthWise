from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AuthSession

# Expiry and revocation are compared in SQL against an explicit `now`, so results do not
# depend on how the database driver returns timezone information.


def _active(now: datetime):
    return (AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)


def create(db: Session, session_id: str, user_id: int, refresh_token_hash: str, expires_at: datetime) -> AuthSession:
    auth_session = AuthSession(
        id=session_id, user_id=user_id, refresh_token_hash=refresh_token_hash, expires_at=expires_at
    )
    db.add(auth_session)
    db.flush()
    return auth_session


def get_active(db: Session, session_id: str, now: datetime) -> AuthSession | None:
    return db.execute(
        select(AuthSession).where(AuthSession.id == session_id, *_active(now))
    ).scalar_one_or_none()


def rotate(
    db: Session, session_id: str, current_hash: str, new_hash: str, new_expires_at: datetime, now: datetime
) -> bool:
    """Atomically swap the refresh token hash; only one caller can win for a given current_hash."""
    result = db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.refresh_token_hash == current_hash, *_active(now))
        .values(refresh_token_hash=new_hash, expires_at=new_expires_at)
    )
    return result.rowcount == 1


def revoke(db: Session, session_id: str, now: datetime) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
