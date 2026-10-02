"""Integration test against the real (private, git-ignored) Zerodha workbook, when present locally.

The file holds personal financial data, so every assertion here reports only counts or booleans:
failure output must never contain symbols, ISINs, quantities, prices, dates, or the client ID.
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from app.adapters.groww_equity import GrowwEquityAdapter
from app.adapters.zerodha_equity import ZerodhaEquityAdapter

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "sample_data" / "zerodha" / "equity"
REAL_FILES = sorted(SAMPLE_DIR.glob("holdings-*.xlsx"))

pytestmark = [
    pytest.mark.skipif(not REAL_FILES, reason="private Zerodha sample file not present (sample_data/ is git-ignored)"),
    # The independent openpyxl read below hits the export's missing default style; harmless.
    pytest.mark.filterwarnings("ignore:Workbook contains no default style"),
]

HEADER = ["Symbol", "ISIN", "Instrument Type", "Quantity Available", "Average Price", "Previous Closing Price",
          "Unrealized P&L"]


def read_combined_independently(path: Path) -> tuple[date, list[dict]]:
    """Read the Combined sheet directly with openpyxl, without using the adapter's code."""
    rows = list(openpyxl.load_workbook(path, data_only=True)["Combined"].iter_rows(values_only=True))
    title = next(c for r in rows for c in r if isinstance(c, str) and c.strip().startswith("Combined Holdings"))
    statement_date = date.fromisoformat(re.search(r"\d{4}-\d{2}-\d{2}", title).group())
    header_index = next(i for i, row in enumerate(rows) if all(h in row for h in HEADER))
    columns = {h: rows[header_index].index(h) for h in HEADER}
    data = [{h: row[columns[h]] for h in HEADER} for row in rows[header_index + 1 :] if row[columns["ISIN"]]]
    return statement_date, data


@pytest.fixture(scope="module")
def real_file() -> Path:
    return REAL_FILES[0]


@pytest.fixture(scope="module")
def parsed(real_file):
    return ZerodhaEquityAdapter().parse(str(real_file), "real-file-batch")


@pytest.fixture(scope="module")
def expected(real_file):
    return read_combined_independently(real_file)


def test_real_file_recognised_by_zerodha_and_not_groww(real_file):
    assert ZerodhaEquityAdapter().can_parse(str(real_file)) is True
    assert GrowwEquityAdapter().can_parse(str(real_file)) is False


def test_every_real_row_parses_without_errors(parsed, expected):
    _, rows = expected
    error_count, holding_count, expected_count = len(parsed.errors), len(parsed.holdings), len(rows)
    assert error_count == 0
    assert expected_count > 0
    assert holding_count == expected_count


def test_combined_sheet_selected_and_date_from_its_title(parsed, expected):
    statement_date, rows = expected
    same_date = parsed.statement_date == statement_date
    all_marked_equity = all(r["Instrument Type"] == "-" for r in rows)
    assert same_date, "statement date differs from the Combined sheet's title"
    assert all_marked_equity


def test_symbols_and_isins_map_exactly(parsed, expected):
    _, rows = expected
    mismatches = sum(
        (h.symbol, h.instrument_name, h.isin) != (e["Symbol"], e["Symbol"], e["ISIN"])
        for h, e in zip(parsed.holdings, rows, strict=True)
    )
    assert mismatches == 0


def test_quantity_and_average_price_match_file_exactly(parsed, expected):
    _, rows = expected
    mismatches = sum(
        not (
            isinstance(h.quantity, Decimal)
            and isinstance(h.average_buy_price, Decimal)
            and float(h.quantity) == e["Quantity Available"]
            and float(h.average_buy_price) == e["Average Price"]
        )
        for h, e in zip(parsed.holdings, rows, strict=True)
    )
    assert mismatches == 0


def test_parsed_cost_basis_agrees_with_file_pnl(parsed, expected):
    """Spec 2.3 sanity check: (previous close - average price) x quantity should reproduce the file's P&L."""
    _, rows = expected
    off_by_more_than_a_paisa = sum(
        abs((Decimal(repr(e["Previous Closing Price"])) - h.average_buy_price) * h.quantity
            - Decimal(repr(e["Unrealized P&L"]))) > Decimal("0.01")
        for h, e in zip(parsed.holdings, rows, strict=True)
    )
    assert off_by_more_than_a_paisa == 0


def test_previous_close_is_not_used_as_cost_basis(parsed, expected):
    _, rows = expected
    differing = [(h, e) for h, e in zip(parsed.holdings, rows, strict=True)
                 if e["Previous Closing Price"] != e["Average Price"]]
    took_previous_close = sum(float(h.average_buy_price) == e["Previous Closing Price"] for h, e in differing)
    assert differing, "no row distinguishes previous close from average price"
    assert took_previous_close == 0


def test_constant_fields(parsed):
    wrong = sum(
        (h.asset_class, h.native_currency, h.source_broker, h.import_batch_id)
        != ("equity", "INR", "zerodha", "real-file-batch")
        for h in parsed.holdings
    )
    assert wrong == 0
