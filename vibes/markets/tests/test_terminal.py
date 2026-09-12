from textual.widgets import Collapsible, Static

from governor.app_client import AppError
from governor.codex_launcher import parser
from governor.terminal import Conversation, Decision, GovernorTerminal, Prompt


class Client:
    origin = "http://127.0.0.1:8787"

    def __init__(self):
        self.requests = []
        self.fail = False
        self.finished = False

    def request(self, path, payload=None):
        self.requests.append((path, payload))
        if path == "/api/state":
            return {"plugin_api": 1}
        if self.fail:
            raise AppError("Lost response")
        if path == "/api/plugin/finish":
            self.finished = True
        return {}

    def report(self, sid):
        return {
            "client": {"runner": "codex"},
            "status": "COMPLETED" if self.finished else "WAITING",
            "budget": {
                "session_cap": "10000",
                "available": "7000",
                "held": "1000",
                "settled": "2000",
                "payment_mode": "mock",
            },
            "tool_results": [],
            "runway": {"state": "TIGHT"},
        }

    def link(self, sid):
        return self.origin + "/#sessions/" + sid


class Transport:
    def __init__(self, argv, cwd, callback):
        self.callback = callback
        self.calls = []
        self.replies = []
        self.closed = False

    async def start(self):
        pass

    async def close(self):
        self.closed = True

    async def request(self, method, params=None):
        self.calls.append((method, params))
        if method.startswith("thread/"):
            return {"thread": {"id": "thread-1"}, "model": "codex-test"}
        if method == "turn/start":
            await self.callback({"method": "turn/started", "params": {"turn": {"id": "turn-1"}}})
            return {"turn": {"id": "turn-1"}}
        if method == "turn/interrupt":
            await self.complete("interrupted")
        return {}

    async def complete(self, status="completed"):
        await self.callback({"method": "turn/completed", "params": {"turn": {"status": status}}})

    async def reply(self, mid, result=None, error=None):
        self.replies.append((mid, result, error))


def terminal(tmp_path, client=None):
    app = GovernorTerminal(
        parser().parse_args(["--cwd", str(tmp_path)]),
        "codex",
        client=client or Client(),
        transport_factory=Transport,
    )
    app.state_file = tmp_path / "state.json"
    return app


async def until(pilot, predicate):
    for _ in range(100):
        if predicate():
            return
        await pilot.pause(0.02)
    raise AssertionError("UI did not reach expected state")


async def test_prompt_stream_budget_followup_stop_and_finish(tmp_path):
    app = terminal(tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        assert not app.created
        app.query_one(Prompt).value = "Inspect my budget"
        await pilot.press("enter")
        await until(pilot, lambda: app.turn_id)
        transport = app.transport
        assert app.busy
        assert "$0.007000" in str(app.query_one("#budget", Static).render())
        for delta in ["Hello ", "from Codex"]:
            await transport.callback(
                {
                    "method": "item/agentMessage/delta",
                    "params": {"itemId": "answer", "delta": delta},
                }
            )
        assert app.messages["answer"][1] == "Hello from Codex"
        await transport.callback(
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "id": "answer",
                        "type": "agentMessage",
                        "text": "Hello from Codex",
                        "phase": "final_answer",
                    }
                },
            }
        )
        await transport.callback(
            {
                "method": "item/started",
                "params": {
                    "item": {
                        "id": "tool",
                        "type": "mcpToolCall",
                        "tool": "get_budget",
                        "arguments": {},
                    }
                },
            }
        )
        assert app.query(Collapsible)
        await transport.complete()
        app.query_one(Prompt).value = "Follow up"
        await pilot.press("enter")
        await until(pilot, lambda: app.turn_id)
        assert app.transport is transport
        assert len([c for c in transport.calls if c[0] == "thread/start"]) == 1
        assert len([r for r in app.client.requests if r[0] == "/api/plugin/runs"]) == 1
        await pilot.press("escape")
        await until(pilot, lambda: not app.busy)
        assert any(c[0] == "turn/interrupt" for c in transport.calls)
        app.query_one(Prompt).value = "/finish"
        await pilot.press("enter")
        await until(pilot, lambda: app.closed_session)
        assert app.client.finished
    assert transport.closed


async def test_lost_create_response_reuses_payload_and_budget(tmp_path):
    client = Client()
    client.fail = True
    app = terminal(tmp_path, client)
    async with app.run_test() as pilot:
        app.submit("first task")
        await until(pilot, lambda: not app.busy)
        client.fail = False
        app.submit("retry with follow-up")
        await until(pilot, lambda: app.turn_id)
        creates = [payload for path, payload in client.requests if path == "/api/plugin/runs"]
        assert len(creates) == 2 and creates[0] == creates[1]
        assert creates[0]["task"] == "first task"


async def test_approval_requires_explicit_choice_and_compact_prompt_history(tmp_path):
    app = terminal(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        app.submit("hello")
        await until(pilot, lambda: app.turn_id)
        assert not app.query_one("#sidebar").display
        await app.transport.callback(
            {
                "id": 99,
                "method": "item/commandExecution/requestApproval",
                "params": {"command": "pwd"},
            }
        )
        await until(pilot, lambda: isinstance(app.screen, Decision))
        assert not app.transport.replies
        await pilot.click("#deny")
        await until(pilot, lambda: bool(app.transport.replies))
        assert app.transport.replies == [(99, {"decision": "decline"}, None)]
        await app.transport.complete()
        app.query_one(Prompt).focus()
        await pilot.press("up")
        assert app.query_one(Prompt).value == "hello"
        await pilot.press("down")
        assert app.query_one(Prompt).value == ""


async def test_conversation_resume_and_immutable_mode(tmp_path):
    app = terminal(tmp_path)
    async with app.run_test() as pilot:
        app.submit("hello")
        await until(pilot, lambda: app.turn_id)
    second = terminal(tmp_path)
    second.args.session = app.sid
    second.sid = app.sid
    async with second.run_test() as pilot:
        second.submit("continue")
        await until(pilot, lambda: second.turn_id)
        assert any(c[0] == "thread/resume" for c in second.transport.calls)
        assert not any(p == "/api/plugin/runs" for p, _ in second.client.requests)
    third = terminal(tmp_path)
    third.args.session = app.sid
    third.args.mode = "solana-devnet"
    async with third.run_test() as pilot:
        third.submit("continue")
        await until(pilot, lambda: not third.busy)
        assert third.transport is None


async def test_stream_follows_new_output_but_respects_scrollback(tmp_path):
    app = terminal(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        pane = app.query_one(Conversation)
        for i in range(12):
            await app.add(f"Message {i}\n" + "Content\n" * 3, "assistant")
        await pilot.pause()
        assert pane.is_vertical_scroll_end
        pane.on_mouse_scroll_up()
        pane.scroll_home(animate=False)
        await pilot.pause()
        await app.add("New output while reading history", "assistant")
        await pilot.pause()
        assert pane.scroll_y == 0
        pane.following = True
        await app.add("Continue following", "assistant")
        await pilot.pause()
        assert pane.is_vertical_scroll_end


async def test_settings_apply_to_next_turn_and_lock_budget_after_creation(tmp_path):
    from textual.widgets import Input, Select

    from governor.terminal_settings import TerminalSettings

    app = terminal(tmp_path)
    original_request = app.client.request
    policy = {
        "session_cap": "10000",
        "per_call_cap": "3000",
        "max_tool_calls": 16,
        "run_timeout_seconds": 120,
    }

    def request(path, payload=None):
        if path == "/api/state":
            return {"plugin_api": 1, "policy": policy}
        return original_request(path, payload)

    app.client.request = request
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.press("f2")
        await until(pilot, lambda: isinstance(app.screen, TerminalSettings))
        app.screen.query_one("#setting-model", Input).value = "my-model"
        app.screen.query_one("#setting-effort", Input).value = "high"
        app.screen.query_one("#setting-session_cap", Input).value = "0.006"
        app.screen.query_one("#setting-per_call_cap", Input).value = "0.002"
        app.screen.query_one("#setting-density", Select).value = "dense"
        await pilot.click("#settings-apply")
        await until(pilot, lambda: not app.settings_open)
        assert app.screen.has_class("dense")
        app.submit("Use my settings")
        await until(pilot, lambda: app.turn_id)
        create = next(
            payload for path, payload in app.client.requests if path == "/api/plugin/runs"
        )
        assert create["limits"]["session_cap"] == "6000"
        turn = next(params for method, params in app.transport.calls if method == "turn/start")
        assert turn["model"] == "my-model" and turn["effort"] == "high"
        await app.transport.complete()
        await pilot.press("f2")
        await until(pilot, lambda: isinstance(app.screen, TerminalSettings))
        assert app.screen.query_one("#setting-session_cap", Input).disabled
        assert app.screen.query_one("#setting-mode", Select).disabled
        app.screen.query_one("#setting-model", Input).value = "second-model"
        await pilot.click("#settings-apply")
        await until(pilot, lambda: not app.settings_open)
        app.submit("Next prompt")
        await until(pilot, lambda: app.turn_id)
        turns = [params for method, params in app.transport.calls if method == "turn/start"]
        assert turns[-1]["model"] == "second-model"
        assert len([path for path, _ in app.client.requests if path == "/api/plugin/runs"]) == 1


async def test_settings_reject_over_cap_and_cancel_without_changes(tmp_path):
    from textual.widgets import Input

    from governor.terminal_settings import TerminalSettings

    app = terminal(tmp_path)
    app.client.request = lambda *args: {
        "plugin_api": 1,
        "policy": {
            "session_cap": "10000",
            "per_call_cap": "3000",
            "max_tool_calls": 16,
            "run_timeout_seconds": 120,
        },
    }
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("f2")
        await until(pilot, lambda: isinstance(app.screen, TerminalSettings))
        app.screen.query_one("#setting-session_cap", Input).value = "1.0"
        app.screen.query_one("#setting-model", Input).value = "unused-model"
        await pilot.click("#settings-apply")
        await pilot.pause()
        assert isinstance(app.screen, TerminalSettings)
        assert "ceilings" in str(app.screen.query_one("#settings-error", Static).render())
        await pilot.press("escape")
        await until(pilot, lambda: not app.settings_open)
        assert app.args.model is None and not app.limits and not app.created
