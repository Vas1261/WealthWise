"""Authentication rules as pure functions (no DB, HTTP, or framework imports)."""

import hashlib
import hmac
from datetime import datetime, timedelta

ACCESS_TOKEN_LIFETIME = timedelta(minutes=15)
REFRESH_TOKEN_LIFETIME = timedelta(days=7)

# bcrypt only uses the first 72 bytes of a password, so longer ones are rejected, not truncated.
MIN_PASSWORD_BYTES = 8
MAX_PASSWORD_BYTES = 72


class PasswordPolicyError(ValueError):
    pass


def password_within_bcrypt_limit(password: str) -> bool:
    return len(password.encode("utf-8")) <= MAX_PASSWORD_BYTES


def validate_password(password: str) -> None:
    size = len(password.encode("utf-8"))
    if not MIN_PASSWORD_BYTES <= size <= MAX_PASSWORD_BYTES:
        raise PasswordPolicyError(
            f"Password must be between {MIN_PASSWORD_BYTES} and {MAX_PASSWORD_BYTES} bytes (UTF-8)"
        )


def normalize_email(email: str) -> str:
    return email.strip().lower()


def access_token_claims(user_id: int, session_id: str, now: datetime) -> dict:
    return {
        "sub": str(user_id),
        "sid": session_id,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + ACCESS_TOKEN_LIFETIME).timestamp()),
    }


def refresh_token_expiry(now: datetime) -> datetime:
    return now + REFRESH_TOKEN_LIFETIME


def compose_refresh_token(session_id: str, secret: str) -> str:
    return f"{session_id}.{secret}"


def session_id_from_refresh_token(token: str) -> str | None:
    session_id, sep, secret = token.partition(".")
    return session_id if sep and session_id and secret else None


def hash_refresh_token(token: str) -> str:
    # Refresh tokens are high-entropy random values, so a fast hash is sufficient (unlike passwords).
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_tokens_match(cookie_token: str | None, header_token: str | None) -> bool:
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token.encode("utf-8"), header_token.encode("utf-8"))
