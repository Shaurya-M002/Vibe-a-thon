import json
import os
import subprocess
import sys
import tomllib

import pytest
import test_web

from governor.app_client import AppClient, AppError
from governor.codex_launcher import command, parser
from governor.codex_mcp import Bridge


@pytest.fixture
def dashboard(tmp_path):
    yield from test_web.dashboard.__wrapped__(tmp_path)


def create(client):
    response = client.post(
        "/api/plugin/runs",
        json={"session_id": "codex-native", "task": "Native MCP test", "mode": "mock"},
    )
    assert response.status_code == 202


def rpc(mid, method, params=None):
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}


def test_real_stdio_handshake_bound_tools_and_retry(dashboard):
    app, client = dashboard
    create(client)
    messages = [
        rpc(1, "initialize", {"protocolVersion": "2024-11-05"}),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        rpc(2, "tools/list"),
        rpc(3, "tools/call", {"name": "get_budget", "arguments": {"call_id": "budget"}}),
    ]
    purchase = {
        "name": "purchase_service",
        "arguments": {"call_id": "buy", "service_id": "summary", "text": "Native MCP purchase"},
    }
    messages.extend(
        [
            rpc(4, "tools/call", purchase),
            rpc(5, "tools/call", purchase),
            rpc(
                6,
                "tools/call",
                {"name": "get_budget", "arguments": {"call_id": "steal", "session_id": "another"}},
            ),
            rpc(
                7,
                "tools/call",
                {"name": "finish_session", "arguments": {"answer": "Done", "status": "COMPLETED"}},
            ),
        ]
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "governor.codex_mcp",
            "--url",
            str(client.base_url).rstrip("/"),
            "--session",
            "codex-native",
        ],
        input="\n".join(map(json.dumps, messages)) + "\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(replies) == 7
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    names = {tool["name"] for tool in replies[1]["result"]["tools"]}
    assert names == {
        "get_budget",
        "get_runway",
        "list_services",
        "purchase_service",
        "summarize_local",
        "discover_vendors",
        "get_vendor_search",
        "get_session",
        "reconcile_receipt",
        "finish_session",
    }
    assert replies[3]["result"] == replies[4]["result"]
    assert replies[5]["result"]["isError"] is True
    assert app.ledger.snapshot("codex-native")["settled"] == "2000"
    assert app.report("codex-native")["status"] == "COMPLETED"


def test_bridge_requires_initialization_and_rejects_unknown_methods(dashboard):
    _, client = dashboard
    create(client)
    bridge = Bridge(AppClient(str(client.base_url)), "codex-native")
    assert bridge.dispatch(rpc(1, "tools/list"))["error"]["code"] == -32002
    assert "result" in bridge.dispatch(rpc(2, "initialize"))
    assert bridge.dispatch(rpc(3, "arbitrary/command"))["error"]["code"] == -32601
    assert bridge.dispatch({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None


def test_invocation_scopes_mcp_config_and_preserves_literal_prompt(tmp_path):
    task = 'literal $(touch /tmp/never) `id` "quotes"'
    args = parser().parse_args(["--exec", "--cwd", str(tmp_path), task])
    client = AppClient()
    cmd = command(args, "/bin/codex", client, "codex-bound", task, "mock", tmp_path / "final.txt")
    config = tomllib.loads(cmd[cmd.index("-c") + 1])["mcp_servers"]["governor"]
    assert config["required"] is True
    assert config["default_tools_approval_mode"] == "approve"
    assert config["args"][-1] == "codex-bound"
    assert config["command"] == sys.executable
    assert task in cmd[-1]
    assert cmd[1] == "exec"
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert "--ignore-user-config" not in cmd
    assert "--model" not in cmd
    args.headless = False
    interactive = command(args, "/bin/codex", client, "codex-bound", task, "mock")
    assert "--no-alt-screen" in interactive
    assert "--output-last-message" not in interactive


def fake_codex(tmp_path, body):
    path = tmp_path / "codex"
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)
    return {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}


def test_launcher_starts_child_and_saves_its_final_answer(dashboard, tmp_path):
    app, client = dashboard
    env = fake_codex(
        tmp_path,
        "import sys\nfrom pathlib import Path\n"
        "target=Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
        "target.write_text('Answer from embedded Codex')\n",
    )
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "governor.codex_launcher",
            "--exec",
            "--url",
            str(client.base_url),
            "--cwd",
            str(tmp_path),
            "Run this task",
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert run.returncode == 0, run.stderr
    sid = app.ledger.sessions()[0]["session_id"]
    report = app.report(sid)
    assert report["result"]["answer"] == "Answer from embedded Codex"
    assert report["budget"]["available"] == "10000"
    assert f"/#sessions/{sid}" in run.stderr


def test_failed_child_keeps_session_and_attachment_preserves_budget(dashboard, tmp_path):
    app, client = dashboard
    create(client)
    client.post(
        "/api/plugin/calls",
        json={
            "session_id": "codex-native",
            "call_id": "paid",
            "tool": "purchase_service",
            "arguments": {"service_id": "summary", "text": "Existing expense"},
        },
    )
    env = fake_codex(tmp_path, "raise SystemExit(7)\n")
    prefix = [
        sys.executable,
        "-m",
        "governor.codex_launcher",
        "--exec",
        "--url",
        str(client.base_url),
        "--session",
        "codex-native",
    ]
    failed = subprocess.run(prefix, env=env, text=True, capture_output=True, timeout=15)
    assert failed.returncode == 7
    assert app.report("codex-native")["status"] == "WAITING"
    assert app.ledger.snapshot("codex-native")["settled"] == "2000"
    changed = subprocess.run(
        [*prefix, "--mode", "solana-devnet"], env=env, text=True, capture_output=True, timeout=15
    )
    assert changed.returncode == 2
    assert len(app.ledger.sessions()) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://user:secret@localhost",
        "http://localhost:8787/remote",
        "http://localhost.evil.example",
    ],
)
def test_app_client_restricts_origin(url):
    with pytest.raises(AppError):
        AppClient(url)


async def test_codex_discovery_uses_bazaar_without_model(dashboard, monkeypatch):
    from governor.discovery import BazaarClient

    app, client = dashboard
    create(client)

    async def search(self, query):
        assert query == "summary"
        return {"resources": [], "partialResults": False}

    monkeypatch.setattr(BazaarClient, "search", search)
    tools = app.plugin.registry("codex-native", "mock")
    result = await tools.execute("discover_vendors", {"query": "summary"})
    assert result["data"]["discovery"]["status"] == "NO_MATCH"
    saved = await tools.execute("get_vendor_search", {})
    assert saved["data"]["discovery"] == result["data"]["discovery"]
    assert app.ledger.snapshot("codex-native")["available"] == "10000"
