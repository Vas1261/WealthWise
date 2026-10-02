"""Import persistence: adapter output -> ImportBatch + Holding, with the snapshot rules (synthetic files)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import Holding, ImportBatch
from app.repositories import import_repository, portfolio_repository
from app.services import import_service
from app.services.import_service import ImportResult, RejectionReason, StatementRejectedError
from tests import test_groww_equity_adapter as groww_equity
from tests import test_groww_mutual_fund_adapter as groww_mf
from tests import test_zerodha_equity_adapter as zerodha
from tests.portfolio_support import groww_equity_file, groww_mf_file, make_user, write_xlsx, zerodha_file

pytestmark = pytest.mark.usefixtures("migrated_db")


@pytest.fixture
def user_id():
    with SessionLocal() as db:
        return make_user(db, "importer@example.com").id


def run_import(user_id, path, name="statement.xlsx"):
    with SessionLocal() as db:
        return import_service.import_statement(db, user_id, path, name)


def batches(user_id):
    with SessionLocal() as db:
        return db.execute(select(ImportBatch).where(ImportBatch.user_id == user_id)
                          .order_by(ImportBatch.statement_date)).scalars().all()


def history(user_id):
    with SessionLocal() as db:
        return [(a.result, a.broker, a.asset_class, a.statement_date, a.rows_succeeded, a.rows_failed, a.batch_id)
                for a in import_repository.list_attempts(db, user_id)]


def effective(user_id):
    with SessionLocal() as db:
        return portfolio_repository.get_effective_positions(db, user_id)


# --- Adapter routing and persistence --------------------------------------------------------------

@pytest.mark.parametrize(
    ("make_file", "broker", "asset_class", "statement_date"),
    [
        (groww_equity_file, "groww", "equity", date(2026, 9, 1)),
        (zerodha_file, "zerodha", "equity", date(2026, 6, 30)),
        (groww_mf_file, "groww", "mutual_fund", date(2026, 6, 30)),
    ],
)
def test_each_format_is_routed_by_content_and_persisted(tmp_path, user_id, make_file, broker, asset_class,
                                                         statement_date):
    outcome = run_import(user_id, make_file(tmp_path / "upload.xlsx"), name="C:\\Users\\x\\Desktop\\my file.xlsx")
    assert (outcome.broker, outcome.asset_class, outcome.statement_date) == (broker, asset_class, statement_date)
    assert (outcome.stored, outcome.current, outcome.replaced_existing) == (True, True, False)
    (batch,) = batches(user_id)
    assert (batch.id, batch.broker, batch.asset_class, batch.statement_date) == (
        outcome.batch_id, broker, asset_class, statement_date)
    assert outcome.result == ImportResult.CURRENT
    assert history(user_id) == [("current", broker, asset_class, statement_date, outcome.rows_succeeded, 0,
                                 outcome.batch_id)]
    assert batch.file_name == outcome.file_name == "my file.xlsx"  # client path never stored
    positions = effective(user_id)
    assert len(positions) == outcome.rows_succeeded == batch.rows_succeeded >= 1
    assert all(p.import_batch_id == batch.id and p.source_broker == broker for p in positions)


def test_persisted_holding_matches_adapter_output(tmp_path, user_id):
    run_import(user_id, groww_equity_file(tmp_path / "g.xlsx"))
    (p,) = effective(user_id)
    assert (p.instrument_name, p.isin, p.symbol, p.quantity, p.average_buy_price, p.native_currency) == (
        "SAMPLE TEXTILES LIMITED", "INE000B01026", None, Decimal("12"), Decimal("31.45"), "INR")


@pytest.mark.parametrize("name", ["/home/x/stmt.xlsx", "C:\\x\\stmt.xlsx", "..\\../stmt.xlsx", "stmt.xlsx"])
def test_only_the_base_file_name_is_stored(tmp_path, user_id, name):
    assert run_import(user_id, groww_equity_file(tmp_path / "g.xlsx"), name=name).file_name == "stmt.xlsx"


def test_unsupported_and_unreadable_files_are_rejected_and_recorded(tmp_path, user_id):
    with pytest.raises(StatementRejectedError) as raised:
        run_import(user_id, write_xlsx(tmp_path / "x.xlsx", {"Sheet1": [["Hello"]]}))
    assert raised.value.reason == RejectionReason.UNSUPPORTED and raised.value.attempt_id is not None
    not_xlsx = tmp_path / "n.xlsx"
    not_xlsx.write_text("not a spreadsheet")
    with pytest.raises(StatementRejectedError):
        run_import(user_id, str(not_xlsx))
    assert batches(user_id) == []
    assert [h[:4] for h in history(user_id)] == [("rejected", None, None, None)] * 2


def test_ambiguous_file_is_rejected_and_recorded(tmp_path, user_id):
    both = write_xlsx(tmp_path / "both.xlsx", {
        "Sheet1": groww_equity.statement_rows([groww_equity.ROW_A]),
        "Combined": zerodha.sheet_rows([zerodha.HOLDING_X]),
    })
    with pytest.raises(StatementRejectedError) as raised:
        run_import(user_id, both)
    assert raised.value.reason == RejectionReason.AMBIGUOUS
    assert batches(user_id) == [] and [h[0] for h in history(user_id)] == ["rejected"]


def test_adapter_format_error_is_rejected_without_storing(tmp_path, user_id):
    # Recognised as Groww equity, but the statement date is impossible: the adapter rejects the file.
    bad = groww_equity_file(tmp_path / "bad.xlsx", as_on="31-02-2026")
    with pytest.raises(StatementRejectedError) as raised:
        run_import(user_id, bad)
    assert raised.value.reason == RejectionReason.INVALID
    assert batches(user_id) == []
    assert [h[:3] for h in history(user_id)] == [("rejected", "groww", "equity")]  # format known, date not


# --- Snapshot rules -----------------------------------------------------------------------------------

def test_same_identity_replaces_snapshot(tmp_path, user_id):
    first = run_import(user_id, groww_equity_file(tmp_path / "a.xlsx", rows=[groww_equity.ROW_A]))
    second = run_import(user_id, groww_equity_file(tmp_path / "b.xlsx", rows=[groww_equity.ROW_A, groww_equity.ROW_B]))
    assert (second.replaced_existing, second.current) == (True, True)
    (batch,) = batches(user_id)
    assert batch.id == second.batch_id != first.batch_id
    assert len(effective(user_id)) == 2
    # both attempts stay in history; the replaced snapshot's link is cleared by ON DELETE SET NULL
    assert [(h[0], h[6]) for h in history(user_id)] == [("current", None), ("current", second.batch_id)]
    with SessionLocal() as db:  # the replaced snapshot's holdings are gone, not orphaned
        assert db.execute(select(func.count()).select_from(Holding)).scalar_one() == 2


def test_newer_statement_supersedes_older(tmp_path, user_id):
    run_import(user_id, groww_equity_file(tmp_path / "old.xlsx", rows=[groww_equity.ROW_A], as_on="01-08-2026"))
    newer = run_import(user_id, groww_equity_file(tmp_path / "new.xlsx", rows=[groww_equity.ROW_B], as_on="01-09-2026"))
    assert newer.current is True
    assert [p.instrument_name for p in effective(user_id)] == ["SYNTHETIC POWER LIMITED"]
    assert len(batches(user_id)) == 2  # the older snapshot remains as history


def test_older_statement_never_replaces_newer(tmp_path, user_id):
    run_import(user_id, groww_equity_file(tmp_path / "new.xlsx", rows=[groww_equity.ROW_B], as_on="01-09-2026"))
    older = run_import(user_id, groww_equity_file(tmp_path / "old.xlsx", rows=[groww_equity.ROW_A], as_on="01-08-2026"))
    assert (older.stored, older.current, older.result) == (True, False, ImportResult.HISTORY)
    assert [h[0] for h in history(user_id)] == ["current", "history"]
    assert [p.instrument_name for p in effective(user_id)] == ["SYNTHETIC POWER LIMITED"]


def test_completely_failed_import_is_never_stored_or_current(tmp_path, user_id):
    run_import(user_id, groww_equity_file(tmp_path / "good.xlsx", rows=[groww_equity.ROW_A], as_on="01-08-2026"))
    bad_row = ["BROKEN LTD", "NOT-AN-ISIN", "abc", -1, None, None, None, None]
    failed = run_import(user_id, groww_equity_file(tmp_path / "bad.xlsx", rows=[bad_row], as_on="01-09-2026"))
    assert (failed.stored, failed.current, failed.batch_id, failed.result) == (False, False, None, ImportResult.FAILED)
    assert (failed.rows_succeeded, failed.rows_failed) == (0, 1) and failed.errors
    assert [b.statement_date for b in batches(user_id)] == [date(2026, 8, 1)]
    # recorded in history with its counts, but never as a snapshot
    assert history(user_id)[-1] == ("failed", "groww", "equity", date(2026, 9, 1), 0, 1, None)
    assert [p.instrument_name for p in effective(user_id)] == ["SAMPLE TEXTILES LIMITED"]


def test_failed_reimport_of_same_identity_keeps_existing_snapshot(tmp_path, user_id):
    run_import(user_id, groww_equity_file(tmp_path / "good.xlsx", rows=[groww_equity.ROW_A]))
    bad_row = ["BROKEN LTD", "NOT-AN-ISIN", "abc", -1, None, None, None, None]
    assert run_import(user_id, groww_equity_file(tmp_path / "bad.xlsx", rows=[bad_row])).stored is False
    assert [p.instrument_name for p in effective(user_id)] == ["SAMPLE TEXTILES LIMITED"]
    (batch,) = batches(user_id)  # the failed attempt with the same identity did not touch the snapshot
    assert (batch.rows_succeeded, batch.rows_failed) == (1, 0)
    assert [h[0] for h in history(user_id)] == ["current", "failed"]


def test_partially_failed_import_becomes_current_with_valid_rows(tmp_path, user_id):
    bad_row = ["BROKEN LTD", "NOT-AN-ISIN", "abc", -1, None, None, None, None]
    outcome = run_import(user_id, groww_equity_file(tmp_path / "p.xlsx", rows=[groww_equity.ROW_A, bad_row]))
    assert (outcome.stored, outcome.current, outcome.rows_succeeded, outcome.rows_failed) == (True, True, 1, 1)
    (batch,) = batches(user_id)
    assert (batch.rows_succeeded, batch.rows_failed) == (1, 1)


def test_valid_empty_statement_is_a_real_empty_snapshot(tmp_path, user_id):
    funds = [groww_mf.tx(units="10"), groww_mf.tx(kind="REDEEM", units="10")]  # fully redeemed, no errors
    run_import(user_id, groww_mf_file(tmp_path / "old.xlsx", to="May 31 2026", transactions=[groww_mf.tx()]))
    outcome = run_import(user_id, groww_mf_file(tmp_path / "new.xlsx", transactions=funds))
    assert (outcome.stored, outcome.current, outcome.rows_succeeded, outcome.errors) == (True, True, 0, [])
    assert effective(user_id) == []  # the newer statement says nothing is held


def test_broker_and_asset_class_snapshots_are_independent(tmp_path, user_id):
    run_import(user_id, groww_equity_file(tmp_path / "ge.xlsx", as_on="01-09-2026"))
    run_import(user_id, zerodha_file(tmp_path / "z.xlsx", holdings=[zerodha.HOLDING_X, zerodha.HOLDING_Y]))
    run_import(user_id, groww_mf_file(tmp_path / "mf.xlsx"))
    # a newer Groww MF statement must not touch Groww equity or Zerodha
    run_import(user_id, groww_mf_file(tmp_path / "mf2.xlsx", to="Jul 31 2026",
                                      transactions=[groww_mf.tx(scheme=groww_mf.BETA)]))
    by_source = {(p.source_broker, p.asset_class) for p in effective(user_id)}
    assert by_source == {("groww", "equity"), ("zerodha", "equity"), ("groww", "mutual_fund")}
    assert [p.instrument_name for p in effective(user_id) if p.asset_class == "mutual_fund"] == [groww_mf.BETA]
    assert len(effective(user_id)) == 1 + 2 + 1


def test_imports_are_scoped_to_their_user(tmp_path, user_id):
    with SessionLocal() as db:
        other = make_user(db, "other@example.com").id
    run_import(user_id, groww_equity_file(tmp_path / "a.xlsx", as_on="01-08-2026"))
    run_import(other, groww_equity_file(tmp_path / "b.xlsx", rows=[groww_equity.ROW_B], as_on="01-09-2026"))
    # the other user's newer statement neither supersedes nor joins this user's snapshot
    assert [p.instrument_name for p in effective(user_id)] == ["SAMPLE TEXTILES LIMITED"]
    assert [p.instrument_name for p in effective(other)] == ["SYNTHETIC POWER LIMITED"]
