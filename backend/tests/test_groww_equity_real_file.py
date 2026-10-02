"""Integration test against the real (private, git-ignored) Groww statement, when present locally.

The file holds personal financial data, so every assertion here reports only counts or booleans:
failure output must never contain names, ISINs, quantities, prices, or account identifiers.
"""

from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from app.adapters.groww_equity import GrowwEquityAdapter

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "sample_data" / "groww" / "equity"
REAL_FILES = sorted(SAMPLE_DIR.glob("Stocks_Holdings_Statement_*.xlsx"))

pytestmark = [
    pytest.mark.skipif(not REAL_FILES, reason="private Groww sample file not present (sample_data/ is git-ignored)"),
    # The independent openpyxl read below hits the export's missing default style; harmless.
    pytest.mark.filterwarnings("ignore:Workbook contains no default style"),
]

HEADER = ["Stock Name", "ISIN", "Quantity", "Average buy price", "Buy value", "Closing price"]


def read_file_independently(path: Path) -> list[dict]:
    """Read the statement table directly with openpyxl, without using the adapter's code."""
    sheet = openpyxl.load_workbook(path, data_only=True)["Sheet1"]
    rows = list(sheet.iter_rows(values_only=True))
    header_index = next(i for i, row in enumerate(rows) if all(h in row for h in HEADER))
    columns = {h: rows[header_index].index(h) for h in HEADER}
    return [
        {h: row[columns[h]] for h in HEADER}
        for row in rows[header_index + 1 :]
        if row[columns["ISIN"]] is not None
    ]


@pytest.fixture(scope="module")
def real_file() -> Path:
    return REAL_FILES[0]


@pytest.fixture(scope="module")
def parsed(real_file):
    return GrowwEquityAdapter().parse(str(real_file), "real-file-batch")


@pytest.fixture(scope="module")
def expected(real_file):
    return read_file_independently(real_file)


def test_real_file_is_recognised(real_file):
    assert GrowwEquityAdapter().can_parse(str(real_file)) is True


def test_every_real_row_parses_without_errors(parsed, expected):
    error_count, holding_count, expected_count = len(parsed.errors), len(parsed.holdings), len(expected)
    assert error_count == 0
    assert expected_count > 0
    assert holding_count == expected_count


def test_statement_date_matches_file_name(real_file, parsed):
    # File names end in _YYYY-MM-DD; the adapter reads the date from the sheet's title line instead.
    matches = parsed.statement_date.isoformat() == real_file.stem.rsplit("_", 1)[-1]
    assert matches, "statement date in the sheet differs from the date in the file name"


def test_names_and_isins_map_exactly(parsed, expected):
    mismatches = sum(
        (h.instrument_name, h.isin) != (e["Stock Name"].strip(), e["ISIN"].strip())
        for h, e in zip(parsed.holdings, expected, strict=True)
    )
    assert mismatches == 0


def test_quantity_and_average_buy_price_match_file_exactly(parsed, expected):
    """Spec Module 3 'done when': parsed rows match the file's own Quantity / Average buy price exactly."""
    mismatches = sum(
        not (
            isinstance(h.quantity, Decimal)
            and isinstance(h.average_buy_price, Decimal)
            and float(h.quantity) == e["Quantity"]
            and float(h.average_buy_price) == e["Average buy price"]
        )
        for h, e in zip(parsed.holdings, expected, strict=True)
    )
    assert mismatches == 0


def test_parsed_cost_basis_agrees_with_file_buy_value(parsed, expected):
    """Spec 2.3 sanity check: quantity x average buy price should reproduce the file's 'Buy value'."""
    off_by_more_than_a_paisa = sum(
        abs(h.quantity * h.average_buy_price - Decimal(repr(e["Buy value"]))) > Decimal("0.01")
        for h, e in zip(parsed.holdings, expected, strict=True)
    )
    assert off_by_more_than_a_paisa == 0


def test_closing_price_is_not_used_as_cost_basis(parsed, expected):
    rows_where_prices_differ = [
        (h, e) for h, e in zip(parsed.holdings, expected, strict=True) if e["Closing price"] != e["Average buy price"]
    ]
    took_closing_price = sum(float(h.average_buy_price) == e["Closing price"] for h, e in rows_where_prices_differ)
    assert rows_where_prices_differ, "no row distinguishes closing price from average buy price"
    assert took_closing_price == 0


def test_constant_fields_and_no_personal_data(parsed):
    wrong_constants = sum(
        (h.asset_class, h.native_currency, h.source_broker, h.symbol, h.import_batch_id)
        != ("equity", "INR", "groww", None, "real-file-batch")
        for h in parsed.holdings
    )
    assert wrong_constants == 0
