"""Finance API: authentication, CSRF, user scoping, validation without echoing values, string money."""

import json
import logging
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.repositories import user_repository
from app.services import fx_service, market_service, portfolio_service
from tests.fake_frankfurter import FakeFrankfurter
from tests.portfolio_support import nh, seed
from tests.test_auth import csrf_headers, logged_in_client
from tests.test_portfolio_service import RecordingMarket, fresh

pytestmark = pytest.mark.usefixtures("migrated_db")

SECRET_NOTE = "Medical bill for Synthetic Person PAN-ZZZZZ9999Z"
ENTRY = {"kind": "expense", "category": "Food", "amount": "1499.50", "date": "2026-10-05", "description": "lunch"}

ROUTES = [
    ("POST", "/api/finance/entries"), ("GET", "/api/finance/entries"), ("GET", "/api/finance/entries/1"),
    ("PUT", "/api/finance/entries/1"), ("DELETE", "/api/finance/entries/1"), ("POST", "/api/finance/budgets"),
    ("GET", "/api/finance/budgets?month=2026-10"), ("GET", "/api/finance/budgets/summary?month=2026-10"),
    ("PUT", "/api/finance/budgets/1"), ("DELETE", "/api/finance/budgets/1"), ("GET", "/api/finance/net-worth"),
]
WRITES = [(m, p) for m, p in ROUTES if m != "GET"]


@pytest.fixture
def market(monkeypatch):
    recorder = RecordingMarket({"INE000D01019": fresh("110")})
    monkeypatch.setattr(market_service, "get_prices", recorder)
    return recorder


@pytest.fixture
def frankfurter(monkeypatch):
    fake = FakeFrankfurter()
    monkeypatch.setattr(fx_service, "default_client", fake.client)
    return fake


@pytest.fixture
def alice():
    return logged_in_client("alice@example.com")


@pytest.fixture
def bob():
    return logged_in_client("bob@example.com")


def send(client: TestClient, method: str, path: str, body=None, *, csrf=True):
    headers = csrf_headers(client) if csrf else {}
    return client.request(method, path, json=body, headers=headers)


def create(client, **overrides):
    response = send(client, "POST", "/api/finance/entries", {**ENTRY, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def user_id_of(client) -> int:
    return client.get("/api/auth/me").json()["id"]


def set_currency(client, currency):
    with SessionLocal() as db:
        user = user_repository.get_by_id(db, user_id_of(client))
        user_repository.set_reporting_currency(db, user, currency)
        db.commit()


# --- Authentication, CSRF, scoping ------------------------------------------------------------------------

@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_requires_authentication(method, path):
    client = TestClient(app)
    body = ENTRY if method in ("POST", "PUT") else None
    assert send(client, method, path, body).status_code == 401


@pytest.mark.parametrize(("method", "path"), WRITES)
def test_every_write_requires_csrf(alice, method, path):
    assert send(alice, method, path, ENTRY, csrf=False).status_code == 403
    assert alice.request(method, path, json=ENTRY, headers={"X-CSRF-Token": "wrong"}).status_code == 403


def test_csrf_failure_writes_nothing(alice):
    entry = create(alice)
    send(alice, "POST", "/api/finance/entries", ENTRY, csrf=False)
    send(alice, "PUT", f"/api/finance/entries/{entry['id']}", {**ENTRY, "amount": "1"}, csrf=False)
    send(alice, "DELETE", f"/api/finance/entries/{entry['id']}", csrf=False)
    send(alice, "POST", "/api/finance/budgets", {"month": "2026-10", "category": "Food", "amount": "1"}, csrf=False)
    assert [e["amount"] for e in alice.get("/api/finance/entries").json()] == ["1499.50"]
    assert alice.get("/api/finance/budgets?month=2026-10").json() == []


def test_other_users_rows_behave_as_not_found(alice, bob):
    entry = create(alice)
    budget = send(alice, "POST", "/api/finance/budgets", {"month": "2026-10", "category": "Food", "amount": "100"})
    budget_id = budget.json()["id"]
    missing_entry = {"detail": "Finance entry not found", "code": "entry_not_found"}
    missing_budget = {"detail": "Budget not found", "code": "budget_not_found"}
    for method, body in (("GET", None), ("PUT", ENTRY), ("DELETE", None)):
        response = send(bob, method, f"/api/finance/entries/{entry['id']}", body)
        assert (response.status_code, response.json()) == (404, missing_entry)
    for method, body in (("PUT", {"amount": "1"}), ("DELETE", None)):
        response = send(bob, method, f"/api/finance/budgets/{budget_id}", body)
        assert (response.status_code, response.json()) == (404, missing_budget)
    assert bob.get("/api/finance/entries").json() == []
    assert bob.get("/api/finance/budgets?month=2026-10").json() == []
    assert bob.get("/api/finance/budgets/summary?month=2026-10").json()["budgets"] == []
    assert alice.get(f"/api/finance/entries/{entry['id']}").json()["amount"] == "1499.50"


@pytest.mark.parametrize(("method", "path", "body"), [
    ("GET", "/api/finance/entries/{id}", None), ("PUT", "/api/finance/entries/{id}", ENTRY),
    ("DELETE", "/api/finance/entries/{id}", None), ("PUT", "/api/finance/budgets/{id}", {"amount": "1"}),
    ("DELETE", "/api/finance/budgets/{id}", None),
])
def test_ids_beyond_the_database_integer_range_are_refused_not_a_server_error(alice, method, path, body):
    too_big = send(alice, method, path.format(id=2**63), body)  # cannot be bound to a query at all
    assert (too_big.status_code, too_big.json()["code"]) == (422, "invalid_request")
    largest = send(alice, method, path.format(id=2**63 - 1), body)  # a valid id that simply does not exist
    assert largest.status_code == 404


def test_user_id_and_currency_in_the_body_are_ignored(alice, bob):
    bob_id = user_id_of(bob)
    entry = create(alice, user_id=bob_id, currency="USD")
    assert entry["currency"] == "INR"
    assert bob.get("/api/finance/entries").json() == []
    budget = send(alice, "POST", "/api/finance/budgets",
                  {"month": "2026-10", "category": "Food", "amount": "1", "currency": "USD", "user_id": bob_id})
    assert budget.json()["currency"] == "INR"


# --- Entries ---------------------------------------------------------------------------------------------

def test_entry_crud_round_trip(alice):
    entry = create(alice, category="  Eating   out ", description="  team lunch  ")
    assert {k: entry[k] for k in ("kind", "category", "amount", "currency", "date", "description")} == {
        "kind": "expense", "category": "Eating out", "amount": "1499.50", "currency": "INR", "date": "2026-10-05",
        "description": "team lunch"}
    assert set(entry) == {"id", "kind", "category", "amount", "currency", "date", "description", "created_at",
                          "updated_at"}
    assert alice.get(f"/api/finance/entries/{entry['id']}").json() == entry  # same UTC timestamps as on create
    assert entry["created_at"].endswith(("Z", "+00:00"))

    replaced = send(alice, "PUT", f"/api/finance/entries/{entry['id']}",
                    {"kind": "income", "category": "Gift", "amount": "7", "date": "2027-01-01"})
    assert replaced.status_code == 200
    body = replaced.json()
    assert (body["kind"], body["category"], body["amount"], body["date"], body["description"]) == (
        "income", "Gift", "7.00", "2027-01-01", None)  # complete replacement: description cleared

    assert send(alice, "DELETE", f"/api/finance/entries/{entry['id']}").status_code == 204
    assert alice.get(f"/api/finance/entries/{entry['id']}").status_code == 404
    assert send(alice, "DELETE", f"/api/finance/entries/{entry['id']}").status_code == 404


@pytest.mark.parametrize("amount", ["999999999999999999.99", "123456789012345678.91", "0.01", "0.10", "10000000000000.01"])
def test_amounts_round_trip_exactly_through_the_api(alice, amount):
    entry = create(alice, amount=amount)
    expected = amount if "." in amount else amount + ".00"
    assert entry["amount"] == expected
    assert alice.get(f"/api/finance/entries/{entry['id']}").json()["amount"] == expected


def test_large_amounts_sum_exactly_in_cash_and_budgets(alice, market):
    create(alice, kind="income", amount="999999999999999999.99")
    create(alice, kind="income", amount="0.01")
    create(alice, amount="123456789012345678.91")
    budget = send(alice, "POST", "/api/finance/budgets",
                  {"month": "2026-10", "category": "Food", "amount": "999999999999999999.99"}).json()
    assert budget["amount"] == "999999999999999999.99"
    body = alice.get("/api/finance/net-worth").json()
    # 999999999999999999.99 + 0.01 - 123456789012345678.91, exact (a float would give 876543210987654400)
    assert body["cash_position"] == body["net_worth"] == "876543210987654321.09"
    line = alice.get("/api/finance/budgets/summary?month=2026-10").json()["budgets"][0]
    assert (line["actual"], line["remaining"]) == ("123456789012345678.91", "876543210987654321.08")


def test_put_keeps_currency_after_reporting_currency_change(alice):
    entry = create(alice)
    set_currency(alice, "EUR")
    replaced = send(alice, "PUT", f"/api/finance/entries/{entry['id']}", {**ENTRY, "currency": "EUR"}).json()
    assert replaced["currency"] == "INR"
    assert create(alice)["currency"] == "EUR"


def test_list_filters(alice):
    a = create(alice, date="2026-10-01")
    b = create(alice, date="2026-10-03", kind="income", category="Salary")
    c = create(alice, date="2026-10-09", category="food")
    ids = lambda query="": [e["id"] for e in alice.get(f"/api/finance/entries{query}").json()]  # noqa: E731
    assert ids() == [c["id"], b["id"], a["id"]]
    assert ids("?start=2026-10-02&end=2026-10-03") == [b["id"]]
    assert ids("?kind=income") == [b["id"]]
    assert ids("?category=Food") == [a["id"]]
    assert ids("?category=%20%20Food%20") == [a["id"]]


@pytest.mark.parametrize(("query", "code"), [
    ("?start=2026-10-05&end=2026-10-04", "invalid_date_range"), ("?start=2026-02-30", "invalid_date"),
    ("?end=1700000000", "invalid_date"), ("?category=%20", "invalid_category"),
])
def test_list_filter_errors(alice, query, code):
    response = alice.get(f"/api/finance/entries{query}")
    assert (response.status_code, response.json()["code"]) == (422, code)


def test_unknown_kind_filter_is_a_schema_error(alice):
    response = alice.get("/api/finance/entries?kind=transfer")
    assert (response.status_code, response.json()["code"]) == (422, "invalid_request")


@pytest.mark.parametrize(("field", "value", "code"), [
    ("amount", "0", "invalid_amount"), ("amount", "-1", "invalid_amount"), ("amount", "1.001", "invalid_amount"),
    ("amount", "1e3", "invalid_amount"), ("amount", "NaN", "invalid_amount"), ("amount", "Infinity", "invalid_amount"),
    ("amount", " 1", "invalid_amount"), ("amount", "+1", "invalid_amount"), ("amount", "1000000000000000000", "invalid_amount"),
    ("category", "   ", "invalid_category"), ("category", "x" * 51, "invalid_category"),
    ("description", "d" * 256, "invalid_description"),
    ("date", "2026-02-30", "invalid_date"), ("date", "20261005", "invalid_date"), ("date", "1899-12-31", "invalid_date"),
    ("date", "2026-10-05T00:00:00", "invalid_date"),
])
def test_invalid_entry_values(alice, field, value, code):
    response = send(alice, "POST", "/api/finance/entries", {**ENTRY, field: value})
    assert (response.status_code, response.json()["code"]) == (422, code)
    assert response.json()["detail"] and f'"{value}"' not in response.text  # never echoed
    assert alice.get("/api/finance/entries").json() == []


@pytest.mark.parametrize(("field", "value"), [
    ("amount", 1499.5), ("amount", 1499), ("amount", None), ("date", 1759622400), ("date", None),
    ("kind", "transfer"), ("category", 5), ("description", 5),
])
def test_wrong_json_types_are_schema_errors_without_echo(alice, field, value):
    body = {**ENTRY, "description": SECRET_NOTE, field: value}
    response = send(alice, "POST", "/api/finance/entries", body)
    assert (response.status_code, response.json()["code"]) == (422, "invalid_request")
    assert all(set(error) == {"type", "loc", "msg"} for error in response.json()["detail"])
    assert SECRET_NOTE not in response.text and "1499.50" not in response.text


def test_missing_field_error_does_not_echo_the_body(alice):
    response = send(alice, "POST", "/api/finance/entries", {"kind": "expense", "description": SECRET_NOTE})
    assert response.status_code == 422 and SECRET_NOTE not in response.text


def test_malformed_json_error_does_not_echo_the_body(alice):
    response = alice.post("/api/finance/entries", content=b'{"description": "' + SECRET_NOTE.encode() + b'",',
                          headers={**csrf_headers(alice), "content-type": "application/json"})
    assert response.status_code == 422 and SECRET_NOTE not in response.text


def test_other_routes_keep_fastapis_default_validation_errors():
    client = TestClient(app)
    response = client.post("/api/auth/register", json={"email": "not-an-email", "password": "x"},
                           headers=csrf_headers(client))
    assert response.status_code == 422
    assert "code" not in response.json() and response.json()["detail"][0]["input"] == "not-an-email"


def test_personal_values_never_reach_logs_or_errors(alice, caplog, market):
    caplog.set_level(logging.DEBUG)
    entry = create(alice, description=SECRET_NOTE, amount="4321.09", category="Therapy")
    send(alice, "PUT", f"/api/finance/entries/{entry['id']}", {**ENTRY, "description": SECRET_NOTE, "amount": "x9"})
    send(alice, "POST", "/api/finance/entries", {**ENTRY, "description": SECRET_NOTE + "!" * 300})
    send(alice, "POST", "/api/finance/budgets", {"month": "2026-13", "category": "Therapy", "amount": "4321.09"})
    alice.get("/api/finance/net-worth")
    alice.get("/api/finance/budgets/summary?month=2026-10")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for value in (SECRET_NOTE, "4321.09", "Therapy", "x9"):
        assert value not in logged


# --- Budgets ---------------------------------------------------------------------------------------------

def test_budget_crud_and_summary(alice):
    created = send(alice, "POST", "/api/finance/budgets", {"month": "2026-10", "category": " Food ", "amount": "200"})
    assert created.status_code == 201
    budget = created.json()
    assert {k: budget[k] for k in ("month", "category", "amount", "currency")} == {
        "month": "2026-10", "category": "Food", "amount": "200.00", "currency": "INR"}
    duplicate = send(alice, "POST", "/api/finance/budgets", {"month": "2026-10", "category": "Food", "amount": "1"})
    assert (duplicate.status_code, duplicate.json()["code"]) == (409, "budget_exists")

    create(alice, amount="150.25", date="2026-10-31")
    create(alice, amount="99", date="2026-11-01")
    create(alice, amount="500", kind="income")
    create(alice, amount="3", category="Travel")
    summary = alice.get("/api/finance/budgets/summary?month=2026-10").json()
    assert summary == {
        "month": "2026-10",
        "budgets": [{"budget_id": budget["id"], "category": "Food", "currency": "INR", "budget": "200.00",
                     "actual": "150.25", "remaining": "49.75", "utilization_pct": "75.12", "over_budget": False}],
        "unbudgeted": [{"category": "Travel", "currency": "INR", "actual": "3.00"}],
    }

    changed = send(alice, "PUT", f"/api/finance/budgets/{budget['id']}", {"amount": "100", "month": "2027-01"})
    assert (changed.json()["amount"], changed.json()["month"]) == ("100.00", "2026-10")
    line = alice.get("/api/finance/budgets/summary?month=2026-10").json()["budgets"][0]
    assert (line["remaining"], line["over_budget"]) == ("-50.25", True)

    assert [b["id"] for b in alice.get("/api/finance/budgets?month=2026-10").json()] == [budget["id"]]
    assert send(alice, "DELETE", f"/api/finance/budgets/{budget['id']}").status_code == 204
    assert alice.get("/api/finance/budgets?month=2026-10").json() == []


def test_summary_route_is_not_a_budget_id(alice):
    response = alice.get("/api/finance/budgets/summary?month=2026-10")
    assert response.status_code == 200 and response.json()["month"] == "2026-10"


@pytest.mark.parametrize("path", ["/api/finance/budgets", "/api/finance/budgets/summary"])
@pytest.mark.parametrize("month", ["2026-13", "2026-1", "26-10", "2026-10-01"])
def test_invalid_months(alice, path, month):
    response = alice.get(f"{path}?month={month}")
    assert (response.status_code, response.json()["code"]) == (422, "invalid_month")


def test_month_is_required(alice):
    assert alice.get("/api/finance/budgets").status_code == 422


@pytest.mark.parametrize("amount", ["0", "-5", "1.234", 100])
def test_invalid_budget_amounts(alice, amount):
    response = send(alice, "POST", "/api/finance/budgets", {"month": "2026-10", "category": "Food", "amount": amount})
    assert response.status_code == 422


# --- Net worth -------------------------------------------------------------------------------------------

def test_empty_net_worth(alice, market):
    body = alice.get("/api/finance/net-worth").json()
    assert body == {
        "reporting_currency": "INR", "net_worth": "0.00", "portfolio_value": "0.00", "cash_position": "0.00",
        "cash_by_currency": {}, "complete": True, "unpriced_holdings": 0, "cost_floor_value": "0.00",
        "contains_stale_prices": False, "oldest_price_as_of": None, "fx_stale": False, "fx_rates": [],
        "fx_unavailable": [],
    }
    assert market.calls == []


def test_net_worth_is_portfolio_plus_cash_in_inr(alice, market):
    with SessionLocal() as db:
        seed(db, user_id_of(alice), "zerodha", "equity", date(2026, 7, 31), [nh(qty="10", avg="100")])
    create(alice, kind="income", amount="1000")
    create(alice, amount="1100.00")
    body = alice.get("/api/finance/net-worth").json()
    assert (body["portfolio_value"], body["cash_position"], body["net_worth"]) == ("1100.00", "-100.00", "1000.00")
    assert body["cash_by_currency"] == {"INR": "-100.00"} and body["complete"] is True
    assert "-0.00" not in json.dumps(body)


def test_net_worth_in_eur_with_fund_floor_kept_separate(alice, market, frankfurter):
    with SessionLocal() as db:
        uid = user_id_of(alice)
        seed(db, uid, "zerodha", "equity", date(2026, 7, 31), [nh(qty="10", avg="100")])
        seed(db, uid, "groww", "mutual_fund", date(2026, 7, 31),
             [nh("Fund", None, None, "5", "20", asset_class="mutual_fund", broker="groww")])
    create(alice, kind="income", amount="1000")
    set_currency(alice, "EUR")
    create(alice, kind="income", amount="4")
    body = alice.get("/api/finance/net-worth").json()
    # 1100 INR x 0.0105 = 11.55 EUR; cash 1000 INR x 0.0105 + 4 EUR = 14.50 EUR; floor 100 INR = 1.05 EUR
    assert (body["reporting_currency"], body["portfolio_value"], body["cash_position"], body["net_worth"]) == (
        "EUR", "11.55", "14.50", "26.05")
    assert (body["cost_floor_value"], body["complete"], body["unpriced_holdings"]) == ("1.05", False, 1)
    assert body["cash_by_currency"] == {"INR": "1000.00", "EUR": "4.00"}
    assert {r["currency"]: (r["rate"], r["status"]) for r in body["fx_rates"]} == {
        "INR": ("0.010500", "fresh"), "EUR": ("1.000000", "identity")}
    assert frankfurter.rate_calls() == ["/v2/rate/INR/EUR"]


def test_net_worth_without_fx_is_null_not_partial(alice, market, frankfurter):
    frankfurter.rates.pop(("INR", "EUR"))
    create(alice, kind="income", amount="1000")
    set_currency(alice, "EUR")
    create(alice, kind="income", amount="4")
    body = alice.get("/api/finance/net-worth").json()
    assert (body["net_worth"], body["cash_position"], body["fx_unavailable"], body["complete"]) == (
        None, None, ["INR"], False)
    assert body["cash_by_currency"] == {"INR": "1000.00", "EUR": "4.00"}


def test_net_worth_conflict_on_invalid_holdings(alice, monkeypatch):
    def broken(db, user, **_):
        raise portfolio_service.PortfolioDataError("zerodha", "equity")

    monkeypatch.setattr(portfolio_service, "get_portfolio", broken)
    response = alice.get("/api/finance/net-worth")
    assert response.status_code == 409
    assert response.json() == {"detail": "Stored zerodha equity holdings are invalid; re-import the latest zerodha "
                                         "equity statement"}
