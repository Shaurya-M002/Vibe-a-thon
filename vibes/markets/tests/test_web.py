"""Exercise the browser boundary and run the real sandbox through HTTP."""

import json
import threading
import time

import httpx
import pytest

from governor.config import BudgetPolicy, Settings
from governor.web import Dashboard, make_server


@pytest.fixture
def dashboard(tmp_path):
    app = Dashboard(Settings(data_dir=tmp_path))
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    with httpx.Client(base_url=origin, headers={"Origin": origin}) as client:
        yield app, client
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_state_and_static_files_do_not_expose_credentials(dashboard):
    app, client = dashboard
    app.settings = app.settings.model_copy(update={"api_key": "private-marker"})
    (app.settings.data_dir / ".env").write_text("SECRET=private-marker")
    response = client.get("/api/state")
    assert response.status_code == 200
    assert response.json()["policy"]["session_cap"] == "10000"
    assert "private-marker" not in response.text
    for path in ("/.env", "/.governor/ledger.sqlite3", "/%2e%2e/web.py"):
        assert client.get(path).status_code == 404
    assert client.get("/").status_code == 200
    assert client.get("/assets/guardian.png").headers["content-type"] == "image/png"
    assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://untrusted.example"},
        {"Host": "untrusted.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "null"},
        {"Content-Type": "text/plain"},
    ],
)
def test_browser_cannot_launch_cross_origin(dashboard, headers):
    app, client = dashboard
    response = client.post("/api/runs", json={"mode": "demo"}, headers=headers)
    assert response.status_code == 403
    assert app.ledger.sessions() == []


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"mode": "bad"},
        {"mode": "gemini", "task": " "},
        {"mode": "demo", "session_cap": "999999"},
    ],
)
def test_run_input_cannot_change_policy(dashboard, payload):
    app, client = dashboard
    assert client.post("/api/runs", json=payload).status_code == 400
    assert app.ledger.sessions() == []


def test_missing_gemini_credentials_is_an_explicit_error(dashboard):
    app, client = dashboard
    response = client.post("/api/runs", json={"mode": "gemini", "task": "Check budget"})
    assert response.status_code == 400
    assert "GEMINI_API_KEY" in response.json()["error"]
    assert app.ledger.sessions() == []


def test_demo_updates_durable_ledger_and_exports(dashboard):
    app, client = dashboard
    response = client.post("/api/runs", json={"mode": "demo"})
    assert response.status_code == 202
    session_id = response.json()["session_id"]
    assert client.post("/api/runs", json={"mode": "demo"}).status_code == 409
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        report = client.get(f"/api/sessions/{session_id}").json()
        if report["status"] != "RUNNING":
            break
        time.sleep(0.05)
    assert report["status"] == "COMPLETED"
    assert report["budget"]["settled"] == "8000"
    assert report["budget"]["held"] == "2000"
    assert report["budget"]["available"] == "0"
    assert report["result"]["simulated_authorizations_this_run"] == 5
    assert len([e for e in report["events"] if e["kind"] == "DENIED"]) == 3
    csv = client.get(f"/api/sessions/{session_id}?format=csv")
    assert len(csv.text.strip().splitlines()) == 5
    assert "mock:" in csv.text
    assert "attachment" in csv.headers["content-disposition"]
    downloaded = client.get(f"/api/sessions/{session_id}?format=json")
    assert downloaded.json()["result"]["status"] == "COMPLETED"
    restarted = Dashboard(app.settings)
    assert restarted.report(session_id)["budget"]["held"] == "2000"
    assert restarted.report(session_id)["result"] == report["result"]


def test_wallet_missing_does_not_invent_a_balance(dashboard):
    _, client = dashboard
    wallet = client.get("/api/wallet").json()
    assert wallet["status"] == "not_configured"
    assert "usdc" not in wallet


def test_policy_change_lists_session_but_refuses_new_budget(dashboard):
    app, client = dashboard
    app.ledger.start("previous", "Original task")
    settings = app.settings.model_copy(update={"policy": BudgetPolicy(session_cap=20000)})
    changed = Dashboard(settings)
    assert changed.state()["sessions"][0]["compatible"] is False
    assert client.get("/api/sessions/missing").status_code == 409


def test_interrupted_run_does_not_appear_live(dashboard):
    app, client = dashboard
    app.ledger.start("interrupted", "A stopped task")
    app.ledger.record("interrupted", "RUN_STARTED", {"model": "scripted"})
    app.ledger.reserve("interrupted", "attempt", "summary", "2000")
    app.ledger.mark_authorizing("interrupted", "attempt")
    result = client.get("/api/sessions/interrupted").json()
    assert result["status"] == "INTERRUPTED"
    assert result["budget"]["held"] == "2000"
    assert json.loads(client.get("/api/state").text)["active_session"] is None


def test_worker_finishing_during_report_is_not_labelled_interrupted(dashboard, monkeypatch):
    app, _ = dashboard
    app.ledger.start("fast-run", "Fast task")
    app.active = "fast-run"
    original = app.ledger.report
    finished = False

    def snapshot_then_finish(sid):
        nonlocal finished
        snapshot = original(sid)
        if not finished:
            finished = True
            app.ledger.record(sid, "RUN_FINISHED", {"status": "COMPLETED"})
            app.active = None
        return snapshot

    monkeypatch.setattr(app.ledger, "report", snapshot_then_finish)
    assert app.report("fast-run")["status"] == "RUNNING"
    assert app.report("fast-run")["status"] == "COMPLETED"


def test_runway_demo_over_http_finishes_the_caller_plan(dashboard):
    _, client = dashboard
    response = client.post("/api/runs", json={"mode": "runway-demo"})
    assert response.status_code == 202
    session_id = response.json()["session_id"]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        report = client.get(f"/api/sessions/{session_id}").json()
        if report["status"] != "RUNNING":
            break
        time.sleep(0.1)
    assert report["status"] == "COMPLETED"
    assert report["runway"]["tasksCompleted"] == 10
    assert report["runway"]["tasksRemaining"] == 0
    assert report["budget"]["available"] == "4000"
    assert len(report["task_plan"]) == 10


def test_browser_cannot_replace_a_scripted_demo_plan(dashboard):
    _, client = dashboard
    assert (
        client.post("/api/runs", json={"mode": "runway-demo", "task_list": []}).status_code == 400
    )


@pytest.mark.parametrize(
    "payload",
    [[], {}, {"query": " "}, {"query": "x" * 401}, {"query": "weather", "network": "mainnet"}],
)
def test_discovery_validates_query_before_creating_session(dashboard, payload):
    app, client = dashboard
    assert client.post("/api/discovery", json=payload).status_code == 400
    assert app.ledger.sessions() == []


def test_discovery_retains_same_origin_boundary(dashboard):
    app, client = dashboard
    assert (
        client.post(
            "/api/discovery",
            json={"query": "weather"},
            headers={"Origin": "https://untrusted.example"},
        ).status_code
        == 403
    )
    assert app.ledger.sessions() == []


def test_discovery_session_updates_and_survives_restart(dashboard, monkeypatch):
    from google.genai import types

    from governor.discovery import VendorScout

    app, client = dashboard
    app.settings = app.settings.model_copy(update={"backend": "vertex", "project": "test"})

    class Model:
        def __init__(self, settings):
            pass

        async def generate(self, *args):
            return types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(
                            role="model",
                            parts=[
                                types.Part(
                                    function_call=types.FunctionCall(
                                        name="submit_search_plan", args={"queries": ["weather"]}
                                    )
                                )
                            ],
                        ),
                        finish_reason=types.FinishReason.STOP,
                    )
                ]
            )

        async def close(self):
            pass

    class Registry:
        async def search(self, query):
            return {"resources": [], "partialResults": False}

    class Scout(VendorScout):
        def __init__(self, *args):
            super().__init__(*args, client=Registry())

    monkeypatch.setattr("governor.web.GeminiModel", Model)
    monkeypatch.setattr("governor.web.VendorScout", Scout)
    response = client.post("/api/discovery", json={"query": "weather"})
    assert response.status_code == 202
    session_id = response.json()["session_id"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        report = client.get(f"/api/sessions/{session_id}").json()
        if report["status"] != "RUNNING":
            break
        time.sleep(0.02)
    assert report["status"] == "COMPLETED"
    assert report["discovery"]["status"] == "NO_MATCH"
    assert report["budget"]["available"] == "10000"
    assert report["attempts"] == []
    assert Dashboard(app.settings).report(session_id)["discovery"] == report["discovery"]


@pytest.mark.parametrize(
    "payload",
    [{"mode": "demo", "discover": True}, {"mode": "gemini", "task": "weather", "discover": "yes"}],
)
def test_discovery_flag_is_strict_and_demos_stay_offline(dashboard, payload):
    app, client = dashboard
    assert client.post("/api/runs", json=payload).status_code == 400
    assert app.ledger.sessions() == []
