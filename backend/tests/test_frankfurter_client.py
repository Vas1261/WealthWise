"""Frankfurter client over a mock transport (no network)."""

import logging
from datetime import date
from decimal import Decimal

import httpx2
import pytest

from app.domain.fx import FxIssue
from app.integrations.frankfurter import FrankfurterClient, FrankfurterError, Quote
from tests.fake_frankfurter import FakeFrankfurter, json_response


def client_for(handler) -> FrankfurterClient:
    return FrankfurterClient("https://api.frankfurter.dev", transport=httpx2.MockTransport(handler))


def respond(response):
    return lambda request: response


def test_rate_request_shape_and_exact_decimal():
    fake = FakeFrankfurter()
    quote = fake.client().rate("USD", "INR")
    assert quote == Quote("USD", "INR", Decimal("96.14"), date(2026, 6, 30))
    assert type(quote.rate) is Decimal and str(quote.rate) == "96.14"  # never via float
    assert fake.calls == ["/v2/rate/USD/INR"]


def test_high_precision_rate_preserved():
    fake = FakeFrankfurter()
    fake.rates[("JPY", "EUR")] = "0.0059123456789012345678"
    assert str(fake.client().rate("JPY", "EUR").rate) == "0.0059123456789012345678"


@pytest.mark.parametrize(("base", "quote"), [("usd", "INR"), ("USD", "inr"), ("US", "INR"), ("USD", "IN R")])
def test_invalid_codes_make_no_request(base, quote):
    fake = FakeFrankfurter()
    with pytest.raises(FrankfurterError) as raised:
        fake.client().rate(base, quote)
    assert raised.value.issue == FxIssue.INVALID_CURRENCY and fake.calls == []


@pytest.mark.parametrize(
    ("response", "issue"),
    [
        (json_response({"status": 422, "message": "invalid currency: XYZ"}, 422), FxIssue.UNSUPPORTED_CURRENCY),
        (json_response({"message": "slow down"}, 429), FxIssue.RATE_LIMITED),
        (json_response({"message": "boom"}, 500), FxIssue.SERVICE_UNAVAILABLE),
        (httpx2.Response(503), FxIssue.SERVICE_UNAVAILABLE),
        (httpx2.Response(404, text="not found"), FxIssue.INVALID_RESPONSE),
        (httpx2.Response(301), FxIssue.INVALID_RESPONSE),
        (httpx2.Response(200, text="<html>"), FxIssue.INVALID_RESPONSE),
        (json_response(["not", "an", "object"]), FxIssue.INVALID_RESPONSE),
        (json_response({"date": "2026-06-30", "base": "USD", "quote": "INR"}), FxIssue.INVALID_RESPONSE),
        (json_response({"date": "2026-06-30", "base": "USD", "quote": "INR", "rate": None}), FxIssue.INVALID_RESPONSE),
        (json_response({"date": "2026-06-30", "base": "USD", "quote": "INR", "rate": "96.14"}),
         FxIssue.INVALID_RESPONSE),
        (json_response({"date": "2026-06-30", "base": "USD", "quote": "INR", "rate": 0}), FxIssue.INVALID_RESPONSE),
        (json_response({"date": "2026-06-30", "base": "USD", "quote": "INR", "rate": -1}), FxIssue.INVALID_RESPONSE),
        (json_response({"date": "2026-06-30", "base": "EUR", "quote": "INR", "rate": 96}), FxIssue.INVALID_RESPONSE),
        (json_response({"date": "2026-06-30", "base": "USD", "quote": "EUR", "rate": 96}), FxIssue.INVALID_RESPONSE),
        (json_response({"date": "30/06/2026", "base": "USD", "quote": "INR", "rate": 96}), FxIssue.INVALID_RESPONSE),
        (json_response({"base": "USD", "quote": "INR", "rate": 96}), FxIssue.INVALID_RESPONSE),
    ],
    ids=["422-unsupported", "429", "500", "503", "404", "301", "html", "json-list", "no-rate", "null-rate",
         "string-rate", "zero-rate", "negative-rate", "wrong-base", "wrong-quote", "bad-date", "no-date"],
)
def test_error_mapping(response, issue):
    with pytest.raises(FrankfurterError) as raised:
        client_for(respond(response)).rate("USD", "INR")
    assert raised.value.issue == issue


@pytest.mark.parametrize("exception", [httpx2.ConnectTimeout, httpx2.ReadTimeout, httpx2.ConnectError,
                                       httpx2.RemoteProtocolError])
def test_timeouts_and_network_failures(exception):
    def fail(request):
        raise exception("down", request=request)

    with pytest.raises(FrankfurterError) as raised:
        client_for(fail).rate("USD", "INR")
    assert raised.value.issue == FxIssue.SERVICE_UNAVAILABLE


def test_timeout_is_ten_seconds():
    client = FrankfurterClient()
    assert client._http.timeout.read == 10.0 and str(client._http.base_url).startswith("https://api.frankfurter.dev")
    client.close()


def test_upstream_messages_never_leak(caplog):
    caplog.set_level(logging.DEBUG)
    leaky = json_response({"status": 422, "message": "invalid currency: UPSTREAM-DETAIL-123"}, 422)
    with pytest.raises(FrankfurterError) as raised:
        client_for(respond(leaky)).rate("USD", "INR")
    rendered = str(raised.value) + repr(raised.value) + caplog.text
    assert "UPSTREAM-DETAIL-123" not in rendered
    assert raised.value.__cause__ is None


def test_currencies_list():
    fake = FakeFrankfurter()
    assert fake.client().currencies() == {"EUR", "INR", "USD", "GBP", "JPY"}
    assert fake.calls == ["/v2/currencies"]


@pytest.mark.parametrize("body", [[], {"iso_code": "EUR"}, [{"iso_code": "eur"}], [{"name": "Euro"}], ["EUR"]])
def test_malformed_currency_lists(body):
    with pytest.raises(FrankfurterError) as raised:
        client_for(respond(json_response(body))).currencies()
    assert raised.value.issue == FxIssue.INVALID_RESPONSE
