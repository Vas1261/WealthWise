"""CSV and PDF renderings of the monthly (FR-23) and portfolio (FR-24) reports.

Presentation only: every figure comes computed from the domain and is rounded here exactly like the JSON
API (api/formatting.py). CSV numbers are plain (no grouping) so spreadsheets read them as numbers; PDF
amounts carry thousands separators and an ISO currency code (the built-in PDF fonts have no rupee sign).
Text the user typed (categories, descriptions) or a file supplied (instrument names) is neutralised for
spreadsheets (OWASP CSV injection) and escaped for the PDF's paragraph markup.
"""

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from html import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.api.formatting import MONEY_PLACES, PERCENT_PLACES, PRICE_PLACES, RATE_PLACES, fixed
from app.domain.market import PriceStatus
from app.domain.portfolio import CurrencyTotals, HoldingValuation, PortfolioSummary, ReportingTotals, pin_dates
from app.domain.reports import MonthlyReport

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
LABELS = {"income": "Income", "expense": "Expense", "equity": "Equity", "mutual_fund": "Mutual fund",
          "zerodha": "Zerodha", "groww": "Groww"}
PRICE_WORDS = {
    PriceStatus.FRESH: "Live", PriceStatus.STALE: "Stale (last known price)",
    PriceStatus.UNAVAILABLE: "Unavailable", PriceStatus.NOT_SUPPORTED: "Not priced (shown at cost)",
}


def _label(value: str) -> str:
    return LABELS.get(value, value)


def _safe(text: str | None) -> str:
    """A text cell a spreadsheet will never run as a formula."""
    text = text or ""
    return "'" + text if text.startswith(FORMULA_START) else text


def _money(value: Decimal | None) -> str:
    return fixed(value, MONEY_PLACES) or ""


def _pct(value: Decimal | None) -> str:
    return fixed(value, PERCENT_PLACES) or ""


def _grouped(value: Decimal | None, currency: str, places: int = MONEY_PLACES) -> str:
    text = fixed(value, places)
    return "—" if text is None else f"{currency} {Decimal(text):,.{places}f}"


def _month_name(report: MonthlyReport) -> str:
    return datetime(report.year, report.month, 1).strftime("%B %Y")


def _price_word(v: HoldingValuation) -> str:
    return "Price in another currency: not used" if v.currency_mismatch else PRICE_WORDS[v.price.status]


# --- CSV -----------------------------------------------------------------------------------------------------


def _csv(sections: Iterable[tuple[str, Sequence[str], Iterable[Sequence[str]]]], heading: Sequence[str]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    for line in heading:
        writer.writerow([line])
    for title, header, rows in sections:
        writer.writerow([])
        writer.writerow([title])
        writer.writerow(header)
        writer.writerows(rows)
    return ("﻿" + out.getvalue()).encode("utf-8")  # the BOM makes Excel read UTF-8


def monthly_csv(report: MonthlyReport, generated_at: datetime) -> bytes:
    b = report.budget
    return _csv(
        [
            ("Summary", ["Currency", "Income", "Expenses", "Net"],
             [[t.currency, _money(t.income), _money(t.expenses), _money(t.net)] for t in report.totals]),
            ("By category", ["Type", "Category", "Currency", "Entries", "Amount"],
             [[_label(c.kind), _safe(c.category), c.currency, str(c.entries), _money(c.amount)]
              for c in report.categories]),
            ("Budget vs actual", ["Category", "Currency", "Budget", "Actual", "Remaining", "Used %", "Over budget"],
             [[_safe(line.category), line.currency, _money(line.budget), _money(line.actual), _money(line.remaining),
               _pct(line.utilization_pct), "yes" if line.over_budget else "no"] for line in b.budgets]),
            ("Expenses without a budget", ["Category", "Currency", "Actual"],
             [[_safe(line.category), line.currency, _money(line.actual)] for line in b.unbudgeted]),
            ("Entries", ["Date", "Type", "Category", "Description", "Currency", "Amount"],
             [[e.entry_date.isoformat(), _label(e.kind), _safe(e.category), _safe(e.description), e.currency,
               _money(e.amount)] for e in report.entries]),
        ],
        [f"WealthWise monthly report: {_month_name(report)}", f"Generated {generated_at:%Y-%m-%d %H:%M} UTC"],
    )


def _reporting_rows(r: ReportingTotals, fx_required: bool) -> list[tuple[str, Decimal | None]]:
    rows = [
        ("Current value (priced holdings)", r.current_value),
        ("Unrealised P&L (market gain)" if fx_required else "Unrealised P&L", r.unrealized_pnl),
        ("Unrealised P&L %", r.unrealized_pnl_pct),
        ("Cost of priced holdings", r.priced_invested_value),
        ("Total invested", r.invested_value),
        ("Cost of holdings without a price", r.unpriced_invested_value),
        ("Mutual funds at cost (not in current value)", r.cost_floor_value),
    ]
    if fx_required and r.total_gain is not None:
        rows += [
            ("Currency gain (exchange-rate change)", r.currency_gain),
            ("Total gain", r.total_gain),
            ("Total gain %", r.total_gain_pct),
            ("Total invested at purchase-time rates", r.invested_value_pinned),
        ]
    return rows


def _status_line(t: CurrencyTotals | ReportingTotals) -> str:
    return (f"{t.holdings} holdings: {t.priced} priced, {t.stale} stale, {t.unavailable} without a price, "
            f"{t.not_supported} mutual funds at cost")


def _holding_row(v: HoldingValuation, pinned: bool) -> list[str]:
    p, dates = v.position, pin_dates(v.position)
    row = [
        _safe(p.instrument_name), p.isin or "", p.symbol or "", _label(p.source_broker), _label(p.asset_class),
        p.statement_date.isoformat(), str(p.quantity), fixed(p.average_buy_price, PRICE_PLACES) or "",
        p.native_currency, _money(v.invested_value.amount), fixed(v.current_price, PRICE_PLACES) or "",
        _price_word(v), v.price.as_of.isoformat() if v.priced and v.price.as_of else "",
        _money(v.current_value.amount if v.current_value else None),
        _money(v.unrealized_pnl.amount if v.unrealized_pnl else None), _pct(v.unrealized_pnl_pct),
        _money(v.cost_floor_value.amount if v.cost_floor_value else None),
    ]
    if pinned:
        row += [fixed(v.pinned_rate, RATE_PLACES) or "", _money(v.pinned_cost.amount if v.pinned_cost else None),
                min(d.on for d in dates).isoformat(), max(d.on for d in dates).isoformat()]
    return row


def portfolio_csv(summary: PortfolioSummary, generated_at: datetime) -> bytes:
    s, r, cur = summary, summary.reporting_totals, summary.reporting_currency
    sections: list[tuple[str, Sequence[str], Iterable[Sequence[str]]]] = []
    if r is not None:
        sections.append((f"Totals in {cur}", ["Figure", "Value"],
                         [[label, _pct(value) if label.endswith("%") else _money(value)]
                          for label, value in _reporting_rows(r, s.fx_required)]))
    sections.append(("Totals by currency (not converted)",
                     ["Currency", "Invested", "Current value", "Unrealised P&L", "P&L %", "Mutual funds at cost"],
                     [[c, _money(t.invested_value), _money(t.current_value), _money(t.unrealized_pnl),
                       _pct(t.unrealized_pnl_pct), _money(t.cost_floor_value)]
                      for c, t in s.totals_by_currency.items()]))
    header = ["Instrument", "ISIN", "Symbol", "Broker", "Type", "Statement date", "Quantity", "Average buy price",
              "Currency", "Invested", "Price", "Price status", "Price as of", "Current value", "Unrealised P&L",
              "P&L %", "At cost (mutual funds)"]
    if s.fx_required:
        header += [f"Pinned rate into {cur}", f"Cost in {cur} at purchase-time rates", "Cost dates from",
                   "Cost dates to"]
    sections.append(("Holdings (values in each holding's own currency)", header,
                     [_holding_row(v, s.fx_required) for v in s.holdings]))
    return _csv(sections, ["WealthWise portfolio report", f"Generated {generated_at:%Y-%m-%d %H:%M} UTC",
                           f"Reporting currency: {cur}", *_portfolio_notes(s)])


def _portfolio_notes(s: PortfolioSummary) -> list[str]:
    notes = []
    if s.fx_unavailable:
        notes.append(f"No exchange rate for {', '.join(s.fx_unavailable)}: totals are not converted.")
    if s.pinned_fx_unavailable:
        notes.append(f"No purchase-date exchange rates yet for {', '.join(s.pinned_fx_unavailable)}: "
                     "no currency gain shown.")
    if s.contains_stale_prices and s.oldest_price_as_of:
        notes.append(f"Some prices are stale (oldest from {s.oldest_price_as_of:%Y-%m-%d %H:%M} UTC).")
    return notes


# --- PDF ------------------------------------------------------------------------------------------------------

_STYLES = getSampleStyleSheet()
_CELL = _STYLES["BodyText"].clone("cell", fontSize=8, leading=10)
_HEADING = _STYLES["Heading2"].clone("section", keepWithNext=1)  # never stranded at the foot of a page
# Holdings columns on landscape A4 (267 mm between margins); the last one only when costs are pinned.
_HOLDING_WIDTHS = [w * mm for w in (48, 22, 15, 23, 25, 24, 25, 23, 13, 26, 23)]


def _p(text: str, style=_CELL) -> Paragraph:
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]], numeric: Iterable[int],
           widths: Sequence[float] | None = None) -> Flowable:
    if not rows:
        return _p("None.")
    numeric = set(numeric)
    data = [[_p(h) for h in header]] + [
        [cell if i in numeric else _p(cell) for i, cell in enumerate(row)] for row in rows
    ]
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8ecf2")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9aa4b2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        *[("ALIGN", (i, 1), (i, -1), "RIGHT") for i in numeric],
    ]))
    return table


def _pdf(title: str, generated_at: datetime, story: list[Flowable], pagesize) -> bytes:
    buffer = io.BytesIO()

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(doc.leftMargin, 10 * mm, f"WealthWise · {title} · generated {generated_at:%Y-%m-%d %H:%M} UTC")
        canvas.drawRightString(pagesize[0] - doc.rightMargin, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=pagesize, title=f"WealthWise {title}", author="WealthWise",
                            leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=18 * mm)
    doc.build([_p(f"WealthWise {title}", _STYLES["Title"]), *story], onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _section(title: str) -> list[Flowable]:
    return [Spacer(1, 4 * mm), _p(title, _HEADING)]


def monthly_pdf(report: MonthlyReport, generated_at: datetime) -> bytes:
    b = report.budget
    story: list[Flowable] = [
        *_section("Summary"),
        _table(["Currency", "Income", "Expenses", "Net"],
               [[t.currency, *(_grouped(x, t.currency) for x in (t.income, t.expenses, t.net))]
                for t in report.totals], numeric=(1, 2, 3)),
        *_section("By category"),
        _table(["Type", "Category", "Entries", "Amount"],
               [[_label(c.kind), c.category, str(c.entries), _grouped(c.amount, c.currency)]
                for c in report.categories], numeric=(2, 3)),
        *_section("Budget vs actual"),
        _table(["Category", "Budget", "Actual", "Remaining", "Used", "Status"],
               [[line.category, _grouped(line.budget, line.currency), _grouped(line.actual, line.currency),
                 _grouped(line.remaining, line.currency), f"{_pct(line.utilization_pct)} %",
                 "Over budget" if line.over_budget else "Within budget"] for line in b.budgets],
               numeric=(1, 2, 3, 4)),
        *_section("Expenses without a budget"),
        _table(["Category", "Actual"], [[line.category, _grouped(line.actual, line.currency)] for line in b.unbudgeted],
               numeric=(1,)),
        *_section("Entries"),
        _table(["Date", "Type", "Category", "Description", "Amount"],
               [[e.entry_date.isoformat(), _label(e.kind), e.category, e.description or "",
                 _grouped(e.amount, e.currency)] for e in report.entries],
               numeric=(4,), widths=[22 * mm, 18 * mm, 35 * mm, 70 * mm, 35 * mm]),
    ]
    return _pdf(f"monthly report: {_month_name(report)}", generated_at, story, A4)


def portfolio_pdf(summary: PortfolioSummary, generated_at: datetime) -> bytes:
    s, r, cur = summary, summary.reporting_totals, summary.reporting_currency
    story: list[Flowable] = [_p(f"Reporting currency: {cur}")]
    story += [_p(note) for note in _portfolio_notes(s)]
    if r is not None:
        story += [
            *_section(f"Totals in {cur}"),
            _table(["Figure", "Value"],
                   [[label, f"{_pct(value)} %" if label.endswith("%") else _grouped(value, cur)]
                    for label, value in _reporting_rows(r, s.fx_required)], numeric=(1,), widths=[90 * mm, 45 * mm]),
            _p(_status_line(r)),
        ]
    story += [
        *_section("Totals by currency (not converted)"),
        _table(["Currency", "Invested", "Current value", "Unrealised P&L", "P&L %", "Mutual funds at cost"],
               [[c, _grouped(t.invested_value, c), _grouped(t.current_value, c), _grouped(t.unrealized_pnl, c),
                 _pct(t.unrealized_pnl_pct), _grouped(t.cost_floor_value, c)] for c, t in s.totals_by_currency.items()],
               numeric=(1, 2, 3, 4, 5)),
        *_section("Holdings (values in each holding's own currency)"),
    ]
    header = ["Instrument", "Broker", "Quantity", "Avg buy price", "Invested", "Price", "Current value",
              "Unrealised P&L", "P&L %", "Price status"]
    rows = []
    for v in s.holdings:
        p, c = v.position, v.position.native_currency
        rows.append([
            f"{p.instrument_name}\n{p.isin or ''}", f"{_label(p.source_broker)} · {_label(p.asset_class)}",
            format(p.quantity.normalize(), "f"),
            _grouped(p.average_buy_price, c, PRICE_PLACES), _grouped(v.invested_value.amount, c),
            _grouped(v.current_price, c, PRICE_PLACES),
            _grouped(v.current_value.amount if v.current_value else None, c),
            _grouped(v.unrealized_pnl.amount if v.unrealized_pnl else None, c), _pct(v.unrealized_pnl_pct),
            _price_word(v),
        ])
        if s.fx_required:
            rows[-1].append(_grouped(v.pinned_cost.amount if v.pinned_cost else None, cur))
    if s.fx_required:
        header.append(f"Cost in {cur} at purchase-time rates")
    story.append(_table(header, rows, numeric=(2, 3, 4, 5, 6, 7, 8, *((10,) if s.fx_required else ())),
                        widths=_HOLDING_WIDTHS[:len(header)]))
    return _pdf("portfolio report", generated_at, story, landscape(A4))
