"""Upstox client against a mock transport: request shape, parsing, error mapping, secret hygiene."""

import json
import logging
from decimal import Decimal

import httpx2
import pytest
from pydantic import SecretStr

from app.domain.market import PriceIssue, Quote
from app.integrations import upstox
from app.integrations.upstox import UpstoxClient, UpstoxError

TOKEN = "unit-test-token-not-a-real-secret-0001"
ISIN_A, ISIN_B = "INE000D01019", "INE000E01025"


def make(handler) -> UpstoxClient:
    return UpstoxClient(SecretStr(TOKEN), transport=httpx2.MockTransport(handler))


def success(data) -> httpx2.Response:
    return httpx2.Response(200, json={"status": "success", "data": data})


def entry(key, price, symbol="SYNTHA"):
    return {f"{key.split('|')[0]}:{symbol}": {"last_price": price, "instrument_token": key, "ltq": 1, "cp": price}}


def test_missing_token_is_not_configured():
    with pytest.raises(UpstoxError) as raised:
        UpstoxClient(SecretStr(""))
    assert raised.value.issue == PriceIssue.NOT_CONFIGURED


def test_request_asks_for_nse_and_bse_by_isin_with_the_token_only_in_the_header():
    seen = []

    def handler(request):
        seen.append(request)
        return success({})

    client = make(handler)
    client.last_prices([ISIN_A, ISIN_B, ISIN_A])
    (request,) = seen
    assert (request.method, request.url.host, request.url.path) == ("GET", "api.upstox.com", "/v3/market-quote/ltp")
    assert request.url.params["instrument_key"].split(",") == [
        f"NSE_EQ|{ISIN_A}", f"BSE_EQ|{ISIN_A}", f"NSE_EQ|{ISIN_B}", f"BSE_EQ|{ISIN_B}"]
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(request.url)


def test_quotes_are_exact_decimals_grouped_by_isin_and_exchange():
    data = {**entry(f"NSE_EQ|{ISIN_A}", 1391.05), **entry(f"BSE_EQ|{ISIN_A}", 1390),
            **entry(f"BSE_EQ|{ISIN_B}", 55.5, "SYNTHB")}
    quotes = make(lambda request: success(data)).last_prices([ISIN_A, ISIN_B])
    assert quotes == {
        ISIN_A: {"NSE": Quote("NSE", "SYNTHA", Decimal("1391.05")), "BSE": Quote("BSE", "SYNTHA", Decimal("1390"))},
        ISIN_B: {"BSE": Quote("BSE", "SYNTHB", Decimal("55.5"))},
    }
    assert isinstance(quotes[ISIN_A]["NSE"].price, Decimal) and str(quotes[ISIN_A]["NSE"].price) == "1391.05"


def test_unlisted_isins_are_simply_absent():
    assert make(lambda request: success({})).last_prices([ISIN_A]) == {}


def test_large_portfolios_are_split_into_requests_of_at_most_500_keys(monkeypatch):
    sizes = []

    def handler(request):
        sizes.append(len(request.url.params["instrument_key"].split(",")))
        return success({})

    isins = [f"INE{n:09d}" for n in range(260)]  # 520 keys
    make(handler).last_prices(isins)
    assert sizes == [500, 20]


@pytest.mark.parametrize(
    ("status", "issue"),
    [(401, PriceIssue.CREDENTIALS_REJECTED), (403, PriceIssue.NOT_ENTITLED), (429, PriceIssue.RATE_LIMITED),
     (400, PriceIssue.REQUEST_REJECTED), (404, PriceIssue.REQUEST_REJECTED), (500, PriceIssue.SERVICE_UNAVAILABLE),
     (503, PriceIssue.SERVICE_UNAVAILABLE), (302, PriceIssue.INVALID_RESPONSE)],
)
def test_http_errors_map_to_issues(status, issue):
    response = httpx2.Response(status, json={"status": "error", "errors": [{"errorCode": "UDAPI", "message": "x"}]})
    with pytest.raises(UpstoxError) as raised:
        make(lambda request: response).last_prices([ISIN_A])
    assert raised.value.issue == issue


@pytest.mark.parametrize("exception", [httpx2.ReadTimeout("slow"), httpx2.ConnectError("refused")])
def test_timeouts_and_connection_failures_are_service_unavailable(exception):
    def handler(request):
        raise exception

    with pytest.raises(UpstoxError) as raised:
        make(handler).last_prices([ISIN_A])
    assert raised.value.issue == PriceIssue.SERVICE_UNAVAILABLE


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, text="<html>"),
        httpx2.Response(200, json=[]),
        httpx2.Response(200, json={"status": "error", "data": {}}),
        httpx2.Response(200, json={"status": "success"}),
        httpx2.Response(200, json={"status": "success", "data": []}),
        success({"NSE_EQ:X": "not an object"}),
        success({"NSE_EQ:X": {"last_price": 1}}),  # no instrument_token
        success(entry("NSE_EQ|INE999Z99999", 10)),  # a listing that was not asked for
        success(entry(f"NSE_EQ|{ISIN_A}", "101.5")),  # price as a string
        success(entry(f"NSE_EQ|{ISIN_A}", 0)),
        success(entry(f"NSE_EQ|{ISIN_A}", -3)),
        success(entry(f"NSE_EQ|{ISIN_A}", None)),
        success(entry(f"NSE_EQ|{ISIN_A}", True)),
    ],
    ids=["html", "list", "status-error", "no-data", "data-list", "entry-not-object", "no-token", "unrequested",
         "string-price", "zero", "negative", "null", "bool"],
)
def test_malformed_or_invalid_responses(response):
    with pytest.raises(UpstoxError) as raised:
        make(lambda request: response).last_prices([ISIN_A])
    assert raised.value.issue == PriceIssue.INVALID_RESPONSE


def test_token_and_provider_messages_never_reach_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    body = {"status": "error", "errors": [{"errorCode": "UDAPI100050", "message": f"Invalid token {TOKEN}"}]}
    with pytest.raises(UpstoxError) as raised:
        make(lambda request: httpx2.Response(401, json=body)).last_prices([ISIN_A])
    assert str(raised.value) == "Upstox rejected the configured access token"
    assert TOKEN not in caplog.text and "Invalid token" not in caplog.text
    assert TOKEN not in json.dumps(raised.value.args)


def test_token_not_in_transport_failure_error():
    def handler(request):
        raise httpx2.ConnectError(f"failed for {request.headers['Authorization']}")

    with pytest.raises(UpstoxError) as raised:
        make(handler).last_prices([ISIN_A])
    assert TOKEN not in str(raised.value) and raised.value.__cause__ is None


def test_exchanges_map_to_upstox_segments():
    assert upstox.instrument_keys(ISIN_A) == [f"NSE_EQ|{ISIN_A}", f"BSE_EQ|{ISIN_A}"]
