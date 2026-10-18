from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.config import Settings, check_app_settings, settings
from app.core.database import SessionLocal
from app.domain.auth import hash_refresh_token
from app.main import app
from app.models import AuthSession, User

PASSWORD = "correct horse battery"
ACCESS, REFRESH, CSRF = settings.access_cookie_name, settings.refresh_cookie_name, settings.csrf_cookie_name

pytestmark = pytest.mark.usefixtures("migrated_db")


@pytest.fixture
def client():
    return TestClient(app)


def csrf_headers(client: TestClient) -> dict:
    if CSRF not in client.cookies:
        assert client.get("/api/auth/csrf").status_code == 204
    return {settings.csrf_header_name: client.cookies[CSRF]}


def register(client: TestClient, email="alice@example.com", password=PASSWORD):
    return client.post("/api/auth/register", json={"email": email, "password": password}, headers=csrf_headers(client))


def login(client: TestClient, email="alice@example.com", password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers=csrf_headers(client))


def post_with_cookies(path: str, cookies: dict):
    """POST from a fresh client carrying exactly the given cookies (plus a valid CSRF pair)."""
    cookies = {CSRF: "csrf-token", **cookies}
    header = "; ".join(f"{name}={value}" for name, value in cookies.items())
    return TestClient(app).post(path, headers={"Cookie": header, settings.csrf_header_name: cookies[CSRF]})


def get_with_cookies(path: str, cookies: dict):
    header = "; ".join(f"{name}={value}" for name, value in cookies.items())
    return TestClient(app).get(path, headers={"Cookie": header})


def set_cookie_headers(response) -> dict[str, str]:
    return {h.split("=", 1)[0]: h for h in response.headers.get_list("set-cookie")}


def logged_in_client(email="alice@example.com") -> TestClient:
    c = TestClient(app)
    assert register(c, email).status_code == 201
    assert login(c, email).status_code == 200
    return c


# 1. Registration
def test_register_creates_user(client):
    response = register(client, "  Alice@Example.COM ")
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "alice@example.com"
    assert body["reporting_currency"] == "INR"
    assert set(body) == {"id", "email", "reporting_currency"}  # never the password hash
    assert ACCESS not in client.cookies  # registration does not log in


def test_register_rejects_invalid_email_and_weak_password(client):
    assert register(client, email="not-an-email").status_code == 422
    rejected = register(client, email="someone@gmail.c")  # one-letter ending: a typo for .com
    assert rejected.status_code == 422 and rejected.json()["detail"][0]["loc"] == ["body", "email"]
    assert register(client, email="someone@gmail.co").status_code == 201
    response = register(client, password="short")
    assert response.status_code == 422
    assert "bytes" in response.json()["detail"]
    assert register(client, password="é" * 37).status_code == 422  # 37 chars but 74 UTF-8 bytes


# 2. Duplicate email
def test_duplicate_email_rejected_case_insensitively(client):
    assert register(client, "alice@example.com").status_code == 201
    response = register(client, "ALICE@example.com")
    assert response.status_code == 409
    assert response.json() == {"detail": "An account with this email already exists"}


# 3. Password stored hashed
def test_password_stored_as_bcrypt_hash(client):
    register(client)
    with SessionLocal() as db:
        user = db.execute(select(User)).scalar_one()
    assert user.password_hash != PASSWORD
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$2b$")
    assert bcrypt.checkpw(PASSWORD.encode(), user.password_hash.encode())


# 4. Login
def test_login_sets_cookies_with_expected_flags(client):
    register(client)
    response = login(client, "ALICE@example.com")
    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"
    assert "token" not in response.text.lower()  # tokens only travel in cookies

    cookies = set_cookie_headers(response)
    access, refresh, csrf = cookies[ACCESS], cookies[REFRESH], cookies[CSRF]
    assert "HttpOnly" in access and "HttpOnly" in refresh
    assert "HttpOnly" not in csrf
    for header in (access, refresh, csrf):
        assert "samesite=lax" in header.lower()
    assert "Max-Age=900" in access
    assert "Max-Age=604800" in refresh
    assert "Path=/api/auth" in refresh


def test_secure_flag_follows_configuration(client, monkeypatch):
    register(client)
    monkeypatch.setattr(settings, "cookie_secure", True)
    for header in set_cookie_headers(login(client)).values():
        assert "Secure" in header


def test_session_stores_only_refresh_token_hash(client):
    register(client)
    login(client)
    refresh_token = client.cookies[REFRESH]
    with SessionLocal() as db:
        auth_session = db.execute(select(AuthSession)).scalar_one()
    assert auth_session.refresh_token_hash == hash_refresh_token(refresh_token)
    assert refresh_token not in auth_session.refresh_token_hash


# 5. Invalid credentials
@pytest.mark.parametrize(
    ("email", "password"),
    [("alice@example.com", "wrong password"), ("nobody@example.com", PASSWORD), ("alice@example.com", "x" * 100)],
)
def test_invalid_credentials_rejected_generically(client, email, password):
    register(client)
    response = login(client, email, password)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}
    assert ACCESS not in client.cookies


# 6. Access authentication
def test_me_returns_authenticated_user():
    c = logged_in_client()
    response = c.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"


def test_access_token_claims_and_lifetime():
    c = logged_in_client()
    claims = jwt.decode(c.cookies[ACCESS], settings.jwt_secret, algorithms=["HS256"])
    assert set(claims) == {"sub", "sid", "type", "iat", "exp"}
    assert claims["exp"] - claims["iat"] == 15 * 60


def test_expired_or_forged_access_token_rejected():
    c = logged_in_client()
    claims = jwt.decode(c.cookies[ACCESS], settings.jwt_secret, algorithms=["HS256"])
    past = int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())
    expired = jwt.encode({**claims, "exp": past}, settings.jwt_secret, "HS256")
    forged = jwt.encode(claims, "an-attacker-secret-that-is-long-enough!!", "HS256")
    assert get_with_cookies("/api/auth/me", {ACCESS: expired}).status_code == 401
    assert get_with_cookies("/api/auth/me", {ACCESS: forged}).status_code == 401


# 7. Refresh rotation
def test_refresh_rotates_tokens_in_same_session():
    c = logged_in_client()
    old_refresh = c.cookies[REFRESH]
    response = c.post("/api/auth/refresh", headers=csrf_headers(c))
    assert response.status_code == 204
    assert c.cookies[REFRESH] != old_refresh
    # A fresh access token is issued (its value may equal the old one if minted in the same second).
    assert "Max-Age=900" in set_cookie_headers(response)[ACCESS]
    with SessionLocal() as db:
        auth_session = db.execute(select(AuthSession)).scalar_one()  # still one session
    assert auth_session.refresh_token_hash == hash_refresh_token(c.cookies[REFRESH])
    assert c.get("/api/auth/me").status_code == 200


# 8. Old refresh token cannot be reused
def test_replayed_refresh_token_rejected_and_session_revoked():
    c = logged_in_client()
    stale = c.cookies[REFRESH]
    assert c.post("/api/auth/refresh", headers=csrf_headers(c)).status_code == 204
    current = c.cookies[REFRESH]

    replay = post_with_cookies("/api/auth/refresh", {REFRESH: stale})
    assert replay.status_code == 401
    assert ACCESS not in set_cookie_headers(replay) or "Max-Age=0" in set_cookie_headers(replay)[ACCESS]

    # Replay is treated as theft: the legitimate holder's newer token is revoked too.
    assert post_with_cookies("/api/auth/refresh", {REFRESH: current}).status_code == 401
    assert c.get("/api/auth/me").status_code == 401


def test_refresh_rejects_missing_malformed_or_expired_token():
    c = logged_in_client()
    assert post_with_cookies("/api/auth/refresh", {}).status_code == 401
    assert post_with_cookies("/api/auth/refresh", {REFRESH: "garbage"}).status_code == 401
    with SessionLocal() as db:
        db.execute(update(AuthSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        db.commit()
    assert c.post("/api/auth/refresh", headers=csrf_headers(c)).status_code == 401


# 9. Logout revokes the session
def test_logout_revokes_session_immediately():
    c = logged_in_client()
    access, refresh = c.cookies[ACCESS], c.cookies[REFRESH]
    old_csrf = c.cookies[CSRF]
    response = c.post("/api/auth/logout", headers=csrf_headers(c))
    assert response.status_code == 204
    assert c.cookies[CSRF] != old_csrf  # CSRF token renewed at logout
    with SessionLocal() as db:
        assert db.execute(select(AuthSession.revoked_at)).scalar_one() is not None

    # Previously issued tokens no longer work, even though the access JWT itself has not expired.
    assert get_with_cookies("/api/auth/me", {ACCESS: access}).status_code == 401
    assert post_with_cookies("/api/auth/refresh", {REFRESH: refresh}).status_code == 401


def test_logout_only_revokes_current_session():
    first = logged_in_client()
    second = TestClient(app)
    assert login(second).status_code == 200
    assert first.post("/api/auth/logout", headers=csrf_headers(first)).status_code == 204
    assert first.get("/api/auth/me").status_code == 401
    assert second.get("/api/auth/me").status_code == 200


# 10. Protected endpoints reject unauthenticated requests
def test_protected_endpoints_require_authentication(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout", headers=csrf_headers(client)).status_code == 401
    assert client.get("/api/auth/me").json() == {"detail": "Not authenticated"}


# 11. CSRF
@pytest.mark.parametrize("path", ["/api/auth/register", "/api/auth/login", "/api/auth/refresh", "/api/auth/logout"])
def test_state_changing_requests_require_matching_csrf_token(path):
    c = logged_in_client()
    body = {"email": "bob@example.com", "password": PASSWORD}
    missing = c.post(path, json=body)
    mismatched = c.post(path, json=body, headers={settings.csrf_header_name: "not-the-cookie-value"})
    for response in (missing, mismatched):
        assert response.status_code == 403
        assert response.json() == {"detail": "CSRF token missing or invalid"}
    assert c.get("/api/auth/me").status_code == 200  # the rejected logout did nothing


def test_login_rotates_csrf_token(client):
    register(client)
    before = client.cookies[CSRF]
    login(client)
    assert client.cookies[CSRF] != before


# 12. Data scoping uses the authenticated user only
def test_current_user_comes_from_token_not_request_data():
    bob = logged_in_client("bob@example.com")
    bob_id = bob.get("/api/auth/me").json()["id"]
    alice = logged_in_client("alice@example.com")

    response = alice.get("/api/auth/me", params={"user_id": bob_id})
    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"


def test_token_subject_must_own_the_session():
    bob = logged_in_client("bob@example.com")
    bob_id = bob.get("/api/auth/me").json()["id"]
    alice = logged_in_client("alice@example.com")
    claims = jwt.decode(alice.cookies[ACCESS], settings.jwt_secret, algorithms=["HS256"])

    # A correctly signed token claiming Bob's id on Alice's session is still refused.
    tampered = jwt.encode({**claims, "sub": str(bob_id)}, settings.jwt_secret, "HS256")
    assert get_with_cookies("/api/auth/me", {ACCESS: tampered}).status_code == 401


# Configuration safety
def test_startup_rejects_missing_secret_and_insecure_production_cookies():
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        check_app_settings(Settings(jwt_secret="short"))
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        check_app_settings(Settings(jwt_secret="s" * 40, app_env="production", cookie_secure=False))
    check_app_settings(Settings(jwt_secret="s" * 40, app_env="production", cookie_secure=True))
