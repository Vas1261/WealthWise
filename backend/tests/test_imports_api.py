"""POST /api/imports over HTTP with synthetic statements: auth, CSRF, ownership, snapshot rules, history."""

import tempfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import imports as imports_api
from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.repositories import import_repository, portfolio_repository
from tests import test_groww_equity_adapter as groww_equity
from tests import test_groww_mutual_fund_adapter as groww_mf
from tests import test_zerodha_equity_adapter as zerodha
from tests.portfolio_support import groww_equity_file, groww_mf_file, write_xlsx, zerodha_file
from tests.test_auth import csrf_headers, logged_in_client

pytestmark = pytest.mark.usefixtures("migrated_db")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
BAD_ROW = ["BROKEN LTD", "NOT-AN-ISIN", "abc", -1, None, None, None, None]


def user_id_of(client: TestClient) -> int:
    return client.get("/api/auth/me").json()["id"]


def upload(client: TestClient, path: str, name: str = "statement.xlsx", **kwargs):
    with open(path, "rb") as handle:
        return client.post("/api/imports", files={"file": (name, handle, XLSX)},
                           headers=kwargs.pop("headers", csrf_headers(client)), **kwargs)


def effective(user_id):
    with SessionLocal() as db:
        return portfolio_repository.get_effective_positions(db, user_id)


def history(user_id):
    with SessionLocal() as db:
        return [(a.result, a.broker, a.asset_class, a.rows_succeeded, a.rows_failed)
                for a in import_repository.list_attempts(db, user_id)]


@pytest.fixture
def alice():
    return logged_in_client("alice@example.com")


# --- Authentication, CSRF, ownership ------------------------------------------------------------------

def test_unauthenticated_upload_rejected(tmp_path):
    anonymous = TestClient(app)
    response = upload(anonymous, groww_equity_file(tmp_path / "g.xlsx"))
    assert response.status_code == 401


@pytest.mark.parametrize("headers", [{}, {settings.csrf_header_name: "not-the-cookie"}], ids=["missing", "wrong"])
def test_csrf_required(alice, tmp_path, headers):
    response = upload(alice, groww_equity_file(tmp_path / "g.xlsx"), headers=headers)
    assert response.status_code == 403
    assert history(user_id_of(alice)) == []


def test_client_supplied_user_id_cannot_choose_owner(alice, tmp_path):
    bob = logged_in_client("bob@example.com")
    bob_id = user_id_of(bob)
    response = upload(alice, groww_equity_file(tmp_path / "g.xlsx"), data={"user_id": str(bob_id)},
                      params={"user_id": bob_id})
    assert response.status_code == 201
    assert len(effective(user_id_of(alice))) == 1
    assert effective(bob_id) == [] and history(bob_id) == []


# --- Successful imports of each format -------------------------------------------------------------------

@pytest.mark.parametrize(
    ("make_file", "broker", "asset_class", "statement_date", "rows"),
    [
        (lambda p: groww_equity_file(p, rows=[groww_equity.ROW_A, groww_equity.ROW_B]), "groww", "equity",
         "2026-09-01", 2),
        (lambda p: zerodha_file(p, holdings=[zerodha.HOLDING_X, zerodha.HOLDING_Y]), "zerodha", "equity",
         "2026-06-30", 2),
        (lambda p: groww_mf_file(p, transactions=[groww_mf.tx(), groww_mf.tx(scheme=groww_mf.BETA)]), "groww",
         "mutual_fund", "2026-06-30", 2),
    ],
    ids=["groww-equity", "zerodha-equity", "groww-mutual-fund"],
)
def test_successful_import_of_each_format(alice, tmp_path, make_file, broker, asset_class, statement_date, rows):
    response = upload(alice, make_file(tmp_path / "upload.xlsx"), name="holdings.xlsx")
    assert response.status_code == 201
    body = response.json()
    assert body == {
        "attempt_id": body["attempt_id"], "result": "current", "broker": broker, "asset_class": asset_class,
        "statement_date": statement_date, "file_name": "holdings.xlsx", "rows_succeeded": rows, "rows_failed": 0,
        "replaced_existing": False, "errors": [],
    }
    assert len(effective(user_id_of(alice))) == rows


def test_imported_holdings_reach_the_portfolio(alice, tmp_path, monkeypatch):
    upload(alice, groww_equity_file(tmp_path / "g.xlsx"))
    upload(alice, groww_mf_file(tmp_path / "m.xlsx"))
    portfolio = alice.get("/api/portfolio").json()
    assert {(h["source_broker"], h["asset_class"]) for h in portfolio["holdings"]} == {
        ("groww", "equity"), ("groww", "mutual_fund")}


# --- Rejections ------------------------------------------------------------------------------------------

def test_unsupported_file(alice, tmp_path):
    response = upload(alice, write_xlsx(tmp_path / "x.xlsx", {"Sheet1": [["Quarterly report"], ["Revenue", 1]]}))
    assert (response.status_code, response.json()["code"]) == (422, "unsupported_statement")
    assert set(response.json()) == {"detail", "code"}
    assert history(user_id_of(alice)) == [("rejected", None, None, 0, 0)]


def test_ambiguous_file(alice, tmp_path):
    both = write_xlsx(tmp_path / "both.xlsx", {"Sheet1": groww_equity.statement_rows([groww_equity.ROW_A]),
                                               "Combined": zerodha.sheet_rows([zerodha.HOLDING_X])})
    response = upload(alice, both)
    assert (response.status_code, response.json()["code"]) == (422, "ambiguous_statement")
    assert effective(user_id_of(alice)) == []


def test_recognised_but_invalid_statement(alice, tmp_path):
    response = upload(alice, groww_equity_file(tmp_path / "g.xlsx", as_on="31-02-2026"))
    assert (response.status_code, response.json()["code"]) == (422, "invalid_statement")


def test_not_a_spreadsheet_with_xlsx_name(alice, tmp_path):
    fake = tmp_path / "fake.xlsx"
    fake.write_text("plain text pretending to be a workbook")
    response = upload(alice, str(fake))
    assert (response.status_code, response.json()["code"]) == (422, "unsupported_statement")


@pytest.mark.parametrize("name", ["statement.csv", "statement.xls", "statement", "statement.xlsx.exe"])
def test_non_xlsx_name_rejected_before_import(alice, tmp_path, name):
    response = upload(alice, groww_equity_file(tmp_path / "g.xlsx"), name=name)
    assert (response.status_code, response.json()["code"]) == (415, "not_xlsx")
    assert history(user_id_of(alice)) == []


def test_oversized_upload_rejected(alice, tmp_path, monkeypatch):
    monkeypatch.setattr(imports_api, "MAX_UPLOAD_BYTES", 1024)
    response = upload(alice, groww_equity_file(tmp_path / "g.xlsx"))
    assert (response.status_code, response.json()["code"]) == (413, "file_too_large")
    assert history(user_id_of(alice)) == []


def test_missing_file_field(alice):
    assert alice.post("/api/imports", headers=csrf_headers(alice)).status_code == 422


# --- Snapshot rules over HTTP ---------------------------------------------------------------------------

def test_completely_failed_import_keeps_existing_snapshot_and_is_in_history(alice, tmp_path):
    uid = user_id_of(alice)
    assert upload(alice, groww_equity_file(tmp_path / "good.xlsx")).status_code == 201
    response = upload(alice, groww_equity_file(tmp_path / "bad.xlsx", rows=[BAD_ROW]))  # same identity
    assert response.status_code == 422
    body = response.json()
    assert (body["result"], body["rows_succeeded"], body["rows_failed"], body["replaced_existing"]) == (
        "failed", 0, 1, False)
    assert {e["field"] for e in body["errors"]} == {"ISIN", "Quantity", "Average buy price"}
    assert [p.instrument_name for p in effective(uid)] == ["SAMPLE TEXTILES LIMITED"]
    assert history(uid) == [("current", "groww", "equity", 1, 0), ("failed", "groww", "equity", 0, 1)]


def test_successful_reimport_replaces_same_snapshot(alice, tmp_path):
    upload(alice, groww_equity_file(tmp_path / "a.xlsx", rows=[groww_equity.ROW_A]))
    body = upload(alice, groww_equity_file(tmp_path / "b.xlsx", rows=[groww_equity.ROW_B])).json()
    assert (body["result"], body["replaced_existing"]) == ("current", True)
    assert [p.instrument_name for p in effective(user_id_of(alice))] == ["SYNTHETIC POWER LIMITED"]


def test_newer_statement_becomes_effective(alice, tmp_path):
    upload(alice, groww_equity_file(tmp_path / "old.xlsx", rows=[groww_equity.ROW_A], as_on="01-08-2026"))
    body = upload(alice, groww_equity_file(tmp_path / "new.xlsx", rows=[groww_equity.ROW_B])).json()
    assert body["result"] == "current"
    assert [p.instrument_name for p in effective(user_id_of(alice))] == ["SYNTHETIC POWER LIMITED"]


def test_older_statement_cannot_replace_newer(alice, tmp_path):
    upload(alice, groww_equity_file(tmp_path / "new.xlsx", rows=[groww_equity.ROW_B]))
    response = upload(alice, groww_equity_file(tmp_path / "old.xlsx", rows=[groww_equity.ROW_A], as_on="01-08-2026"))
    assert (response.status_code, response.json()["result"]) == (201, "history")
    assert [p.instrument_name for p in effective(user_id_of(alice))] == ["SYNTHETIC POWER LIMITED"]


def test_valid_empty_statement_is_kept_as_a_real_snapshot(alice, tmp_path):
    upload(alice, groww_mf_file(tmp_path / "old.xlsx", to="May 31 2026"))
    redeemed = [groww_mf.tx(units="10"), groww_mf.tx(kind="REDEEM", units="10")]
    response = upload(alice, groww_mf_file(tmp_path / "new.xlsx", transactions=redeemed))
    body = response.json()
    assert (response.status_code, body["result"], body["rows_succeeded"], body["errors"]) == (201, "current", 0, [])
    assert effective(user_id_of(alice)) == []


def test_partial_import_returns_row_errors_and_counts(alice, tmp_path):
    marker = "CELL-VALUE-TEST-55"
    bad = [marker, marker, marker, marker, None, None, None, None]
    response = upload(alice, groww_equity_file(tmp_path / "p.xlsx", rows=[groww_equity.ROW_A, bad]))
    body = response.json()
    assert (response.status_code, body["result"], body["rows_succeeded"], body["rows_failed"]) == (201, "current", 1, 1)
    # the text is a valid Stock Name; ISIN, Quantity and Average buy price are not
    assert [(e["row"], e["field"]) for e in body["errors"]] == [
        (13, "ISIN"), (13, "Quantity"), (13, "Average buy price"),
    ]
    assert marker not in response.text  # messages never echo cell values


# --- Paths and private data -----------------------------------------------------------------------------

@pytest.mark.parametrize("client_name", ["C:\\Users\\someone\\Desktop\\stmt.xlsx", "/home/someone/stmt.xlsx",
                                         "../../etc/stmt.xlsx", "stmt.xlsx"])
def test_no_filesystem_path_leaks(alice, tmp_path, client_name):
    response = upload(alice, groww_equity_file(tmp_path / "g.xlsx"), name=client_name)
    assert response.status_code == 201 and response.json()["file_name"] == "stmt.xlsx"
    for fragment in ("someone", "Desktop", "/home", "etc/", tempfile.gettempdir(), str(tmp_path)):
        assert fragment not in response.text
    with SessionLocal() as db:
        assert {a.file_name for a in import_repository.list_attempts(db, user_id_of(alice))} == {"stmt.xlsx"}


def test_response_contains_no_holdings_or_values(alice, tmp_path):
    response = upload(alice, groww_equity_file(tmp_path / "g.xlsx"))
    for value in ("SAMPLE TEXTILES", "INE000B01026", "31.45"):
        assert value not in response.text


def test_temporary_upload_files_are_removed(alice, tmp_path, monkeypatch):
    created: list[str] = []
    real = tempfile.mkstemp

    def tracking_mkstemp(*args, **kwargs):
        handle, path = real(*args, **kwargs)
        created.append(path)
        return handle, path

    monkeypatch.setattr(imports_api.tempfile, "mkstemp", tracking_mkstemp)
    upload(alice, groww_equity_file(tmp_path / "ok.xlsx"))
    upload(alice, write_xlsx(tmp_path / "x.xlsx", {"Sheet1": [["nothing"]]}))
    monkeypatch.setattr(imports_api, "MAX_UPLOAD_BYTES", 10)
    upload(alice, groww_equity_file(tmp_path / "big.xlsx"))
    assert len(created) == 3 and not any(Path(p).exists() for p in created)


def test_statement_date_in_history_matches_statement(alice, tmp_path):
    upload(alice, zerodha_file(tmp_path / "z.xlsx", as_on="2026-05-31"))
    with SessionLocal() as db:
        (attempt,) = import_repository.list_attempts(db, user_id_of(alice))
    assert attempt.statement_date == date(2026, 5, 31)


# --- GET /api/imports: import history (Module 10) -----------------------------------------------------

HISTORY_FIELDS = {"attempt_id", "result", "broker", "asset_class", "statement_date", "file_name", "rows_succeeded",
                  "rows_failed", "replaced_existing", "imported_at"}


def test_history_requires_authentication():
    assert TestClient(app).get("/api/imports").status_code == 401


def test_history_is_empty_for_a_new_user(alice):
    response = alice.get("/api/imports")
    assert (response.status_code, response.json()) == (200, [])


def test_history_lists_every_result_newest_first(alice, tmp_path):
    first = upload(alice, groww_equity_file(tmp_path / "g.xlsx"), name="first.xlsx")
    upload(alice, groww_equity_file(tmp_path / "g.xlsx"), name="again.xlsx")  # same identity: replaces
    upload(alice, groww_equity_file(tmp_path / "old.xlsx", as_on="01-08-2026"), name="older.xlsx")
    upload(alice, groww_equity_file(tmp_path / "bad.xlsx", as_on="02-09-2026", rows=[BAD_ROW]), name="bad.xlsx")
    upload(alice, write_xlsx(tmp_path / "x.xlsx", {"Sheet1": [["Quarterly report"], ["Revenue", 1]]}),
           name="other.xlsx")
    assert first.status_code == 201

    body = alice.get("/api/imports").json()
    assert all(set(row) == HISTORY_FIELDS for row in body)
    assert [(r["file_name"], r["result"], r["replaced_existing"]) for r in body] == [
        ("other.xlsx", "rejected", False), ("bad.xlsx", "failed", False), ("older.xlsx", "history", False),
        ("again.xlsx", "current", True), ("first.xlsx", "current", False),
    ]
    assert [r["attempt_id"] for r in body] == sorted((r["attempt_id"] for r in body), reverse=True)
    rejected, failed, *_ = body
    assert (rejected["broker"], rejected["asset_class"], rejected["statement_date"]) == (None, None, None)
    assert (failed["broker"], failed["rows_succeeded"], failed["rows_failed"]) == ("groww", 0, 1)
    assert body[-1]["statement_date"] and body[-1]["imported_at"].endswith(("Z", "+00:00"))


def test_history_is_scoped_to_the_user(alice, tmp_path):
    upload(alice, groww_equity_file(tmp_path / "g.xlsx"), name="alice.xlsx")
    bob = logged_in_client("bob@example.com")
    upload(bob, zerodha_file(tmp_path / "z.xlsx"), name="bob.xlsx")
    assert [r["file_name"] for r in alice.get("/api/imports").json()] == ["alice.xlsx"]
    assert [r["file_name"] for r in bob.get("/api/imports").json()] == ["bob.xlsx"]


def test_history_exposes_no_paths_batches_holdings_or_row_errors(alice, tmp_path):
    upload(alice, groww_equity_file(tmp_path / "bad.xlsx", rows=[BAD_ROW]), name=str(tmp_path / "nested" / "bad.xlsx"))
    upload(alice, groww_equity_file(tmp_path / "g.xlsx"))
    text = alice.get("/api/imports").text
    for forbidden in (str(tmp_path), "batch", "holdings", "errors", "NOT-AN-ISIN", "BROKEN LTD", "INE"):
        assert forbidden not in text


def test_history_read_needs_no_csrf_and_changes_nothing(alice, tmp_path):
    upload(alice, groww_equity_file(tmp_path / "g.xlsx"))
    before = (history(user_id_of(alice)), effective(user_id_of(alice)))
    assert alice.get("/api/imports", headers={}).status_code == 200
    assert (history(user_id_of(alice)), effective(user_id_of(alice))) == before
