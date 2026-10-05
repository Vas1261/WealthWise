"""FX service: same-currency short-cut, cache hit/miss/expiry, stale fallback, unavailable (no network)."""

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx2
import pytest

from app.core.config import settings
from app.domain.fx import FxIssue, FxStatus
from app.services import fx_service
from app.services.fx_service import FxUnavailableError
from tests.fake_frankfurter import FakeFrankfurter, json_response

T0 = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def fake():
    return FakeFrankfurter()


def rate(fake, base, quote, at=T0):
    return fx_service.get_rate(base, quote, client=fake.client(), now=at)


def test_same_currency_is_exactly_one_without_request(fake):
    result = rate(fake, "INR", "INR")
    assert (result.status, result.rate, str(result.rate)) == (FxStatus.IDENTITY, Decimal("1"), "1")
    assert fake.calls == []


def test_same_currency_needs_no_client_even_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "fx_enabled", False)
    assert fx_service.get_rate("EUR", "EUR").status == FxStatus.IDENTITY


def test_fresh_rate_then_cache_hit_without_request(fake):
    first = rate(fake, "USD", "INR")
    assert (first.status, first.rate, first.rate_date, first.fetched_at) == (
        FxStatus.FRESH, Decimal("96.14"), date(2026, 6, 30), T0)
    second = rate(fake, "USD", "INR", at=T0 + timedelta(minutes=59))
    assert second == first
    assert fake.rate_calls() == ["/v2/rate/USD/INR"]


def test_cache_expires_after_ttl(fake):
    rate(fake, "USD", "INR")
    fake.rates[("USD", "INR")] = "97.50"
    refreshed = rate(fake, "USD", "INR", at=T0 + timedelta(minutes=60))
    assert (refreshed.status, refreshed.rate, refreshed.fetched_at) == (FxStatus.FRESH, Decimal("97.50"),
                                                                       T0 + timedelta(minutes=60))
    assert len(fake.rate_calls()) == 2


def test_ttl_comes_from_configuration(fake, monkeypatch):
    monkeypatch.setattr(settings, "fx_cache_ttl_minutes", 5)
    rate(fake, "USD", "INR")
    rate(fake, "USD", "INR", at=T0 + timedelta(minutes=5))
    assert len(fake.rate_calls()) == 2


def test_pairs_cached_independently_and_requested_in_needed_direction(fake):
    rate(fake, "USD", "INR")
    rate(fake, "INR", "USD")  # never derived from 1 / 96.14
    rate(fake, "USD", "EUR")
    rate(fake, "USD", "INR", at=T0 + timedelta(minutes=1))
    assert fake.rate_calls() == ["/v2/rate/USD/INR", "/v2/rate/INR/USD", "/v2/rate/USD/EUR"]
    assert rate(fake, "INR", "USD").rate == Decimal("0.0104")


@pytest.mark.parametrize(
    ("failure", "issue"),
    [
        (httpx2.ReadTimeout("slow"), FxIssue.SERVICE_UNAVAILABLE),
        (httpx2.ConnectError("down"), FxIssue.SERVICE_UNAVAILABLE),
        (json_response({"message": "x"}, 500), FxIssue.SERVICE_UNAVAILABLE),
        (json_response({"message": "x"}, 429), FxIssue.RATE_LIMITED),
        (httpx2.Response(200, text="garbage"), FxIssue.INVALID_RESPONSE),
    ],
    ids=["timeout", "network", "http-500", "rate-limited", "malformed"],
)
def test_failed_refresh_serves_last_good_rate_as_stale(fake, failure, issue):
    rate(fake, "USD", "INR")
    fake.rates[("USD", "INR")] = failure
    later = T0 + timedelta(days=30)  # no age limit within the process
    stale = rate(fake, "USD", "INR", at=later)
    assert (stale.status, stale.rate, stale.fetched_at, stale.issue, stale.usable) == (
        FxStatus.STALE, Decimal("96.14"), T0, issue, True)


@pytest.mark.parametrize(
    ("failure", "issue"),
    [(httpx2.ReadTimeout("slow"), FxIssue.SERVICE_UNAVAILABLE),
     (json_response({"message": "x"}, 429), FxIssue.RATE_LIMITED),
     (httpx2.Response(200, text="garbage"), FxIssue.INVALID_RESPONSE)],
)
def test_failure_without_cache_is_unavailable(fake, failure, issue):
    fake.rates[("USD", "INR")] = failure
    result = rate(fake, "USD", "INR")
    assert (result.status, result.rate, result.issue, result.usable) == (FxStatus.UNAVAILABLE, None, issue, False)


def test_unsupported_currency_is_unavailable(fake):
    result = rate(fake, "XYZ", "INR")
    assert (result.status, result.issue) == (FxStatus.UNAVAILABLE, FxIssue.UNSUPPORTED_CURRENCY)


def test_invalid_code_is_unavailable_without_request(fake):
    result = rate(fake, "usd", "INR")
    assert (result.status, result.issue, fake.calls) == (FxStatus.UNAVAILABLE, FxIssue.INVALID_CURRENCY, [])


def test_disabled_makes_no_request_and_still_serves_cache(fake, monkeypatch):
    rate(fake, "USD", "INR")
    monkeypatch.setattr(settings, "fx_enabled", False)
    assert fx_service.default_client() is None
    stale = fx_service.get_rate("USD", "INR", now=T0 + timedelta(hours=2))
    assert (stale.status, stale.issue, stale.rate) == (FxStatus.STALE, FxIssue.DISABLED, Decimal("96.14"))
    missing = fx_service.get_rate("JPY", "INR", now=T0)
    assert (missing.status, missing.issue) == (FxStatus.UNAVAILABLE, FxIssue.DISABLED)
    assert len(fake.calls) == 1


def test_get_rates_one_request_per_distinct_currency(fake):
    rates = fx_service.get_rates(["INR", "USD", "INR", "EUR", "USD"], "EUR", client=fake.client(), now=T0)
    assert list(rates) == ["INR", "USD", "EUR"]
    assert rates["EUR"].status == FxStatus.IDENTITY
    assert sorted(fake.rate_calls()) == ["/v2/rate/INR/EUR", "/v2/rate/USD/EUR"]


def test_failures_log_issue_codes_only(fake, caplog):
    caplog.set_level(logging.DEBUG)
    fake.rates[("USD", "INR")] = json_response({"message": "SECRET-UPSTREAM-TEXT"}, 500)
    rate(fake, "USD", "INR")
    assert "service_unavailable" in caplog.text and "SECRET-UPSTREAM-TEXT" not in caplog.text


# --- Supported currencies (for PATCH /api/auth/me) -------------------------------------------------------

def test_supported_currencies_cached_for_a_day(fake):
    assert "EUR" in fx_service.supported_currencies(client=fake.client(), now=T0)
    fx_service.supported_currencies(client=fake.client(), now=T0 + timedelta(hours=23))
    assert fake.calls == ["/v2/currencies"]
    fx_service.supported_currencies(client=fake.client(), now=T0 + timedelta(hours=24))
    assert fake.calls == ["/v2/currencies", "/v2/currencies"]


def test_supported_currencies_fall_back_to_last_list(fake):
    fx_service.supported_currencies(client=fake.client(), now=T0)
    fake.currencies = httpx2.Response(500)
    assert "INR" in fx_service.supported_currencies(client=fake.client(), now=T0 + timedelta(days=2))


def test_supported_currencies_unavailable_without_list(fake, monkeypatch):
    fake.currencies = httpx2.Response(503)
    with pytest.raises(FxUnavailableError):
        fx_service.supported_currencies(client=fake.client(), now=T0)
    monkeypatch.setattr(settings, "fx_enabled", False)
    with pytest.raises(FxUnavailableError) as raised:
        fx_service.supported_currencies(now=T0)
    assert raised.value.issue == FxIssue.DISABLED
