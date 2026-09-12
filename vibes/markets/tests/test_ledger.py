import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from governor.config import BudgetPolicy
from governor.ledger import Ledger, LedgerError


@pytest.fixture
def ledger(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3", BudgetPolicy(session_cap=5000, per_call_cap=3000))
    ledger.start("session", "Summarize")
    return ledger


def test_holds_and_exact_cap(ledger):
    assert ledger.reserve("session", "a", "summary", "3000").status == "RESERVED"
    assert ledger.reserve("session", "b", "summary", "2000").status == "RESERVED"
    denied = ledger.reserve("session", "c", "summary", "1")
    assert denied.result["code"] == "SESSION_CAP_EXCEEDED"
    assert ledger.snapshot("session")["available"] == "0"
    decisions = [e for e in ledger.report("session")["events"] if e["kind"] == "DENIED"]
    assert decisions[-1]["data"]["code"] == "SESSION_CAP_EXCEEDED"


def test_commit_is_idempotent_and_does_not_clamp(ledger):
    ledger.reserve("session", "a", "summary", "2000")
    ledger.mark_authorizing("session", "a")
    with pytest.raises(LedgerError, match="differs"):
        ledger.commit("session", "a", "3000", {})
    assert ledger.snapshot("session")["held"] == "2000"
    assert ledger.report("session")["events"][-1]["kind"] == "SETTLEMENT_MISMATCH"
    ledger.commit("session", "a", "2000", {"receipt": "mock-receipt"})
    ledger.commit("session", "a", "2000", {"receipt": "mock-receipt"})
    assert ledger.snapshot("session")["settled"] == "2000"
    assert ledger.snapshot("session")["held"] == "0"


def test_restart_preserves_exposure_and_refuses_policy_changes(ledger):
    ledger.reserve("session", "a", "summary", "3000")
    ledger.mark_authorizing("session", "a")
    restarted = Ledger(ledger.path, ledger.policy)
    restarted.start("session", "Summarize", resume=True)
    assert restarted.snapshot("session")["available"] == "2000"
    with pytest.raises(LedgerError, match="may still settle"):
        restarted.release_unsigned("session", "a", "timeout")
    changed = Ledger(ledger.path, BudgetPolicy(session_cap=9000, per_call_cap=3000))
    with pytest.raises(LedgerError, match="policy"):
        changed.start("session", "Summarize", resume=True)


def test_parallel_reservations_cannot_overspend(ledger):
    def reserve(index):
        return ledger.reserve("session", str(index), "summary", "3000").status

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert results.count("RESERVED") == 1
    assert results.count("DENIED") == 7
    assert ledger.snapshot("session")["available"] == "2000"


def test_duplicate_and_unsigned_release(ledger):
    assert ledger.reserve("session", "a", "summary", "2000").created
    assert not ledger.reserve("session", "a", "summary", "2000").created
    ledger.release_unsigned("session", "a", "cancelled before signing")
    assert ledger.snapshot("session")["available"] == "5000"
    with pytest.raises(LedgerError, match="different payment terms"):
        ledger.reserve("session", "a", "summary", "1000")


def test_resume_missing_session_never_creates_fresh_budget(ledger):
    with pytest.raises(LedgerError, match="does not exist"):
        ledger.start("missing", "Summarize", resume=True)


def test_per_call_denials_do_not_hold_money(ledger):
    denial = ledger.reserve("session", "a", "summary", "3001")
    assert denial.result["code"] == "PER_CALL_CAP_EXCEEDED"
    assert ledger.snapshot("session")["held"] == "0"


def test_deleted_accounting_table_is_not_silently_recreated(ledger):
    ledger.reserve("session", "a", "summary", "2000")
    with sqlite3.connect(ledger.path) as db:
        db.execute("DROP TABLE attempts")
    with pytest.raises(LedgerError, match="incomplete"):
        Ledger(ledger.path, ledger.policy)


def test_empty_existing_file_is_not_treated_as_a_new_ledger(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    path.touch()
    with pytest.raises(LedgerError, match="incomplete"):
        Ledger(path, BudgetPolicy())
