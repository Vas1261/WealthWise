"""A deterministic in-memory Frankfurter v2 for offline tests (served through httpx2.MockTransport)."""

import json
from decimal import Decimal

import httpx2

from app.integrations.frankfurter import FrankfurterClient

RATE_DATE = "2026-06-30"


class FakeFrankfurter:
    """rates: (base, quote) -> rate (str/number) | httpx2.Response | Exception; unknown pairs get 422."""

    def __init__(self):
        self.rates: dict[tuple[str, str], object] = {
            ("INR", "EUR"): "0.0105", ("USD", "EUR"): "0.88577", ("INR", "USD"): "0.0104", ("USD", "INR"): "96.14",
        }
        self.currencies: object = ["EUR", "INR", "USD", "GBP", "JPY"]
        self.calls: list[str] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        self.calls.append(path)
        if path == "/v2/currencies":
            if isinstance(self.currencies, (httpx2.Response, Exception)):
                return self._outcome(self.currencies)
            return httpx2.Response(200, json=[{"iso_code": c, "name": c} for c in self.currencies])
        _, _, _, base, quote = path.split("/")
        outcome = self.rates.get((base, quote))
        if outcome is None:
            return httpx2.Response(422, json={"status": 422, "message": f"invalid currency: {base}"})
        if isinstance(outcome, (httpx2.Response, Exception)):
            return self._outcome(outcome)
        body = f'{{"date":"{RATE_DATE}","base":"{base}","quote":"{quote}","rate":{outcome}}}'
        return httpx2.Response(200, content=body.encode(), headers={"content-type": "application/json"})

    @staticmethod
    def _outcome(outcome):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def rate_calls(self) -> list[str]:
        return [c for c in self.calls if c.startswith("/v2/rate/")]

    def client(self) -> FrankfurterClient:
        return FrankfurterClient("https://api.frankfurter.dev", transport=httpx2.MockTransport(self.handle))


def json_response(body, status=200) -> httpx2.Response:
    return httpx2.Response(status, content=json.dumps(body, default=str).encode(),
                           headers={"content-type": "application/json"})


ONE = Decimal(1)
