"""PATCH /api/auth/me: only the authenticated user's reporting currency, validated against Frankfurter."""

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.repositories import user_repository
from app.services import fx_service
from tests.fake_frankfurter import FakeFrankfurter
from tests.test_auth import csrf_headers, logged_in_client

pytestmark = pytest.mark.usefixtures("migrated_db")


@pytest.fixture
def frankfurter(monkeypatch):
    fake = FakeFrankfurter()
    monkeypatch.setattr(fx_service, "default_client", fake.client)
    return fake


@pytest.fixture
def alice():
    return logged_in_client("alice@example.com")


def patch(client, body, headers=None):
    return client.patch("/api/auth/me", json=body, headers=csrf_headers(client) if headers is None else headers)


def currency_of(email):
    with SessionLocal() as db:
        return user_repository.get_by_email(db, email).reporting_currency


def test_default_reporting_currency_is_inr(alice):
    assert alice.get("/api/auth/me").json()["reporting_currency"] == "INR"


def test_update_reporting_currency(alice, frankfurter):
    response = patch(alice, {"reporting_currency": "EUR"})
    assert response.status_code == 200
    assert response.json()["reporting_currency"] == "EUR"
    assert alice.get("/api/auth/me").json()["reporting_currency"] == "EUR"
    assert frankfurter.calls == ["/v2/currencies"]
    assert patch(alice, {"reporting_currency": "INR"}).json()["reporting_currency"] == "INR"
    assert frankfurter.calls == ["/v2/currencies"]  # currency list cached


def test_requires_authentication(frankfurter):
    anonymous = TestClient(app)
    assert patch(anonymous, {"reporting_currency": "EUR"}).status_code == 401


@pytest.mark.parametrize("headers", [{}, {settings.csrf_header_name: "wrong"}], ids=["missing", "wrong"])
def test_requires_csrf(alice, frankfurter, headers):
    assert patch(alice, {"reporting_currency": "EUR"}, headers=headers).status_code == 403
    assert currency_of("alice@example.com") == "INR"


@pytest.mark.parametrize("code", ["eur", "Eur", "EU", "EURO", " EUR", "E1R", ""])
def test_invalid_code_rejected_without_request(alice, frankfurter, code):
    response = patch(alice, {"reporting_currency": code})
    assert (response.status_code, response.json()["code"]) == (422, "invalid_currency")
    assert frankfurter.calls == [] and currency_of("alice@example.com") == "INR"


@pytest.mark.parametrize("body", [{}, {"reporting_currency": None}, {"reporting_currency": 978}])
def test_missing_or_non_string_value_rejected(alice, frankfurter, body):
    assert patch(alice, body).status_code == 422
    assert currency_of("alice@example.com") == "INR"


def test_unsupported_currency_rejected(alice, frankfurter):
    response = patch(alice, {"reporting_currency": "XYZ"})
    assert (response.status_code, response.json()["code"]) == (422, "unsupported_currency")
    assert currency_of("alice@example.com") == "INR"


@pytest.mark.parametrize("failure", [httpx2.Response(503), httpx2.ConnectError("down"), httpx2.Response(200, text="x")])
def test_frankfurter_unavailable_returns_503_and_stores_nothing(alice, frankfurter, failure):
    frankfurter.currencies = failure
    response = patch(alice, {"reporting_currency": "EUR"})
    assert (response.status_code, response.json()["code"]) == (503, "currency_validation_unavailable")
    assert currency_of("alice@example.com") == "INR"


def test_fx_disabled_returns_503(alice, monkeypatch):
    monkeypatch.setattr(settings, "fx_enabled", False)
    assert patch(alice, {"reporting_currency": "EUR"}).status_code == 503
    assert currency_of("alice@example.com") == "INR"


def test_user_id_in_request_cannot_target_another_user(alice, frankfurter):
    bob = logged_in_client("bob@example.com")
    bob_id = bob.get("/api/auth/me").json()["id"]
    response = alice.patch("/api/auth/me", params={"user_id": bob_id},
                           json={"reporting_currency": "EUR", "user_id": bob_id, "id": bob_id},
                           headers=csrf_headers(alice))
    assert response.status_code == 200 and response.json()["email"] == "alice@example.com"
    assert (currency_of("alice@example.com"), currency_of("bob@example.com")) == ("EUR", "INR")


def test_no_other_profile_fields_change(alice, frankfurter):
    response = patch(alice, {"reporting_currency": "EUR", "email": "evil@example.com", "password": "x" * 20})
    assert response.status_code == 200
    assert response.json() == {"id": response.json()["id"], "email": "alice@example.com", "reporting_currency": "EUR"}
    assert alice.post("/api/auth/login", json={"email": "alice@example.com", "password": "correct horse battery"},
                      headers=csrf_headers(alice)).status_code == 200


def test_upstream_text_not_leaked(alice, frankfurter):
    frankfurter.currencies = httpx2.Response(500, json={"message": "UPSTREAM-DETAIL-9"})
    assert "UPSTREAM-DETAIL-9" not in patch(alice, {"reporting_currency": "EUR"}).text
