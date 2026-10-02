"""Twelve Data client against a deterministic mock transport (no network)."""

import logging
from decimal import Decimal

import httpx2
import pytest
from pydantic import SecretStr

from app.domain.market import PriceIssue, SymbolCandidate
from app.integrations.twelve_data import TwelveDataClient, TwelveDataError

FAKE_KEY = "unit-test-key-not-a-real-secret-0001"
CANDIDATE = {
    "symbol": "SYNTHA", "instrument_name": "Synthetic Alpha Ltd", "exchange": "NSE", "mic_code": "XNSE",
    "exchange_timezone": "Asia/Kolkata", "instrument_type": "Common Stock", "country": "India", "currency": "INR",
    "access": {"global": "Level A", "plan": "Grow"},
}


def make_client(handler, requests: list | None = None) -> TwelveDataClient:
    def recording(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        return handler(request)

    return TwelveDataClient(SecretStr(FAKE_KEY), transport=httpx2.MockTransport(recording))


def respond(status=200, json=None, text=None):
    return lambda request: httpx2.Response(status, json=json) if text is None else httpx2.Response(status, text=text)


# --- Configuration and request shape -------------------------------------------------------------

def test_missing_key_is_not_configured():
    with pytest.raises(TwelveDataError) as raised:
        TwelveDataClient(SecretStr(""))
    assert raised.value.issue == PriceIssue.NOT_CONFIGURED


def test_symbol_search_request_and_parsing():
    requests: list = []
    client = make_client(respond(json={"data": [CANDIDATE], "status": "ok"}), requests)
    assert client.symbol_search("INE000D01019") == [SymbolCandidate(
        "SYNTHA", "Synthetic Alpha Ltd", "NSE", "XNSE", "India", "INR", "Common Stock")]
    (request,) = requests
    assert request.method == "GET"
    assert request.url.host == "api.twelvedata.com" and request.url.path == "/symbol_search"
    assert dict(request.url.params) == {"symbol": "INE000D01019", "show_plan": "true"}


def test_price_request_and_exact_decimal():
    requests: list = []
    client = make_client(respond(json={"price": "1234.56000"}), requests)
    price = client.price("SYNTHA", "XNSE")
    assert type(price) is Decimal and price == Decimal("1234.56000")
    assert requests[0].url.path == "/price"
    assert dict(requests[0].url.params) == {"symbol": "SYNTHA", "mic_code": "XNSE"}


def test_key_only_in_authorization_header():
    requests: list = []
    make_client(respond(json={"price": "10"}), requests).price("SYNTHA", "XNSE")
    request = requests[0]
    assert request.headers["Authorization"] == f"apikey {FAKE_KEY}"
    assert FAKE_KEY not in str(request.url)
    assert "apikey" not in request.url.params


# --- Error mapping -------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("status", "body", "issue"),
    [
        (429, {"code": 429, "message": "You have run out of API credits", "status": "error"}, PriceIssue.RATE_LIMITED),
        (401, {"code": 401, "message": "**apikey** parameter is incorrect", "status": "error"},
         PriceIssue.CREDENTIALS_REJECTED),
        (403, {"code": 403, "message": "upgrade required", "status": "error"}, PriceIssue.NOT_ENTITLED),
        (404, {"code": 404, "message": "symbol not found", "status": "error"}, PriceIssue.SYMBOL_NOT_FOUND),
        (400, {"code": 400, "message": "invalid symbol", "status": "error"}, PriceIssue.SYMBOL_NOT_FOUND),
        (404, {"code": 404, "message": "This symbol is available starting with the Grow plan", "status": "error"},
         PriceIssue.NOT_ENTITLED),
        (500, {"code": 500, "message": "internal error", "status": "error"}, PriceIssue.SERVICE_UNAVAILABLE),
        (503, None, PriceIssue.SERVICE_UNAVAILABLE),
        (302, None, PriceIssue.INVALID_RESPONSE),
    ],
)
def test_http_errors_map_to_issues(status, body, issue):
    client = make_client(respond(status, json=body))
    with pytest.raises(TwelveDataError) as raised:
        client.price("SYNTHA", "XNSE")
    assert raised.value.issue == issue


@pytest.mark.parametrize(
    ("body", "issue"),
    [
        ({"code": 429, "message": "minute limit", "status": "error"}, PriceIssue.RATE_LIMITED),
        ({"code": 404, "message": "not found", "status": "error"}, PriceIssue.SYMBOL_NOT_FOUND),
        ({"code": 403, "message": "plan", "status": "error"}, PriceIssue.NOT_ENTITLED),
        ({"code": 999, "message": "odd", "status": "error"}, PriceIssue.INVALID_RESPONSE),
    ],
)
def test_errors_reported_inside_http_200_bodies(body, issue):
    with pytest.raises(TwelveDataError) as raised:
        make_client(respond(200, json=body)).price("SYNTHA", "XNSE")
    assert raised.value.issue == issue


@pytest.mark.parametrize(
    "exception", [httpx2.ConnectTimeout, httpx2.ReadTimeout, httpx2.ConnectError, httpx2.RemoteProtocolError]
)
def test_timeouts_and_connection_failures_are_service_unavailable(exception):
    def fail(request):
        raise exception("boom", request=request)

    with pytest.raises(TwelveDataError) as raised:
        make_client(fail).price("SYNTHA", "XNSE")
    assert raised.value.issue == PriceIssue.SERVICE_UNAVAILABLE


@pytest.mark.parametrize(
    "response",
    [
        respond(text="<html>gateway</html>"),
        respond(json=["not", "an", "object"]),
        respond(json={}),
        respond(json={"price": None}),
        respond(json={"price": "abc"}),
        respond(json={"price": "-3.5"}),
        respond(json={"price": "0"}),
        respond(json={"price": "NaN"}),
    ],
    ids=["html", "json-list", "missing-price", "null-price", "non-numeric", "negative", "zero", "nan"],
)
def test_malformed_or_invalid_price_responses(response):
    with pytest.raises(TwelveDataError) as raised:
        make_client(response).price("SYNTHA", "XNSE")
    assert raised.value.issue == PriceIssue.INVALID_RESPONSE


@pytest.mark.parametrize(
    "body",
    [
        {}, {"data": "nope"}, {"data": [{"symbol": "SYNTHA"}]},
        {"data": [{**CANDIDATE, "mic_code": None}]}, {"data": ["x"]},
    ],
    ids=["no-data", "data-not-list", "missing-fields", "null-field", "non-object-item"],
)
def test_malformed_symbol_search_responses(body):
    with pytest.raises(TwelveDataError) as raised:
        make_client(respond(json=body)).symbol_search("SYNTHA")
    assert raised.value.issue == PriceIssue.INVALID_RESPONSE


def test_empty_symbol_search_is_a_valid_empty_list():
    assert make_client(respond(json={"data": [], "status": "ok"})).symbol_search("NOPE") == []


# --- Secret hygiene --------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "handler",
    [
        respond(401, json={"code": 401, "message": f"bad key {FAKE_KEY}", "status": "error"}),
        respond(200, json={"code": 500, "message": f"echo {FAKE_KEY}", "status": "error"}),
        respond(text=f"garbage {FAKE_KEY}"),
    ],
)
def test_key_and_provider_messages_never_reach_errors_or_logs(handler, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TwelveDataError) as raised:
        make_client(handler).price("SYNTHA", "XNSE")
    error = raised.value
    rendered = str(error) + repr(error) + repr(error.__cause__) + repr(error.__context__) + caplog.text
    assert "HTTP Request" in caplog.text  # the request was logged, so the check below is not vacuous
    assert FAKE_KEY not in rendered
    assert "echo" not in str(error) and "garbage" not in str(error)
    # No chained exception (which could carry provider data) is exposed with the error.
    assert error.__cause__ is None and (error.__context__ is None or error.__suppress_context__)


def test_key_not_in_transport_failure_error(caplog):
    caplog.set_level(logging.DEBUG)

    def fail(request):
        raise httpx2.ConnectError(f"cannot reach {request.url}", request=request)

    with pytest.raises(TwelveDataError) as raised:
        make_client(fail).price("SYNTHA", "XNSE")
    assert FAKE_KEY not in str(raised.value) + repr(raised.value) + caplog.text
