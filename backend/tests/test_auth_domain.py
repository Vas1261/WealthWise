from datetime import UTC, datetime, timedelta

import pytest

from app.domain import auth as rules


@pytest.mark.parametrize("password", ["12345678", "x" * 72, "éééé", "é" * 36])  # é is 2 bytes in UTF-8
def test_password_accepted_within_8_to_72_utf8_bytes(password):
    rules.validate_password(password)


@pytest.mark.parametrize("password", ["1234567", "x" * 73, "ééé", "é" * 37])
def test_password_rejected_outside_8_to_72_utf8_bytes(password):
    with pytest.raises(rules.PasswordPolicyError):
        rules.validate_password(password)


def test_byte_limit_not_character_limit():
    password = "é" * 37  # 37 characters, 74 bytes
    assert len(password) < rules.MAX_PASSWORD_BYTES
    assert not rules.password_within_bcrypt_limit(password)


def test_normalize_email_trims_and_lowercases():
    assert rules.normalize_email("  Alice@Example.COM ") == "alice@example.com"


def test_access_token_claims():
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    claims = rules.access_token_claims(42, "sid-1", now)
    assert claims == {
        "sub": "42",
        "sid": "sid-1",
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }


def test_refresh_token_expires_after_7_days():
    now = datetime(2026, 10, 2, tzinfo=UTC)
    assert rules.refresh_token_expiry(now) == now + timedelta(days=7)


def test_refresh_token_round_trip():
    token = rules.compose_refresh_token("abc-123", "s3cr3t")
    assert rules.session_id_from_refresh_token(token) == "abc-123"


@pytest.mark.parametrize("token", ["", "nodot", ".secret", "sid."])
def test_malformed_refresh_token_has_no_session_id(token):
    assert rules.session_id_from_refresh_token(token) is None


def test_refresh_token_hash_is_stable_and_not_the_token():
    token = rules.compose_refresh_token("abc", "secret")
    assert rules.hash_refresh_token(token) == rules.hash_refresh_token(token)
    assert rules.hash_refresh_token(token) != token
    assert len(rules.hash_refresh_token(token)) == 64


@pytest.mark.parametrize(
    ("cookie", "header", "expected"),
    [("tok", "tok", True), ("tok", "other", False), (None, "tok", False), ("tok", None, False), ("", "", False)],
)
def test_csrf_tokens_match(cookie, header, expected):
    assert rules.csrf_tokens_match(cookie, header) is expected
