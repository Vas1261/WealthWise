import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache

import bcrypt
import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain import auth as rules
from app.models import User
from app.repositories import session_repository, user_repository

JWT_ALGORITHM = "HS256"


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class NotAuthenticated(Exception):
    pass


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str


@dataclass(frozen=True)
class AuthContext:
    user: User
    session_id: str


def _now() -> datetime:
    return datetime.now(UTC)


@cache
def _dummy_password_hash() -> bytes:
    # Compared against when the email is unknown, so response time doesn't reveal registered emails.
    return bcrypt.hashpw(b"timing-equalizer", bcrypt.gensalt())


def register(db: Session, email: str, password: str) -> User:
    rules.validate_password(password)
    email = rules.normalize_email(email)
    if user_repository.get_by_email(db, email) is not None:
        raise EmailAlreadyRegistered
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    try:
        user = user_repository.create(db, email, password_hash)
        db.commit()
    except IntegrityError:  # concurrent registration of the same email
        db.rollback()
        raise EmailAlreadyRegistered from None
    return user


def login(db: Session, email: str, password: str) -> tuple[User, IssuedTokens]:
    user = user_repository.get_by_email(db, rules.normalize_email(email))
    if not rules.password_within_bcrypt_limit(password):
        raise InvalidCredentials
    stored_hash = user.password_hash.encode("ascii") if user else _dummy_password_hash()
    if not bcrypt.checkpw(password.encode("utf-8"), stored_hash) or user is None:
        raise InvalidCredentials

    now = _now()
    session_id = str(uuid.uuid4())
    refresh_token = rules.compose_refresh_token(session_id, secrets.token_urlsafe(32))
    session_repository.create(
        db, session_id, user.id, rules.hash_refresh_token(refresh_token), rules.refresh_token_expiry(now)
    )
    db.commit()
    return user, IssuedTokens(_encode_access_token(user.id, session_id, now), refresh_token)


def refresh(db: Session, refresh_token: str) -> IssuedTokens:
    """Rotate the refresh token. Presenting a stale token revokes the whole session (replay detection)."""
    session_id = rules.session_id_from_refresh_token(refresh_token)
    if session_id is None:
        raise NotAuthenticated

    now = _now()
    new_refresh_token = rules.compose_refresh_token(session_id, secrets.token_urlsafe(32))
    rotated = session_repository.rotate(
        db,
        session_id,
        current_hash=rules.hash_refresh_token(refresh_token),
        new_hash=rules.hash_refresh_token(new_refresh_token),
        new_expires_at=rules.refresh_token_expiry(now),
        now=now,
    )
    if not rotated:
        session_repository.revoke(db, session_id, now)
        db.commit()
        raise NotAuthenticated

    auth_session = session_repository.get_active(db, session_id, now)
    db.commit()
    return IssuedTokens(_encode_access_token(auth_session.user_id, session_id, now), new_refresh_token)


def logout(db: Session, session_id: str) -> None:
    session_repository.revoke(db, session_id, _now())
    db.commit()


def authenticate(db: Session, access_token: str) -> AuthContext:
    """Resolve the user from a signed access token whose session is still active."""
    try:
        claims = jwt.decode(
            access_token,
            settings.jwt_secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "sid", "iat", "exp"]},
        )
    except jwt.InvalidTokenError:
        raise NotAuthenticated from None
    if claims.get("type") != "access":
        raise NotAuthenticated

    auth_session = session_repository.get_active(db, claims["sid"], _now())
    if auth_session is None or str(auth_session.user_id) != claims["sub"]:
        raise NotAuthenticated
    user = user_repository.get_by_id(db, auth_session.user_id)
    if user is None:
        raise NotAuthenticated
    return AuthContext(user=user, session_id=auth_session.id)


def _encode_access_token(user_id: int, session_id: str, now: datetime) -> str:
    return jwt.encode(rules.access_token_claims(user_id, session_id, now), settings.jwt_secret, JWT_ALGORITHM)
