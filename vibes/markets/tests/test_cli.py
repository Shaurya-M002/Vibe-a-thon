import csv
import io
import json
import os
import subprocess
import sys


def invoke(tmp_path, *arguments):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GEMINI_", "GOVERNOR_", "GOOGLE_"))
    }
    return subprocess.run(
        [sys.executable, "-m", "governor", *arguments, "--data-dir", str(tmp_path / "state")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_offline_demo_audit_and_resume(tmp_path):
    first = invoke(tmp_path, "demo", "--session", "smoke")
    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result["status"] == "COMPLETED"
    assert result["model_mode"] == "scripted"
    assert result["payment_mode"] == "mock"
    assert result["budget"]["settled"] == "8000"
    assert result["budget"]["held"] == "2000"
    assert result["budget"]["available"] == "0"
    assert result["simulated_authorizations_this_run"] == 5
    with open(result["audit_path"]) as file:
        audit = json.load(file)
    codes = {event["data"].get("code") for event in audit["events"]}
    assert {"PER_CALL_CAP_EXCEEDED", "PRICE_CHANGED", "SESSION_CAP_EXCEEDED"} <= codes
    repeated = invoke(tmp_path, "demo", "--session", "smoke")
    assert repeated.returncode == 2
    resumed = invoke(tmp_path, "demo", "--session", "smoke", "--resume")
    assert resumed.returncode == 0, resumed.stderr
    resumed_result = json.loads(resumed.stdout)
    assert resumed_result["budget"] == result["budget"]
    assert resumed_result["simulated_authorizations_this_run"] == 0
    report = invoke(tmp_path, "report", "smoke", "--format", "csv")
    assert report.returncode == 0
    expenses = list(csv.DictReader(io.StringIO(report.stdout)))
    assert len(expenses) == 4
    assert sum(int(row["amount_atomic_usdc"]) for row in expenses) == 8000
    assert all(row["receipt"].startswith("mock:") for row in expenses)


def test_live_mode_requires_credentials_and_does_not_silently_use_script(tmp_path):
    result = invoke(tmp_path, "run", "Summarize a document")
    assert result.returncode == 2
    assert result.stdout == ""
    assert not (tmp_path / "state" / "ledger.sqlite3").exists()


def test_missing_resume_fails_without_creating_ledger(tmp_path):
    result = invoke(tmp_path, "demo", "--session", "missing", "--resume")
    assert result.returncode == 2
    assert not (tmp_path / "state" / "ledger.sqlite3").exists()


def test_session_path_traversal_is_rejected(tmp_path):
    result = invoke(tmp_path, "demo", "--session", "../escape")
    assert result.returncode == 2
    assert not (tmp_path / "state").exists()
