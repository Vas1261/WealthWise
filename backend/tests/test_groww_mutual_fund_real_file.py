"""Integration test against the real (private, git-ignored) Groww mutual fund order history, when present.

The file holds personal and financial data, so expected results are computed from the workbook at
test time (never copied into this file) and every assertion reports only counts or booleans:
failure output must never contain scheme names, units, NAVs, amounts, dates, or personal details.
"""

import re
from collections import Counter
from datetime import datetime
from decimal import Context, Decimal
from pathlib import Path

import openpyxl
import pytest

from app.adapters.groww_equity import GrowwEquityAdapter
from app.adapters.groww_mutual_fund import GrowwMutualFundAdapter
from app.adapters.zerodha_equity import ZerodhaEquityAdapter

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "sample_data" / "groww" / "mutual_funds"
REAL_FILES = sorted(SAMPLE_DIR.glob("Mutual_Funds_Order_History_*.xlsx"))

pytestmark = [
    pytest.mark.skipif(not REAL_FILES, reason="private Groww MF sample file not present (sample_data/ is git-ignored)"),
    # The independent openpyxl read below hits the export's missing default style; harmless.
    pytest.mark.filterwarnings("ignore:Workbook contains no default style"),
]

HEADER = ["Scheme Name", "Transaction Type", "Units", "NAV", "Amount", "Date"]
PERSONAL_LABELS = ("Name", "Mobile Number", "PAN")


def number(text) -> Decimal:
    return Decimal(str(text).strip().replace(",", ""))


def read_independently(path: Path) -> dict:
    """Read the Transactions sheet directly and apply the B3 rule, without using the adapter's code."""
    rows = list(openpyxl.load_workbook(path, data_only=True)["Transactions"].iter_rows(values_only=True))
    title = next(c for r in rows for c in r if isinstance(c, str) and c.strip().startswith("TRANSACTIONS FROM"))
    to_date = datetime.strptime(re.search(r"TO (\w{3} \d{2} \d{4})", title).group(1), "%b %d %Y").date()
    header_index = next(i for i, row in enumerate(rows) if all(h in row for h in HEADER))
    col = {h: rows[header_index].index(h) for h in HEADER}
    data = [r for r in rows[header_index + 1 :] if r[col["Scheme Name"]]]

    ctx = Context(prec=28)
    schemes: dict[str, dict[str, Decimal]] = {}
    for r in data:
        s = schemes.setdefault(" ".join(r[col["Scheme Name"]].split()),
                               {"bought": Decimal(0), "cost": Decimal(0), "sold": Decimal(0)})
        units, nav = number(r[col["Units"]]), number(r[col["NAV"]])
        if r[col["Transaction Type"]].strip() == "PURCHASE":
            s["bought"] += units
            s["cost"] += units * nav
        else:
            s["sold"] += units
    expected = [
        (name, s["bought"] - s["sold"], ctx.divide(s["cost"], s["bought"]))
        for name, s in schemes.items()
        if s["bought"] - s["sold"] > 0
    ]
    personal = [
        str(r[i + 1]) for r in rows[:header_index] for i, c in enumerate(r[:-1])
        if isinstance(c, str) and c.strip() in PERSONAL_LABELS and r[i + 1] is not None
    ]
    return {
        "to_date": to_date,
        "types": Counter(r[col["Transaction Type"]].strip() for r in data),
        "scheme_count": len(schemes),
        "expected": expected,
        "personal": personal,
    }


@pytest.fixture(scope="module")
def real_file() -> Path:
    return REAL_FILES[0]


@pytest.fixture(scope="module")
def parsed(real_file):
    return GrowwMutualFundAdapter().parse(str(real_file), "real-file-batch")


@pytest.fixture(scope="module")
def independent(real_file):
    return read_independently(real_file)


def test_recognised_only_by_the_mutual_fund_adapter(real_file):
    assert GrowwMutualFundAdapter().can_parse(str(real_file)) is True
    assert GrowwEquityAdapter().can_parse(str(real_file)) is False
    assert ZerodhaEquityAdapter().can_parse(str(real_file)) is False


def test_transactions_structure_matches_observations(independent):
    """Observed when the sample was inspected: 9 schemes, 187 PURCHASE and 31 REDEEM rows, nothing else."""
    types = independent["types"]
    assert independent["scheme_count"] == 9
    assert (types["PURCHASE"], types["REDEEM"], len(types)) == (187, 31, 2)


def test_every_row_parses_without_errors(parsed):
    error_count = len(parsed.errors)
    assert error_count == 0


def test_statement_date_is_title_to_date(parsed, independent):
    same = parsed.statement_date == independent["to_date"]
    assert same, "statement date differs from the title's TO date"


def test_holdings_match_independent_b3_calculation(parsed, independent):
    expected = independent["expected"]
    actual = [(h.instrument_name, h.quantity, h.average_buy_price) for h in parsed.holdings]
    holding_count, expected_count = len(actual), len(expected)
    assert expected_count > 0
    assert holding_count == expected_count
    mismatches = sum(a != e for a, e in zip(actual, expected, strict=True))
    assert mismatches == 0


def test_fully_redeemed_schemes_are_excluded(parsed, independent):
    excluded = independent["scheme_count"] - len(independent["expected"])
    held_with_non_positive_units = sum(h.quantity <= 0 for h in parsed.holdings)
    assert excluded == independent["scheme_count"] - len(parsed.holdings)
    assert held_with_non_positive_units == 0


def test_values_are_decimals_with_fixed_fields(parsed):
    wrong = sum(
        not (isinstance(h.quantity, Decimal) and isinstance(h.average_buy_price, Decimal))
        or (h.isin, h.symbol, h.asset_class, h.native_currency, h.source_broker, h.import_batch_id)
        != (None, None, "mutual_fund", "INR", "groww", "real-file-batch")
        for h in parsed.holdings
    )
    assert wrong == 0


def test_no_personal_details_in_output(parsed, independent):
    personal = independent["personal"]
    rendered = repr(parsed.holdings) + repr(parsed.errors)
    leaked = sum(value in rendered for value in personal)
    assert len(personal) == len(PERSONAL_LABELS), "personal details block not found where expected"
    assert leaked == 0
