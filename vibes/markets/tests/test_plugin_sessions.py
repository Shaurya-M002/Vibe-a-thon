import json
import subprocess
import sys
from pathlib import Path

import pytest
import test_web

from governor.web import Dashboard


@pytest.fixture
def dashboard(tmp_path):
    yield from test_web.dashboard.__wrapped__(tmp_path)


CLI = Path(__file__).parents[1] / "plugins/governor/scripts/governor.py"


def create(client, sid="codex-test", **extra):
    return client.post(
        "/api/plugin/runs", json={"session_id": sid, "mode": "mock", "task": "Codex task", **extra}
    )


def call(client, cid, tool="get_budget", arguments=None, sid="codex-test"):
    return client.post(
        "/api/plugin/calls",
        json={"session_id": sid, "call_id": cid, "tool": tool, "arguments": arguments or {}},
    )


def test_codex_owns_reasoning_and_app_persists_results_without_gemini(dashboard):
    app, client = dashboard
    assert create(client).status_code == 202
    report = client.get("/api/sessions/codex-test").json()
    assert report["status"] == "WAITING"
    assert report["client"]["runner"] == "codex"
    assert call(client, "budget").json()["budget"]["available"] == "10000"
    assert (
        call(
            client,
            "buy",
            "purchase_service",
            {"service_id": "summary", "text": "A useful summary."},
        ).json()["code"]
        == "SETTLED"
    )
    finish = {
        "session_id": "codex-test",
        "status": "COMPLETED",
        "answer": "Codex completed the work.",
    }
    assert client.post("/api/plugin/finish", json=finish).status_code == 200
    assert client.post("/api/plugin/finish", json=finish).status_code == 200
    report = Dashboard(app.settings).report("codex-test")
    assert report["result"]["answer"] == finish["answer"]
    assert report["budget"]["settled"] == "2000"
    assert len(report["tool_results"]) == 2
    assert call(client, "after-close").status_code == 503


def test_creation_and_tool_retries_never_reset_or_spend_twice(dashboard):
    app, client = dashboard
    assert create(client).status_code == 202
    args = {"service_id": "summary", "text": "First paid item."}
    paid = call(client, "same-call", "purchase_service", args).json()
    assert create(client).status_code == 202
    assert call(client, "same-call", "purchase_service", args).json() == paid
    assert (
        call(client, "same-call", "purchase_service", {**args, "text": "Changed"}).status_code
        == 503
    )
    assert create(client, task="Changed mission").status_code == 503
    assert create(client, mode="solana-devnet").status_code == 503
    assert app.ledger.snapshot("codex-test")["settled"] == "2000"
    assert len(app.ledger.sessions()) == 1
    restarted = Dashboard(app.settings)
    assert (
        restarted.plugin.create({"session_id": "codex-test", "mode": "mock", "task": "Codex task"})
        == "codex-test"
    )


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "null"}, {"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}],
)
def test_plugin_writes_keep_browser_origin_boundary(dashboard, headers):
    app, client = dashboard
    for path in ("runs", "calls", "finish"):
        assert client.post("/api/plugin/" + path, json={}, headers=headers).status_code == 403
    assert app.ledger.sessions() == []


def test_plugin_cannot_change_policy_or_control_gemini_session(dashboard):
    app, client = dashboard
    assert create(client, session_cap="999999").status_code == 400
    assert create(client, sid="../bad").status_code == 400
    app.ledger.start("gemini-owned", "Other task")
    assert call(client, "steal", sid="gemini-owned").status_code == 503
    assert create(client).status_code == 202
    assert call(client, "badtool", "raise_cap").status_code == 400
    result = call(
        client, "external", "purchase_service", {"service_id": "bazaar-untrusted", "text": "x"}
    ).json()
    assert result["ok"] is False
    assert app.ledger.snapshot("codex-test")["available"] == "10000"


def test_codex_budget_and_call_limit_are_enforced(dashboard):
    app, client = dashboard
    create(client)
    for i in range(5):
        assert (
            call(
                client, f"buy-{i}", "purchase_service", {"service_id": "summary", "text": str(i)}
            ).json()["code"]
            == "SETTLED"
        )
    denied = call(
        client, "over", "purchase_service", {"service_id": "summary", "text": "six"}
    ).json()
    assert denied["code"] == "SESSION_CAP_EXCEEDED"
    app.plugin.settings = app.settings.model_copy(update={"max_tool_calls": 6})
    assert call(client, "limit").json()["code"] == "TOOL_CALL_LIMIT"


def test_interrupted_calls_remain_visible_and_are_never_reexecuted(dashboard):
    app, client = dashboard
    create(client)
    app.ledger.record(
        "codex-test",
        "CODEX_TOOL_REQUEST",
        {"call_id": "lost", "name": "purchase_service", "request_hash": "unrecoverable"},
    )
    assert Dashboard(app.settings).report("codex-test")["status"] == "INTERRUPTED"
    assert call(client, "lost").status_code == 503
    assert app.ledger.snapshot("codex-test")["settled"] == "0"


def test_standalone_cli_runs_from_other_directories_and_exports(dashboard, tmp_path):
    _, client = dashboard

    def invoke(*args):
        return subprocess.run(
            [sys.executable, str(CLI), "--url", str(client.base_url).rstrip("/"), *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=15,
        )

    assert (
        invoke("start", "--session", "codex-cli-test", "--task", "Codex is the agent").returncode
        == 0
    )
    result = invoke("call", "codex-cli-test", "--call-id", "budget", "--tool", "get_budget")
    assert json.loads(result.stdout)["budget"]["available"] == "10000"
    watched = invoke("watch", "codex-cli-test", "--seconds", "1")
    assert json.loads(watched.stdout.splitlines()[-1])["status"] == "WAITING"
    assert invoke("finish", "codex-cli-test", "--answer", "Done from Codex.").returncode == 0
    exported = json.loads(invoke("export", "codex-cli-test").stdout)
    assert exported["result"]["answer"] == "Done from Codex."
    assert "amount_atomic_usdc" in invoke("export", "codex-cli-test", "--format", "csv").stdout


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://127.0.0.1.evil.example",
        "http://user:pass@localhost:8787",
        "http://localhost:8787/private",
    ],
)
def test_cli_rejects_nonlocal_or_credentialed_origins(url):
    result = subprocess.run(
        [sys.executable, str(CLI), "--url", url, "doctor"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "loopback" in result.stderr


def test_terminal_messages_are_durable_idempotent_and_advisory(dashboard):
    app, client = dashboard
    create(client)
    before = app.ledger.snapshot("codex-test")
    message = {
        "session_id": "codex-test",
        "message_id": "msg-1",
        "role": "user",
        "text": "Find a vendor <script>alert(1)</script>",
    }
    assert client.post("/api/plugin/messages", json=message).status_code == 200
    assert client.post("/api/plugin/messages", json=message).status_code == 200
    assert (
        client.post("/api/plugin/messages", json={**message, "text": "changed"}).status_code == 503
    )
    assert (
        client.post("/api/plugin/messages", json={**message, "role": "system"}).status_code == 400
    )
    assert (
        client.post("/api/plugin/messages", json={**message, "text": "x" * 20001}).status_code
        == 400
    )
    app.ledger.start("not-codex", "Gemini task")
    assert (
        client.post("/api/plugin/messages", json={**message, "session_id": "not-codex"}).status_code
        == 503
    )
    report = Dashboard(app.settings).report("codex-test")
    assert report["conversation"] == [message]
    assert report["tool_results"] == []
    assert report["budget"] == before


def test_selected_limits_enforced_by_ledger_and_survive_restart(dashboard):
    app, client = dashboard
    limits = {
        "session_cap": "3000",
        "per_call_cap": "2000",
        "max_tool_calls": 2,
        "tool_timeout_seconds": 1,
    }
    assert create(client, limits=limits).status_code == 202
    result = call(
        client, "first", "purchase_service", {"service_id": "summary", "text": "First"}
    ).json()
    assert result["code"] == "SETTLED"
    assert call(client, "second").status_code == 200
    assert call(client, "third").json()["code"] == "TOOL_CALL_LIMIT"
    ledger = Dashboard(app.settings).ledger
    assert ledger.snapshot("codex-test")["available"] == "1000"
    assert (
        ledger.reserve("codex-test", "per-call", "summary", "2001").result["code"]
        == "PER_CALL_CAP_EXCEEDED"
    )
    assert (
        ledger.reserve("codex-test", "total", "summary", "1500").result["code"]
        == "SESSION_CAP_EXCEEDED"
    )
    assert create(client, limits=limits).status_code == 202
    assert create(client, limits={**limits, "session_cap": "4000"}).status_code == 503
    assert app.report("codex-test")["client"]["limits"] == limits
    assert (
        call(client, "first", "purchase_service", {"service_id": "summary", "text": "First"}).json()
        == result
    )


@pytest.mark.parametrize(
    "limits",
    [
        {"session_cap": "10001"},
        {"per_call_cap": "3001"},
        {"max_tool_calls": 17},
        {"max_tool_calls": True},
        {"tool_timeout_seconds": 121},
        {"session_cap": "0"},
        {"session_cap": 1000},
        {"max_tool_calls": "2"},
        {"session_cap": "1000", "per_call_cap": "2000"},
    ],
)
def test_invalid_selected_limits_do_not_create_budget(dashboard, limits):
    app, client = dashboard
    assert create(client, limits=limits).status_code == 400
    assert app.ledger.sessions() == []


def test_selected_tool_timeout_preserves_existing_holds(dashboard, monkeypatch):
    import asyncio

    app, client = dashboard
    assert create(client, limits={"tool_timeout_seconds": 1}).status_code == 202
    app.ledger.reserve("codex-test", "existing", "summary", "500")

    class SlowRegistry:
        schemas = {"get_budget": {}}

        async def execute(self, *args):
            await asyncio.sleep(10)
            return {"ok": True}

    monkeypatch.setattr(app.plugin, "registry", lambda *args: SlowRegistry())
    result = call(client, "slow", "get_budget").json()
    assert result == {"ok": False, "code": "TOOL_ERROR", "holds_preserved": True}
    assert app.ledger.snapshot("codex-test")["held"] == "500"
    assert app.active is None
