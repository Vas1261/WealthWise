"""Zerodha equity adapter tests (D1 rule) against small synthetic .xlsx files generated per test."""

import os
from datetime import date, datetime
from decimal import Decimal

import openpyxl
import pytest

from app.adapters.base import BrokerAdapter, StatementFormatError
from app.adapters.groww_equity import GrowwEquityAdapter
from app.adapters.zerodha_equity import ZerodhaEquityAdapter
from app.domain.holdings import NormalizedHolding

BATCH_ID = "batch-456"
DATE = "2026-06-30"
COMBINED_TITLE = f"Combined Holdings Statement as on {DATE}"
EQUITY_TITLE = f"Equity Holdings Statement as on {DATE}"
MF_TITLE = f"Mutual Funds Holdings Statement as on {DATE}"

COMBINED_HEADER = [
    "Symbol", "ISIN", "Sector", "Instrument Type", "Quantity Available", "Quantity Discrepant",
    "Quantity Long Term", "Quantity Pledged (Margin)", "Quantity Pledged (Loan)", "Average Price",
    "Previous Closing Price", "Unrealized P&L", "Unrealize P&L Pct.",
]
EQUITY_HEADER = [h for h in COMBINED_HEADER if h != "Instrument Type"][:-1] + ["Unrealized P&L Pct."]
MF_HEADER = [
    "Symbol", "ISIN", "Instrument Type", "Quantity Available", "Quantity Discrepant", "Quantity Pledged (Margin)",
    "Quantity Pledged (Loan)", "Average Price", "Previous Closing Price", "Unrealized P&L", "Unrealized P&L Pct.",
]

# Invented holdings. Previous closing price and P&L deliberately differ from the average price.
HOLDING_X = {
    "Symbol": "SYNTHA", "ISIN": "INE000D01019", "Sector": "SYNTHETIC SECTOR", "Instrument Type": "-",
    "Quantity Available": 14.0, "Quantity Discrepant": 0.0, "Quantity Long Term": 14.0,
    "Quantity Pledged (Margin)": 0.0, "Quantity Pledged (Loan)": 0.0, "Average Price": 412.3871,
    "Previous Closing Price": 455.5, "Unrealized P&L": 603.58, "Unrealize P&L Pct.": 10.4545,
    "Unrealized P&L Pct.": 10.4545,
}
HOLDING_Y = {
    **HOLDING_X, "Symbol": "SYNTHB-BE", "ISIN": "INF000E01027", "Quantity Available": 3,
    "Quantity Long Term": 0.0, "Average Price": 1875.25, "Previous Closing Price": 1702.0,
}


def holding(**changes) -> dict:
    return {**HOLDING_X, **changes}


def sheet_rows(data, *, header=COMBINED_HEADER, title=COMBINED_TITLE, client_id=True, extra_rows=()):
    """The real Zerodha layout (column A is added empty by the writer): header lands on row 23."""
    rows = [[] for _ in range(6)]
    rows.append(["Client ID", "ZZ0000"] if client_id else [])  # row 7
    rows += [[], [], []]
    rows.append([title] if title else [])  # row 11
    rows += [[], ["Summary"], []]
    rows += [
        ["Invested Value", 1000.0], ["Present Value", 1087.35],
        ["Unrealized P&L", 87.35], ["Unrealized P&L Pct.", 8.735],
    ]
    rows += [[], [], ["   "], []]  # row 21 holds a whitespace-only cell, as in the real export
    rows += list(extra_rows)
    rows.append(header)
    rows += [[d.get(h) for h in header] for d in data]
    return rows


HEADER_ROW = 23  # in sheet_rows' default layout (as in the real file); data starts on row 24


@pytest.fixture
def write_xlsx(tmp_path):
    def write(sheets: dict[str, list[list]], *, column_offset: int = 1, name: str = "holdings-ZZ0000.xlsx") -> str:
        workbook = openpyxl.Workbook()
        workbook.remove(workbook.active)
        for sheet_name, rows in sheets.items():
            sheet = workbook.create_sheet(sheet_name)
            for row_number, row in enumerate(rows, start=1):
                for column_number, value in enumerate(row, start=1 + column_offset):
                    if value is not None:
                        sheet.cell(row_number, column_number, value)
        path = tmp_path / name
        workbook.save(path)
        return str(path)

    return write


@pytest.fixture
def workbook(write_xlsx):
    """The real three-sheet structure: Equity, Mutual Funds (empty), Combined."""

    def make(combined=(HOLDING_X,), equity=(HOLDING_X,), **combined_layout) -> str:
        return write_xlsx({
            "Equity": sheet_rows(equity, header=EQUITY_HEADER, title=EQUITY_TITLE),
            "Mutual Funds": sheet_rows([], header=MF_HEADER, title=MF_TITLE),
            "Combined": sheet_rows(combined, **combined_layout),
        })

    return make


adapter = ZerodhaEquityAdapter()


def expected_holding(source: dict, *, quantity: str, price: str) -> NormalizedHolding:
    return NormalizedHolding(
        instrument_name=source["Symbol"], isin=source["ISIN"], symbol=source["Symbol"], asset_class="equity",
        quantity=Decimal(quantity), average_buy_price=Decimal(price),
        native_currency="INR", source_broker="zerodha", import_batch_id=BATCH_ID,
    )


# --- Interface and recognition --------------------------------------------------------------

def test_implements_broker_adapter_interface():
    assert isinstance(adapter, BrokerAdapter)


def test_can_parse_recognises_zerodha_workbook(workbook):
    assert adapter.can_parse(workbook()) is True


def test_can_parse_rejects_other_files(write_xlsx, tmp_path):
    groww = write_xlsx({"Sheet1": [["Unique Client Code", "X1"], ["Holdings statement for stocks as on 01-09-2026"],
                                   ["Stock Name", "ISIN", "Quantity", "Average buy price"]]}, name="g.xlsx")
    not_xlsx = tmp_path / "notes.xlsx"
    not_xlsx.write_text("not a spreadsheet")
    for path in (groww, str(not_xlsx), str(tmp_path / "missing.xlsx")):
        assert adapter.can_parse(path) is False


def test_groww_adapter_does_not_claim_zerodha_workbook(workbook):
    assert GrowwEquityAdapter().can_parse(workbook()) is False


def test_parse_rejects_unreadable_file(tmp_path):
    not_xlsx = tmp_path / "notes.xlsx"
    not_xlsx.write_text("not a spreadsheet")
    with pytest.raises(StatementFormatError, match="readable"):
        adapter.parse(str(not_xlsx), BATCH_ID)


# --- A. Valid Combined workbook --------------------------------------------------------------

def test_valid_combined_workbook(workbook):
    result = adapter.parse(workbook(combined=[HOLDING_X, HOLDING_Y]), BATCH_ID)
    assert result.errors == []
    assert result.statement_date == date(2026, 6, 30)
    assert result.holdings == [
        expected_holding(HOLDING_X, quantity="14.0", price="412.3871"),
        expected_holding(HOLDING_Y, quantity="3", price="1875.25"),
    ]


def test_values_are_exact_decimals_and_unmapped_columns_unused(workbook):
    h = adapter.parse(workbook(), BATCH_ID).holdings[0]
    assert type(h.quantity) is Decimal and type(h.average_buy_price) is Decimal
    assert str(h.average_buy_price) == "412.3871"  # 'Average Price', not 'Previous Closing Price' (455.5)
    assert h.quantity == Decimal("14")  # 'Quantity Available', not 'Quantity Long Term' or pledged columns


def test_quantity_available_is_used_even_when_other_quantity_columns_differ(workbook):
    row = holding(**{"Quantity Available": 9.0, "Quantity Long Term": 4.0, "Quantity Pledged (Margin)": 2.0})
    assert adapter.parse(workbook(combined=[row]), BATCH_ID).holdings[0].quantity == Decimal("9.0")


# --- B/C/J. Sheet selection and priority ----------------------------------------------------

def test_combined_preferred_when_both_qualify(workbook):
    result = adapter.parse(workbook(combined=[HOLDING_X], equity=[HOLDING_Y]), BATCH_ID)
    assert [h.symbol for h in result.holdings] == ["SYNTHA"]


@pytest.mark.parametrize(
    "combined_layout",
    [
        {"client_id": False},
        {"header": [h for h in COMBINED_HEADER if h != "Average Price"]},
        {"title": None},
        {"title": "Combined Holdings Statement as on 2026-02-30"},
        {"title": "Combined Holdings Statement as on 30-06-2026"},
        {"extra_rows": [[COMBINED_TITLE]]},
    ],
    ids=[
        "combined-without-client-id", "combined-missing-required-header", "combined-without-title",
        "combined-impossible-date", "combined-non-iso-date", "combined-duplicated-title",
    ],
)
def test_equity_fallback_when_combined_does_not_qualify(workbook, combined_layout):
    result = adapter.parse(workbook(combined=[HOLDING_X], equity=[HOLDING_Y], **combined_layout), BATCH_ID)
    assert [h.symbol for h in result.holdings] == ["SYNTHB-BE"]


def test_combined_without_instrument_type_column_falls_back_to_equity(workbook):
    no_instrument_type = [h for h in COMBINED_HEADER if h != "Instrument Type"]
    result = adapter.parse(workbook(combined=[HOLDING_X], equity=[HOLDING_Y], header=no_instrument_type), BATCH_ID)
    assert [h.symbol for h in result.holdings] == ["SYNTHB-BE"]
    assert result.errors == []  # Equity rows need no Instrument Type


def test_combined_without_instrument_type_and_no_equity_sheet_is_rejected(write_xlsx):
    no_instrument_type = [h for h in COMBINED_HEADER if h != "Instrument Type"]
    path = write_xlsx({"Combined": sheet_rows([HOLDING_X], header=no_instrument_type)})
    with pytest.raises(StatementFormatError, match="not a Zerodha equity holdings statement"):
        adapter.parse(path, BATCH_ID)


def test_selection_uses_title_not_sheet_name(write_xlsx):
    path = write_xlsx({
        "Combined": sheet_rows([HOLDING_Y], header=EQUITY_HEADER, title=EQUITY_TITLE),  # misleading name
        "Sheet A": sheet_rows([HOLDING_X]),  # the real Combined statement, under another name
    })
    assert [h.symbol for h in adapter.parse(path, BATCH_ID).holdings] == ["SYNTHA"]


def test_only_the_selected_sheet_is_read(workbook):
    result = adapter.parse(workbook(combined=[HOLDING_X], equity=[HOLDING_X, HOLDING_Y]), BATCH_ID)
    assert len(result.holdings) == 1  # Equity rows are not merged in


# --- D/E/F/H. Rejection of non-qualifying workbooks -------------------------------------------

def test_mutual_funds_title_never_qualifies(write_xlsx):
    rows = sheet_rows([holding()], header=COMBINED_HEADER, title=MF_TITLE)  # even with an equity-looking table
    path = write_xlsx({"Mutual Funds": rows})
    assert adapter.can_parse(path) is False
    with pytest.raises(StatementFormatError):
        adapter.parse(path, BATCH_ID)


def test_mutual_funds_sheet_ignored_next_to_valid_equity_sheet(write_xlsx):
    path = write_xlsx({
        "Mutual Funds": sheet_rows([HOLDING_X], header=MF_HEADER, title=MF_TITLE),
        "Equity": sheet_rows([HOLDING_Y], header=EQUITY_HEADER, title=EQUITY_TITLE),
    })
    assert [h.symbol for h in adapter.parse(path, BATCH_ID).holdings] == ["SYNTHB-BE"]


def test_missing_client_id_rejected(write_xlsx):
    path = write_xlsx({"Combined": sheet_rows([HOLDING_X], client_id=False)})
    assert adapter.can_parse(path) is False
    with pytest.raises(StatementFormatError, match="Client ID"):
        adapter.parse(path, BATCH_ID)


@pytest.mark.parametrize("missing", ["Symbol", "ISIN", "Quantity Available", "Average Price"])
def test_missing_required_header_rejected(write_xlsx, missing):
    path = write_xlsx({"Combined": sheet_rows([HOLDING_X], header=[h for h in COMBINED_HEADER if h != missing])})
    with pytest.raises(StatementFormatError, match="not a Zerodha equity holdings statement"):
        adapter.parse(path, BATCH_ID)


def test_header_above_title_does_not_count(write_xlsx):
    rows = sheet_rows([])
    rows[0] = COMBINED_HEADER  # a complete header, but above the title
    rows[HEADER_ROW - 1] = ["Not the header"]
    with pytest.raises(StatementFormatError, match="not a Zerodha equity holdings statement"):
        adapter.parse(write_xlsx({"Combined": rows}), BATCH_ID)


@pytest.mark.parametrize(
    "title",
    [
        f"Holdings Statement as on {DATE}",
        f"combined holdings statement as on {DATE}",
        f"COMBINED HOLDINGS STATEMENT AS ON {DATE}",
        f"Combined Holding Statement as on {DATE}",
        f"Combined Holdings Statement as at {DATE}",
        f"Combined Holdings Statement on {DATE}",
        f"Stocks Holdings Statement as on {DATE}",
        f"Combined Holdings Statement as on {DATE} (provisional)",
    ],
)
def test_unsupported_title_wording_rejected(write_xlsx, title):
    path = write_xlsx({"Combined": sheet_rows([HOLDING_X], title=title)})
    assert adapter.can_parse(path) is False
    with pytest.raises(StatementFormatError):
        adapter.parse(path, BATCH_ID)


# --- G/I. Invalid, duplicated and ambiguous titles -------------------------------------------

@pytest.mark.parametrize(
    "bad_date",
    ["2026-13-01", "2026-02-30", "02-09-2026", "2026-9-2", "2026/09/02", "20260902", "", "2026-06-30T00:00"],
)
def test_invalid_statement_date_rejected(write_xlsx, bad_date):
    path = write_xlsx({"Combined": sheet_rows([HOLDING_X], title=f"Combined Holdings Statement as on {bad_date}")})
    assert adapter.can_parse(path) is False
    with pytest.raises(StatementFormatError, match="YYYY-MM-DD"):
        adapter.parse(path, BATCH_ID)


def test_valid_combined_wins_over_malformed_equity(write_xlsx):
    path = write_xlsx({
        "Equity": sheet_rows([HOLDING_Y], header=EQUITY_HEADER, title="Equity Holdings Statement as on 2026-02-30"),
        "Combined": sheet_rows([HOLDING_X]),
    })
    result = adapter.parse(path, BATCH_ID)
    assert ([h.symbol for h in result.holdings], result.statement_date) == (["SYNTHA"], date(2026, 6, 30))


@pytest.mark.parametrize(
    "unrelated",
    [
        ["Equity Holdings Statement as on not-a-date"],
        ["Combined Holdings Statement as on 2026-13-01"],
        [MF_TITLE],
        ["combined holdings statement as on 2026-06-30"],
        [COMBINED_TITLE, COMBINED_TITLE],
    ],
    ids=["equity-title-bad-date", "combined-title-bad-date", "mutual-funds-title", "wrong-case", "duplicated-title"],
)
def test_malformed_unrelated_sheet_does_not_block_valid_combined(write_xlsx, unrelated):
    path = write_xlsx({"Notes": [[cell] for cell in unrelated], "Combined": sheet_rows([HOLDING_X])})
    assert adapter.can_parse(path) is True
    assert [h.symbol for h in adapter.parse(path, BATCH_ID).holdings] == ["SYNTHA"]


def test_two_qualifying_equity_sheets_without_combined_rejected(write_xlsx):
    equity = sheet_rows([HOLDING_Y], header=EQUITY_HEADER, title=EQUITY_TITLE)
    path = write_xlsx({"Equity": equity, "Equity (2)": equity, "Combined": sheet_rows([HOLDING_X], client_id=False)})
    assert adapter.can_parse(path) is False
    with pytest.raises(StatementFormatError, match="ambiguous: more than one qualifying Equity"):
        adapter.parse(path, BATCH_ID)


def test_two_equity_sheets_do_not_matter_when_one_combined_qualifies(write_xlsx):
    equity = sheet_rows([HOLDING_Y], header=EQUITY_HEADER, title=EQUITY_TITLE)
    path = write_xlsx({"Equity": equity, "Equity (2)": equity, "Combined": sheet_rows([HOLDING_X])})
    assert [h.symbol for h in adapter.parse(path, BATCH_ID).holdings] == ["SYNTHA"]


def test_rejection_message_explains_non_qualifying_sheets(write_xlsx):
    path = write_xlsx({
        "Combined": sheet_rows([HOLDING_X], title="Combined Holdings Statement as on 2026-02-30"),
        "Equity": sheet_rows([HOLDING_Y], header=EQUITY_HEADER, title=EQUITY_TITLE, client_id=False),
    })
    with pytest.raises(StatementFormatError) as rejected:
        adapter.parse(path, BATCH_ID)
    message = str(rejected.value)
    assert "sheet 'Combined' has a statement date that is not a valid YYYY-MM-DD date" in message
    assert "sheet 'Equity' has no 'Client ID' label" in message


@pytest.mark.parametrize("second_title", [COMBINED_TITLE, EQUITY_TITLE, "Combined Holdings Statement as on 2026-09-03"])
def test_duplicated_title_on_one_sheet_rejected(write_xlsx, second_title):
    rows = sheet_rows([HOLDING_X], extra_rows=[[second_title]])
    with pytest.raises(StatementFormatError, match="more than one holdings statement title"):
        adapter.parse(write_xlsx({"Combined": rows}), BATCH_ID)


def test_two_qualifying_combined_sheets_rejected(write_xlsx):
    path = write_xlsx({"Combined": sheet_rows([HOLDING_X]), "Combined (2)": sheet_rows([HOLDING_Y])})
    assert adapter.can_parse(path) is False
    with pytest.raises(StatementFormatError, match="ambiguous: more than one qualifying Combined"):
        adapter.parse(path, BATCH_ID)


# --- K/L/M. Combined Instrument Type ----------------------------------------------------------

@pytest.mark.parametrize("marker", ["-", " - "])
def test_combined_equity_marker_accepted(workbook, marker):
    result = adapter.parse(workbook(combined=[holding(**{"Instrument Type": marker})]), BATCH_ID)
    assert (len(result.holdings), result.errors) == (1, [])


@pytest.mark.parametrize("instrument_type", ["MF", "ETF", "EQ", "--", "–", 0, None, "   "])
def test_combined_other_or_blank_instrument_type_is_a_row_error(workbook, instrument_type):
    rows = [HOLDING_X, holding(**{"Symbol": "SYNTHC", "Instrument Type": instrument_type}), HOLDING_X]
    result = adapter.parse(workbook(combined=rows), BATCH_ID)
    assert [h.symbol for h in result.holdings] == ["SYNTHA", "SYNTHA"]  # the bad row is not imported
    assert [(e.row_number, e.field) for e in result.errors] == [(HEADER_ROW + 2, "Instrument Type")]
    assert "not supported" in result.errors[0].message


def test_instrument_type_not_classified_by_isin_prefix(workbook):
    # INF ISINs (e.g. ETFs) marked '-' are equity rows; an INE ISIN with another type is still rejected.
    rows = [holding(ISIN="INF000F01025"), holding(ISIN="INE000G01033", **{"Instrument Type": "MF"})]
    result = adapter.parse(workbook(combined=rows), BATCH_ID)
    assert [h.isin for h in result.holdings] == ["INF000F01025"]
    assert [e.field for e in result.errors] == ["Instrument Type"]


# --- N/O/P/Q/R/S. Row validation ----------------------------------------------------------------

@pytest.mark.parametrize(
    ("changes", "field", "message"),
    [
        ({"Symbol": None}, "Symbol", "Symbol is missing"),
        ({"Symbol": "   "}, "Symbol", "Symbol is missing"),
        ({"Symbol": 532540}, "Symbol", "Symbol is missing"),
        ({"ISIN": None}, "ISIN", "ISIN is missing"),
        ({"ISIN": "INE000D0101"}, "ISIN", "ISIN is not a valid 12-character ISIN"),
        ({"ISIN": "ine000d01019"}, "ISIN", "ISIN is not a valid 12-character ISIN"),
        ({"ISIN": "INE000D0101X"}, "ISIN", "ISIN is not a valid 12-character ISIN"),
        ({"Quantity Available": 0}, "Quantity Available", "Quantity Available must be greater than zero"),
        ({"Quantity Available": 0.0}, "Quantity Available", "Quantity Available must be greater than zero"),
        ({"Quantity Available": -3.0}, "Quantity Available", "Quantity Available must be greater than zero"),
        ({"Quantity Available": None}, "Quantity Available", "Quantity Available is missing or not a number"),
        ({"Quantity Available": "abc"}, "Quantity Available", "Quantity Available is not a number"),
        ({"Average Price": -0.01}, "Average Price", "Average Price must not be negative"),
        ({"Average Price": None}, "Average Price", "Average Price is missing or not a number"),
        ({"Average Price": "N/A"}, "Average Price", "Average Price is not a number"),
    ],
)
def test_invalid_value_is_a_row_error_and_other_rows_still_parse(workbook, changes, field, message):
    result = adapter.parse(workbook(combined=[HOLDING_X, holding(**changes), HOLDING_Y]), BATCH_ID)
    assert [h.symbol for h in result.holdings] == ["SYNTHA", "SYNTHB-BE"]
    assert [(e.row_number, e.field, e.message) for e in result.errors] == [(HEADER_ROW + 2, field, message)]
    assert result.failed_row_count == 1


def test_zero_average_price_accepted(workbook):
    result = adapter.parse(workbook(combined=[holding(**{"Average Price": 0})]), BATCH_ID)
    assert result.errors == []
    assert result.holdings[0].average_buy_price == Decimal("0")


def test_mixed_rows_report_every_invalid_field(workbook):
    bad = holding(Symbol=None, ISIN="BAD", **{"Instrument Type": "MF", "Quantity Available": -1, "Average Price": -1})
    result = adapter.parse(workbook(combined=[bad, HOLDING_Y]), BATCH_ID)
    assert [h.symbol for h in result.holdings] == ["SYNTHB-BE"]
    assert [e.field for e in result.errors] == [
        "Instrument Type", "Symbol", "ISIN", "Quantity Available", "Average Price",
    ]
    assert {e.row_number for e in result.errors} == {HEADER_ROW + 1}
    assert result.failed_row_count == 1


def test_equity_sheet_rows_validated_without_instrument_type(write_xlsx):
    path = write_xlsx({"Equity": sheet_rows([HOLDING_Y, holding(**{"Average Price": -5})],
                                            header=EQUITY_HEADER, title=EQUITY_TITLE)})
    result = adapter.parse(path, BATCH_ID)
    assert [h.symbol for h in result.holdings] == ["SYNTHB-BE"]
    assert [e.field for e in result.errors] == ["Average Price"]


def test_error_messages_never_echo_cell_values(workbook):
    secret = "ACCOUNT-SECRET-77"
    row = {h: secret for h in COMBINED_HEADER}
    result = adapter.parse(workbook(combined=[row]), BATCH_ID)
    assert result.errors and all(secret not in e.message for e in result.errors)


# --- T. Empty table, blank rows -----------------------------------------------------------------

def test_empty_holdings_table(workbook):
    result = adapter.parse(workbook(combined=[]), BATCH_ID)
    assert (result.holdings, result.errors, result.statement_date) == ([], [], date(2026, 6, 30))


def test_blank_and_whitespace_rows_skipped(workbook):
    blank = {h: None for h in COMBINED_HEADER}
    spaces = {h: "  " for h in COMBINED_HEADER}
    result = adapter.parse(workbook(combined=[blank, HOLDING_X, spaces, blank, HOLDING_Y, blank]), BATCH_ID)
    assert (len(result.holdings), result.errors) == (2, [])


# --- U/V. Layout and whitespace -----------------------------------------------------------------

@pytest.mark.parametrize("column_offset", [0, 1, 4])
def test_header_found_in_any_column(write_xlsx, column_offset):
    path = write_xlsx({"Combined": sheet_rows([HOLDING_X])}, column_offset=column_offset)
    assert [h.symbol for h in adapter.parse(path, BATCH_ID).holdings] == ["SYNTHA"]


def test_row_numbers_follow_shifted_header(write_xlsx):
    extra = [["note"], [], ["another note"]]
    path = write_xlsx({"Combined": sheet_rows([holding(Symbol=None)], extra_rows=extra)}, column_offset=3)
    assert [e.row_number for e in adapter.parse(path, BATCH_ID).errors] == [HEADER_ROW + len(extra) + 1]


def test_whitespace_in_title_label_and_header_is_collapsed(write_xlsx):
    header = [f"  {h.replace(' ', '   ')} " for h in COMBINED_HEADER]
    rows = sheet_rows([], header=header, title="  Combined   Holdings Statement\tas on  2026-06-30 ")
    rows[6] = [" Client   ID ", "ZZ0000"]
    rows.append([HOLDING_X[h] for h in COMBINED_HEADER])
    result = adapter.parse(write_xlsx({"Combined": rows}), BATCH_ID)
    assert (result.statement_date, len(result.holdings), result.errors) == (date(2026, 6, 30), 1, [])


def test_whitespace_normalisation_does_not_relax_wording(write_xlsx):
    path = write_xlsx({"Combined": sheet_rows([HOLDING_X], title="CombinedHoldings Statement as on 2026-06-30")})
    assert adapter.can_parse(path) is False


# --- W. Statement date only from the selected title --------------------------------------------

def test_statement_date_from_selected_title_not_file_name_or_mtime(write_xlsx):
    path = write_xlsx(
        {
            "Equity": sheet_rows([HOLDING_Y], header=EQUITY_HEADER, title="Equity Holdings Statement as on 2026-08-15"),
            "Combined": sheet_rows([HOLDING_X], title="Combined Holdings Statement as on 2026-06-30"),
        },
        name="holdings-ZZ0000_2025-01-31.xlsx",
    )
    stamp = datetime(2024, 3, 3).timestamp()
    os.utime(path, (stamp, stamp))
    assert adapter.parse(path, BATCH_ID).statement_date == date(2026, 6, 30)  # Combined title wins


def test_fallback_uses_equity_title_date(write_xlsx):
    path = write_xlsx({"Equity": sheet_rows([HOLDING_Y], header=EQUITY_HEADER,
                                            title="Equity Holdings Statement as on 2026-08-15")})
    assert adapter.parse(path, BATCH_ID).statement_date == date(2026, 8, 15)
