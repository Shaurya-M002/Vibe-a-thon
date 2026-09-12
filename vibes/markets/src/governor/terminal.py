"""Governor's interactive terminal, backed by one persistent Codex conversation."""

import asyncio
import json
import os
import time
import uuid
import webbrowser
from decimal import Decimal

from platformdirs import user_state_path
from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Button, Collapsible, Footer, Input, Static

from governor.app_client import AppClient, AppError
from governor.codex_launcher import mcp_configuration
from governor.codex_transport import CodexTransport
from governor.plugin_sessions import identifier
from governor.terminal_settings import TerminalSettings
from governor.terminal_text import AssistantReply


def dollars(value):
    return f"${Decimal(str(value)) / 1_000_000:.6f}"


class Prompt(Input):
    BINDINGS = [
        Binding("up", "previous", "Previous prompt", show=False),
        Binding("down", "next", "Next prompt", show=False),
    ]

    def __init__(self):
        super().__init__(placeholder="Ask Codex anything…", id="prompt", max_length=20000)
        self.history = []
        self.position = 0
        self.draft = ""

    def action_previous(self):
        if self.history and self.position > 0:
            if self.position == len(self.history):
                self.draft = self.value
            self.position -= 1
            self.value = self.history[self.position]
            self.cursor_position = len(self.value)

    def action_next(self):
        if self.position < len(self.history):
            self.position += 1
            self.value = (
                self.history[self.position] if self.position < len(self.history) else self.draft
            )
            self.cursor_position = len(self.value)

    def remember(self, value):
        self.history.append(value)
        self.position = len(self.history)
        self.draft = ""
        self.value = ""


class Conversation(VerticalScroll):
    """Follow streamed output until the user deliberately scrolls back."""

    following = True

    def follow(self):
        if self.following:
            self.call_after_refresh(self.scroll_end, animate=False, immediate=True)

    def on_mouse_scroll_up(self):
        self.following = False

    def on_mouse_scroll_down(self):
        self.call_after_refresh(self.check_follow)

    def check_follow(self):
        self.following = self.is_vertical_scroll_end

    def on_key(self, event):
        if event.key in ("up", "pageup", "home"):
            self.following = False
        elif event.key == "end":
            self.following = True
        elif event.key in ("down", "pagedown"):
            self.call_after_refresh(self.check_follow)


class Decision(ModalScreen):
    """An explicit per-request response; never grants session-wide command approval."""

    BINDINGS = [("escape", "deny", "Cancel")]
    DEFAULT_CSS = """
    Decision { align: center middle; background: $background 75%; }
    #decision { width: 85%; max-width: 100; height: auto; max-height: 90%;
        border: thick #ff8a3d; padding: 1 2; background: #181818; }
    #decision-body { height: auto; max-height: 24; }
    #decision-actions { height: 3; margin-top: 1; }
    #decision-actions Button { margin-right: 2; }
    .answer { margin-top: 1; }
    """

    def __init__(self, message):
        super().__init__()
        self.message = message
        self.questions = message.get("params", {}).get("questions", [])

    def compose(self):
        with Vertical(id="decision"):
            yield Static("CODEX NEEDS YOUR INPUT", classes="orange")
            with VerticalScroll(id="decision-body"):
                if self.questions:
                    for index, question in enumerate(self.questions):
                        options = question.get("options") or []
                        labels = "\n".join(
                            f"• {o['label']}: {o.get('description', '')}" for o in options
                        )
                        yield Static(Text(question["question"] + "\n" + labels))
                        yield Input(
                            placeholder="Type your answer",
                            id=f"answer-{index}",
                            classes="answer",
                            password=question.get("isSecret", False),
                        )
                else:
                    yield Static(Text(json.dumps(self.message.get("params", {}), indent=2)))
            with Horizontal(id="decision-actions"):
                yield Button(
                    "Send answers" if self.questions else "Allow once",
                    id="allow",
                    variant="warning",
                )
                yield Button("Cancel" if self.questions else "Deny", id="deny")

    def on_button_pressed(self, event):
        if event.button.id == "deny":
            self.action_deny()
        elif self.questions:
            values = [
                self.query_one(f"#answer-{i}", Input).value.strip()
                for i in range(len(self.questions))
            ]
            if not all(values):
                self.notify("Answer each question before sending.")
                return
            self.dismiss(
                {
                    "answers": {
                        q["id"]: {"answers": [value]}
                        for q, value in zip(self.questions, values, strict=True)
                    }
                }
            )
        else:
            self.dismiss({"decision": "accept"})

    def action_deny(self):
        self.dismiss({"answers": {}} if self.questions else {"decision": "decline"})


class GovernorTerminal(App):
    TITLE = "Governor / Codex"
    BINDINGS = [
        ("ctrl+q", "leave", "Quit"),
        ("escape", "stop", "Stop"),
        ("ctrl+o", "dashboard", "Open app"),
        ("ctrl+l", "focus_prompt", "Prompt"),
        ("f2", "settings", "Settings"),
    ]
    CSS = """
    Screen { background: #101010; color: #efeee9; }
    #masthead { height: 3; padding: 0 2; border-bottom: solid #35312b; background: #191715; }
    #brand { padding-top: 1; width: 1fr; color: #ff8a3d; text-style: bold; }
    #connection { padding-top: 1; width: auto; color: #aaa59d; }
    #settings-button { width: 16; min-width: 16; height: 3; margin-left: 2;
        background: #2c241b; color: #ff994f; border: none; }
    #parameters { height: 2; padding: 0 2; color: #a8a39a; background: #191715; }
    #workspace { height: 1fr; }
    #conversation { width: 1fr; padding: 1 2; scrollbar-color: #815337; }
    #welcome { padding: 1 2; margin-bottom: 1; border-left: thick #ff8a3d; background: #1c1916; }
    #sidebar { width: 31; padding: 1 2; border-left: solid #35312b; background: #151413; }
    #budget { height: auto; }
    #session { margin-top: 2; height: auto; color: #a8a39a; }
    #budget-note { color: #847f78; margin-top: 2; height: auto; }
    .message { height: auto; padding: 1 2; margin-bottom: 1; }
    .user { background: #26201a; border-left: thick #ff8a3d; }
    .assistant { border-left: solid #51483d; }
    .dense .message { padding: 0 2; margin-bottom: 0; }
    .dense .tool { margin-bottom: 0; }
    .notice { color: #aaa59d; padding: 0 2; margin-bottom: 1; height: auto; }
    .orange { color: #ff8a3d; text-style: bold; }
    .tool { margin-bottom: 1; padding: 0 1; border: solid #39332b; background: #171614; }
    .tool Static { height: auto; max-height: 18; overflow-y: auto; }
    #compact-budget { display: none; height: 1; padding: 0 3; color: #aaa59d; }
    .compact #compact-budget { display: block; }
    #status { height: 1; padding: 0 3; color: #ff8a3d; }
    #composer { height: 5; padding: 0 2; }
    #prompt { width: 1fr; border: tall #ff8a3d; background: #211c17; }
    #send { min-width: 10; width: 10; margin-left: 1; background: #ff8a3d; color: #101010; }
    Footer { background: #191715; }
    FooterKey > .footer-key--key { background: #30271e; color: #ff8a3d; }
    .compact #sidebar, .no-sidebar #sidebar { display: none; }
    .no-sidebar #compact-budget { display: block; }
    """

    def __init__(
        self, args, executable, initial_task=None, *, client=None, transport_factory=CodexTransport
    ):
        super().__init__()
        self.register_theme(
            Theme(
                name="governor",
                primary="#ff994f",
                secondary="#b4e3a7",
                accent="#ff994f",
                warning="#ff994f",
                success="#b4e3a7",
                foreground="#efeee9",
                background="#101010",
                surface="#171614",
                panel="#211c17",
            )
        )
        self.theme = "governor"
        self.args, self.executable, self.initial_task = args, executable, initial_task
        self.client = client or AppClient(args.url)
        self.transport_factory = transport_factory
        self.sid = identifier(args.session) if args.session else "codex-" + uuid.uuid4().hex[:16]
        self.mode = args.mode or "mock"
        self.effort = ""
        self.effective_model = args.model or "Configured model"
        self.limits = {}
        self.policy = {}
        self.display_settings = {
            "density": "comfortable",
            "sidebar": True,
            "expand_tools": False,
            "refresh": 1.5,
        }
        self.turn_started_at = None
        self.status_text = "○ Ready — type a prompt below"
        self.settings_open = False
        self.created = False
        self.payload = None
        self.transport = None
        self.thread_id = None
        self.turn_id = None
        self.busy = False
        self.closed_session = False
        self.polling = False
        self.messages = {}
        self.tools = {}
        self.last_answer = ""
        self.last_turn_status = None
        self.mirror_failed = False
        self.report = None
        self.exiting = False
        self.request_lock = asyncio.Lock()
        self.state_file = user_state_path("governor") / "threads" / f"{self.sid}.json"

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("▟ GOVERNOR   /   CODEX", id="brand")
            yield Static("READY", id="connection")
            yield Button("Settings F2", id="settings-button")
        yield Static("", id="parameters")
        with Horizontal(id="workspace"):
            with Conversation(id="conversation"):
                yield Static(
                    Text(
                        "BUILD WITH CODEX. STAY WITHIN BUDGET.\n\n"
                        "Explore your project, find a vendor, or put an idea to work.\n"
                        "Your tools, conversation, and spending stay together.\n\n"
                        "01  Press F2 to tune your session\n"
                        "02  Type a prompt to begin\n"
                        "03  Follow every tool call and budget update"
                    ),
                    id="welcome",
                )
            with VerticalScroll(id="sidebar"):
                yield Static("BUDGET\n\nStarts with your first prompt", id="budget")
                yield Static(
                    Text(
                        f"SESSION\n{self.sid}\n\nMODE\n{self.mode}\n\nWORKSPACE\n{self.args.cwd.resolve()}"
                    ),
                    id="session",
                )
                yield Static(
                    "USDC service budget only.\nCodex inference uses your\nexisting Codex account.",
                    id="budget-note",
                )
        yield Static("Service budget starts with your first prompt", id="compact-budget")
        yield Static("○ Ready — type a prompt below", id="status")
        with Horizontal(id="composer"):
            yield Prompt()
            yield Button("Send ↵", id="send")
        yield Footer()

    def on_mount(self):
        self.query_one(Prompt).focus()
        self.budget_timer = self.set_interval(1.5, self.poll_budget)
        self.set_interval(0.5, self.refresh_status)
        self.refresh_parameters()
        self.query_one(Prompt).border_title = " YOUR PROMPT "
        if self.initial_task:
            self.submit(self.initial_task)
        elif self.args.session:
            self.attach()

    def on_resize(self, event):
        self.screen.set_class(event.size.width < 100, "compact")

    def status(self, text):
        self.status_text = text
        self.refresh_status()

    def refresh_status(self):
        text = self.status_text
        if self.busy and self.turn_started_at is not None:
            text += f"  ·  {int(time.monotonic() - self.turn_started_at)}s"
        color = "#ff994f" if self.busy else "#b4e3a7"
        self.query_one("#status", Static).update(Text(text, style=color))
        self.query_one("#send", Button).label = "Stop ■" if self.busy else "Send ↵"

    def refresh_parameters(self):
        line = Text("● ", style="#b4e3a7" if self.mode == "mock" else "#ff994f")
        line.append("MOCK" if self.mode == "mock" else "DEVNET", style="bold #ffffff")
        line.append("  /  " + (self.args.model or self.effective_model))
        line.append("  /  " + (self.effort or "current effort"))
        line.append("  /  " + self.display_settings["density"])
        self.query_one("#parameters", Static).update(line)

    @work
    async def action_settings(self):
        if self.settings_open:
            return
        if self.busy:
            self.notify("Wait for this turn or press Esc before changing session settings.")
            return
        self.settings_open = True
        try:
            try:
                state = await asyncio.to_thread(self.client.request, "/api/state")
                self.policy = state.get("policy", {})
            except (ValueError, OSError):
                pass
            if self.busy:
                return
            values = {
                **self.display_settings,
                "model": self.args.model or "",
                "effort": self.effort,
                "mode": self.mode,
                "tool_approval": self.args.tool_approval,
                "limits": self.limits,
            }
            if self.report:
                values["limits"] = self.report.get("client", {}).get(
                    "limits",
                    {
                        **self.report["budget"],
                    },
                )
            result = await self.push_screen_wait(
                TerminalSettings(
                    values,
                    self.policy,
                    locked=bool(self.created or self.payload is not None or self.args.session),
                )
            )
            if result is not None:
                self.args.model = result["model"] or None
                self.effort = result["effort"]
                if not (self.created or self.payload is not None or self.args.session):
                    self.mode = result["mode"]
                    self.args.mode = self.mode
                    self.args.tool_approval = result["tool_approval"]
                    self.limits = result["limits"]
                self.display_settings = {key: result[key] for key in self.display_settings}
                self.screen.set_class(result["density"] == "dense", "dense")
                self.screen.set_class(not result["sidebar"], "no-sidebar")
                for box, _ in self.tools.values():
                    box.collapsed = not result["expand_tools"]
                self.budget_timer.stop()
                self.budget_timer = self.set_interval(result["refresh"], self.poll_budget)
                self.refresh_parameters()
                self.query_one("#session", Static).update(
                    Text(
                        f"SESSION\n{self.sid}\n\nMODE\n{self.mode}\n\n"
                        f"WORKSPACE\n{self.args.cwd.resolve()}"
                    )
                )
                self.status("○ Settings applied · ready for your next prompt")
                self.query_one(Prompt).focus()
        finally:
            self.settings_open = False

    async def add(self, text, kind="notice"):
        content = (
            AssistantReply(text.removeprefix("CODEX\n")) if kind == "assistant" else Text(text)
        )
        widget = Static(content, classes="message " + kind if kind != "notice" else "notice")
        pane = self.query_one(Conversation)
        await pane.mount(widget)
        pane.follow()
        return widget

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "prompt":
            self.submit(event.value)

    def on_button_pressed(self, event):
        if event.button.id == "settings-button":
            self.action_settings()
        elif event.button.id == "send":
            if self.busy:
                self.action_stop()
            else:
                self.submit(self.query_one(Prompt).value)

    def submit(self, text):
        text = text.strip()
        if not text:
            return
        if text == "/quit":
            self.action_leave()
            return
        if text == "/stop":
            self.action_stop()
            return
        if text == "/settings":
            self.query_one(Prompt).value = ""
            self.action_settings()
            return
        if text == "/app":
            self.action_dashboard()
            self.query_one(Prompt).value = ""
            return
        if self.settings_open:
            self.notify("Close Settings before sending your prompt.")
            return
        if self.busy:
            self.notify("Codex is working. Esc stops the current turn; your draft stays here.")
            return
        self.query_one(Prompt).remember(text)
        if text in ("/help", "/budget"):
            self.show_info(text)
        elif text == "/finish":
            self.finish()
        elif text.startswith("/"):
            self.notify("Unknown command. Type /help.")
        elif self.closed_session:
            self.notify("This session is finished. Launch governor-codex for a new session.")
        else:
            self.busy = True
            self.run_prompt(text)

    @work
    async def show_info(self, command):
        if command == "/budget":
            await self.poll_budget()
            await self.add(json.dumps((self.report or {}).get("budget", {}), indent=2))
        else:
            await self.add(
                "/settings  Tune model, budget and display · F2\n"
                "/budget  Show ledger totals     /app  Open dashboard\n"
                "/stop  Interrupt Codex          /finish  Save answer and close budget\n"
                "/quit  Leave session open       ↑ ↓  Recall prompts\n"
                "Tool rows expand to show arguments, output and errors."
            )

    async def ensure_session(self, task):
        if self.created:
            return
        state = await asyncio.to_thread(self.client.request, "/api/state")
        self.policy = state.get("policy", {})
        if state.get("plugin_api") != 1:
            raise AppError("Restart governor-web with the current Governor version.")
        if self.args.session:
            report = await asyncio.to_thread(self.client.report, self.sid)
            if report.get("client", {}).get("runner") != "codex":
                raise AppError("Only Codex-owned Governor sessions can be attached.")
            if report["status"] in ("COMPLETED", "STOPPED", "RUNNING"):
                raise AppError(
                    "Session is finished or executing a tool; it cannot be attached now."
                )
            self.mode = report["budget"]["payment_mode"]
            if self.args.mode is not None and self.args.mode != self.mode:
                raise AppError("Cannot change the existing session's payment mode.")
            if self.args.task_list:
                raise AppError("An attached session cannot replace its task plan.")
            for message in report.get("conversation", []):
                role = message["role"]
                await self.add(
                    ("YOU" if role == "user" else "CODEX") + "\n" + message["text"], role
                )
        else:
            # Keep both identity and payload across lost responses; never allocate a fresh cap.
            if self.payload is None:
                self.payload = {"session_id": self.sid, "task": task, "mode": self.mode}
                if self.limits:
                    self.payload["limits"] = self.limits.copy()
                if self.args.task_list:
                    self.payload["task_list"] = json.loads(self.args.task_list.read_text())
            await asyncio.to_thread(self.client.request, "/api/plugin/runs", self.payload)
        self.created = True
        self.refresh_parameters()
        self.query_one("#session", Static).update(
            Text(
                f"SESSION\n{self.sid}\n\nMODE\n{self.mode}\n\nWORKSPACE\n{self.args.cwd.resolve()}"
            )
        )
        await self.add("Session connected · " + self.client.link(self.sid))
        await self.poll_budget()

    @work
    async def attach(self):
        try:
            await self.ensure_session("")
        except (ValueError, OSError) as exc:
            await self.add(str(exc))

    async def connect_codex(self):
        if self.transport:
            return
        argv = [
            self.executable,
            "app-server",
            "-c",
            mcp_configuration(self.args, self.client, self.sid),
        ]
        if self.args.profile:
            argv.extend(["-c", "profile=" + json.dumps(self.args.profile)])
        transport = self.transport_factory(argv, self.args.cwd.resolve(), self.on_codex_event)
        self.transport = transport
        try:
            await transport.start()
            params = {
                "cwd": str(self.args.cwd.resolve()),
                "developerInstructions": (
                    "You are running inside Governor. You are the reasoning agent; do "
                    "not invoke Gemini. "
                    f"The Governor budget session is {self.sid}, payment mode {self.mode}. "
                    "Use the preloaded governor MCP tools for budget, Bazaar "
                    "discovery and purchases. "
                    "Call get_budget and list_services at the start, with stable call IDs. "
                    "Do not create another budget session, reset caps, or send funds "
                    "outside the gate. "
                    "Respect refusals and uncertain holds. Assess discovered vendors yourself. "
                    "Keep this session open across prompts. Only call finish_session when the user "
                    "explicitly asks to finish the entire session. get_session "
                    "supplies prior evidence. "
                    "For longer replies, use short paragraphs and Markdown headings. "
                    "Bold a few key takeaways; use inline code for commands and paths. "
                    "Keep emphasis selective so long responses are easy to scan."
                ),
            }
            for flag in ("model", "sandbox"):
                if value := getattr(self.args, flag):
                    params[flag] = value
            method = "thread/start"
            if self.state_file.is_file():
                saved = json.loads(self.state_file.read_text())
                if saved["origin"] != self.client.origin or saved["cwd"] != params["cwd"]:
                    raise AppError("Saved Codex conversation belongs to another app or workspace.")
                params["threadId"] = saved["thread_id"]
                method = "thread/resume"
            result = await transport.request(method, params)
            self.thread_id = result["thread"]["id"]
            self.state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temp = self.state_file.with_suffix(".tmp")
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as file:
                json.dump(
                    {
                        "origin": self.client.origin,
                        "cwd": params["cwd"],
                        "thread_id": self.thread_id,
                    },
                    file,
                )
            temp.replace(self.state_file)
            self.effective_model = result.get("model", "CODEX")
            self.query_one("#connection", Static).update(Text("● CONNECTED", style="#b4e3a7"))
            self.refresh_parameters()
            if method == "thread/resume":
                await self.add(
                    "Resumed the saved Codex conversation with the same Governor budget."
                )
        except BaseException:
            await transport.close()
            self.transport = None
            raise

    @work
    async def run_prompt(self, text):
        self.query_one(Conversation).following = True
        for welcome in self.query("#welcome"):
            await welcome.remove()
        await self.add("YOU\n" + text, "user")
        self.turn_started_at = time.monotonic()
        self.status("◌ Connecting Codex…")
        try:
            await self.ensure_session(text)
            await self.mirror_message("user", text)
            await self.connect_codex()
            self.status("◌ Codex is working · Esc to stop")
            result = await self.transport.request(
                "turn/start",
                {
                    "threadId": self.thread_id,
                    "input": [{"type": "text", "text": text}],
                    **({"model": self.args.model} if self.args.model else {}),
                    **({"effort": self.effort} if self.effort else {}),
                },
            )
            if self.args.model:
                self.effective_model = self.args.model
                self.refresh_parameters()
            # turn/completed may arrive before the response; do not re-mark a finished turn busy.
            if self.busy:
                self.turn_id = result.get("turn", {}).get("id", self.turn_id)
        except (ValueError, OSError, TimeoutError) as exc:
            self.busy = False
            self.status("○ Needs attention · your session and budget are preserved")
            await self.add(str(exc))

    async def on_codex_event(self, event):
        method, params = event.get("method", ""), event.get("params", {})
        if "id" in event:
            self.handle_request(event)
            return
        if method == "turn/started":
            self.turn_id = params["turn"]["id"]
        elif method == "item/agentMessage/delta":
            key = params.get("itemId", "message")
            if key not in self.messages:
                self.messages[key] = [await self.add("CODEX\n", "assistant"), ""]
            entry = self.messages[key]
            entry[1] += params.get("delta", "")
            entry[0].update(AssistantReply(entry[1]))
            self.query_one(Conversation).follow()
        elif method in ("item/started", "item/completed"):
            item = params.get("item", {})
            kind, key = item.get("type"), item.get("id")
            if kind == "agentMessage" and method == "item/completed":
                answer = item.get("text", "")
                if item.get("phase") != "commentary":
                    self.last_answer = answer or self.last_answer
                if answer:
                    self.mirror_message_worker("assistant", answer, key)
                if key not in self.messages:
                    self.messages[key] = [await self.add("CODEX\n" + answer, "assistant"), answer]
                elif answer:
                    self.messages[key][1] = answer
                    self.messages[key][0].update(AssistantReply(answer))
                self.query_one(Conversation).follow()
            elif kind in (
                "mcpToolCall",
                "commandExecution",
                "fileChange",
                "webSearch",
                "dynamicToolCall",
            ):
                done = method == "item/completed"
                name = item.get("tool") or item.get("command") or kind
                failed = item.get("status") in ("failed", "declined") or bool(item.get("error"))
                marker = "!" if failed else "✓" if done else "◌"
                title = escape(f"{marker} {name}"[:160])
                detail = json.dumps(item, indent=2, ensure_ascii=False)[:16000]
                if key not in self.tools:
                    body = Static(Text(detail))
                    box = Collapsible(
                        body,
                        title=title,
                        collapsed=not self.display_settings["expand_tools"],
                        classes="tool",
                    )
                    await self.query_one("#conversation").mount(box)
                    self.tools[key] = (box, body)
                else:
                    box, body = self.tools[key]
                    box.title = title
                    body.update(Text(detail))
                self.query_one(Conversation).follow()
                self.status(f"{'Finished' if done else 'Running'} · {name}"[:200])
        elif method == "item/commandExecution/outputDelta":
            if row := self.tools.get(params.get("itemId")):
                row[1].update(Text(str(row[1].render())[-12000:] + params.get("delta", "")))
        elif method == "item/mcpToolCall/progress":
            self.status(params.get("message", "Tool working…"))
        elif method == "turn/completed":
            turn = params.get("turn", {})
            self.busy = False
            self.turn_id = None
            self.last_turn_status = turn.get("status")
            self.status("○ " + turn.get("status", "completed").capitalize() + " · type a follow-up")
            if turn.get("error"):
                await self.add("Codex: " + json.dumps(turn["error"]))
            self.poll_budget_worker()
        elif method == "error":
            await self.add("Codex: " + params.get("error", {}).get("message", "An error occurred"))
        elif method == "governor/disconnected":
            self.busy = False
            self.turn_id = None
            self.query_one("#connection", Static).update("DISCONNECTED")
            self.status("○ Codex disconnected · quit and reopen with --session " + self.sid)

    @work
    async def handle_request(self, event):
        async with self.request_lock:
            method = event["method"]
            self.status("◈ Waiting for your input")
            if method in (
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
                "item/tool/requestUserInput",
            ):
                result = await self.push_screen_wait(Decision(event))
                await self.transport.reply(event["id"], result)
            elif method == "item/permissions/requestApproval":
                # Extra permission profiles cannot be meaningfully reviewed by the command modal.
                await self.transport.reply(event["id"], {"permissions": {}, "scope": "turn"})
                await self.add(
                    "Codex requested an extra permission profile. No extra "
                    "permissions granted; use --native to review it in Codex."
                )
            else:
                await self.transport.reply(
                    event["id"],
                    error={"code": -32601, "message": "Request unsupported by Governor terminal"},
                )
                await self.add(
                    "Unsupported Codex request: " + method + ". Use --native for this interaction."
                )
            self.status("◌ Codex is working · Esc to stop" if self.busy else "○ Ready")

    @work(group="mirror")
    async def mirror_message_worker(self, role, text, message_id=None):
        await self.mirror_message(role, text, message_id)

    async def mirror_message(self, role, text, message_id=None):
        try:
            await asyncio.to_thread(
                self.client.request,
                "/api/plugin/messages",
                {
                    "session_id": self.sid,
                    "message_id": identifier(message_id) if message_id else uuid.uuid4().hex,
                    "role": role,
                    "text": text[:20000],
                },
            )
        except (ValueError, OSError):
            if not self.mirror_failed:
                self.mirror_failed = True
                await self.add(
                    "Conversation sync unavailable. Restart governor-web with the "
                    "current version. Governor tools still use the existing ledger."
                )

    @work
    async def poll_budget_worker(self):
        await self.poll_budget()

    async def poll_budget(self):
        if not self.created or self.polling:
            return
        self.polling = True
        try:
            self.report = await asyncio.to_thread(self.client.report, self.sid)
            budget = self.report["budget"]
            limits = self.report.get("client", {}).get("limits", {})
            max_calls = limits.get("max_tool_calls", self.policy.get("max_tool_calls", "—"))
            timeout = limits.get(
                "tool_timeout_seconds", self.policy.get("run_timeout_seconds", "—")
            )
            available, cap = int(budget["available"]), int(budget["session_cap"])
            blocks = max(0, min(20, int(20 * available / cap))) if cap else 0
            self.query_one("#budget", Static).update(
                Text(
                    "SERVICE BUDGET\n\n"
                    + dollars(available)
                    + " available\n"
                    + "━" * blocks
                    + "─" * (20 - blocks)
                    + f"\n\nCap       {dollars(cap)}\nSpent     {dollars(budget['settled'])}"
                    + f"\nHeld      {dollars(budget['held'])}\n\n"
                    + f"Per call  {dollars(budget.get('per_call_cap', cap))}\n"
                    + f"Tools     {len(self.report.get('tool_results', []))} / {max_calls}\n"
                    + f"Timeout   {timeout}s\n"
                    + f"Runway    {self.report.get('runway', {}).get('state', 'UNKNOWN')}"
                )
            )
            self.query_one("#compact-budget", Static).update(
                f"{dollars(available)} available · {dollars(budget['settled'])} spent · {self.mode}"
            )
            self.closed_session = self.report["status"] in ("COMPLETED", "STOPPED")
            if self.closed_session and not self.busy:
                self.status("■ Session finished · /app to review, Ctrl+Q to quit")
        except (ValueError, OSError):
            self.query_one("#compact-budget", Static).update(
                "Budget offline · reconnect governor-web"
            )
            self.query_one("#budget", Static).update(
                "BUDGET OFFLINE\n\nReconnect governor-web.\nExisting spend and "
                "holds\nremain in the ledger."
            )
        finally:
            self.polling = False

    @work
    async def finish(self):
        if not self.created or not self.last_answer:
            self.notify("Complete a Codex turn before finishing the session.")
            return
        self.busy = True
        try:
            await asyncio.to_thread(
                self.client.request,
                "/api/plugin/finish",
                {
                    "session_id": self.sid,
                    "answer": self.last_answer[:20000],
                    "status": "COMPLETED" if self.last_turn_status == "completed" else "STOPPED",
                },
            )
            self.closed_session = True
            await self.add("Final answer saved to " + self.client.link(self.sid))
            self.status("■ Session finished · Ctrl+Q to quit")
        except (ValueError, OSError) as exc:
            await self.add(str(exc))
        finally:
            self.busy = False

    @work
    async def action_stop(self):
        if self.transport and self.turn_id:
            try:
                await self.transport.request(
                    "turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id}
                )
                self.status("◌ Stopping Codex · payment holds are preserved")
            except (ValueError, OSError, TimeoutError) as exc:
                await self.add(str(exc))
        elif self.busy:
            self.notify("Codex is connecting. Ctrl+Q closes the terminal.")

    def action_focus_prompt(self):
        self.query_one(Prompt).focus()

    def action_dashboard(self):
        webbrowser.open(self.client.link(self.sid) if self.created else self.client.origin)

    @work
    async def action_leave(self):
        if self.exiting:
            return
        self.exiting = True
        if self.transport:
            if self.turn_id:
                try:
                    await asyncio.wait_for(
                        self.transport.request(
                            "turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id}
                        ),
                        3,
                    )
                except (ValueError, OSError, TimeoutError):
                    pass
            await self.transport.close()
        self.exit()

    async def on_unmount(self):
        if self.transport:
            await self.transport.close()


def run_terminal(args, executable, task):
    app = GovernorTerminal(args, executable, task)
    app.run()
    if app.created:
        print(f"Governor session: {app.client.link(app.sid)}")
        if not app.closed_session:
            print(f"Resume: governor-codex --session {app.sid} --cwd {str(args.cwd.resolve())!r}")
    return 0
