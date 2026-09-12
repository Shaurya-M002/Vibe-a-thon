"""User-operated terminal settings. Budget fields freeze when creation is attempted."""

import re
from decimal import Decimal

from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static, Switch, TabbedContent, TabPane


def usdc_atomic(value):
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?", value):
        raise ValueError("Use a positive USDC amount with at most six decimal places.")
    result = int(Decimal(value) * 1_000_000)
    if not 0 < result < 2**63:
        raise ValueError("USDC amount is outside the supported range.")
    return str(result)


class TerminalSettings(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    TerminalSettings { align: center middle; background: #080808 80%; }
    #settings-dialog { width: 86; max-width: 95%; height: 38; max-height: 94%;
        background: #171614; border: round #ff994f; padding: 0 2; }
    #settings-title { color: #ffffff; text-style: bold; height: 1; }
    #settings-hint { color: #a8a39a; height: auto; margin: 0 0 1 0; }
    #settings-tabs { height: 1fr; }
    TabPane { padding: 0; }
    .settings-scroll { height: 1fr; padding: 0 1; scrollbar-color: #815337; }
    .field-label { height: auto; color: #efeee9; margin-top: 1; }
    .field-hint { height: auto; color: #a8a39a; }
    #settings-dialog Input, #settings-dialog Select { margin: 0; }
    #settings-error { height: auto; color: #ff994f; }
    #settings-actions { height: 3; margin-top: 1; align-horizontal: right; }
    #settings-actions Button { margin-left: 1; }
    #settings-dialog Input:focus { border: tall #ff994f; }
    #settings-dialog Switch { margin-top: 1; }
    """

    def __init__(self, values, policy, *, locked=False):
        super().__init__()
        self.values, self.policy, self.locked = values, policy, locked

    def compose(self):
        v = self.values
        with Vertical(id="settings-dialog"):
            yield Static("SETTINGS  /  Make this session yours", id="settings-title")
            yield Static(
                "Model changes apply to your next prompt. Display changes apply now.",
                id="settings-hint",
            )
            with TabbedContent(id="settings-tabs"):
                with TabPane("Codex", id="tab-codex"):
                    with VerticalScroll(classes="settings-scroll"):
                        yield Static("Model ID", classes="field-label")
                        yield Input(
                            v["model"],
                            placeholder="Keep Codex's current model",
                            id="setting-model",
                            max_length=120,
                        )
                        yield Static(
                            "Use a model available to your Codex account.", classes="field-hint"
                        )
                        yield Static("Reasoning effort", classes="field-label")
                        yield Input(
                            v["effort"],
                            placeholder="Keep current effort · e.g. low, medium, high",
                            id="setting-effort",
                            max_length=32,
                        )
                        yield Static(
                            "Accepted effort levels depend on your selected model.",
                            classes="field-hint",
                        )
                        yield Static("Governor tool approvals", classes="field-label")
                        yield Select(
                            [(x, x) for x in ("approve", "auto", "prompt", "writes")],
                            value=v["tool_approval"],
                            allow_blank=False,
                            id="setting-approval",
                            disabled=self.locked,
                        )
                        yield Static(
                            "Approval selection is fixed once the session starts. "
                            "Ledger caps always apply.",
                            classes="field-hint",
                        )
                with TabPane("Budget", id="tab-budget"):
                    with VerticalScroll(classes="settings-scroll"):
                        yield Static(
                            "Limits are locked to this session."
                            if self.locked
                            else "Choose limits before your first prompt. "
                            "These stay within the app operator's ceilings.",
                            classes="field-hint",
                        )
                        yield Static("Payment mode", classes="field-label")
                        yield Select(
                            [
                                ("Mock · simulated payments", "mock"),
                                ("Solana Devnet · wallet transfers", "solana-devnet"),
                            ],
                            value=v["mode"],
                            allow_blank=False,
                            id="setting-mode",
                            disabled=self.locked,
                        )
                        for name, label in (
                            ("session_cap", "Session budget · USDC"),
                            ("per_call_cap", "Per-call cap · USDC"),
                            ("max_tool_calls", "Maximum Governor tool calls"),
                            ("tool_timeout_seconds", "Tool timeout · seconds"),
                        ):
                            yield Static(label, classes="field-label")
                            value = v["limits"].get(name)
                            if value is None:
                                value = self.policy.get(
                                    "run_timeout_seconds"
                                    if name == "tool_timeout_seconds"
                                    else name,
                                    "",
                                )
                            display = (
                                str(Decimal(str(value)) / 1_000_000)
                                if name.endswith("cap") and value != ""
                                else str(value)
                            )
                            yield Input(
                                display,
                                id="setting-" + name,
                                disabled=self.locked or not self.policy,
                                max_length=24,
                            )
                        yield Static(
                            Text("Operator ceilings: " + self.ceiling_text()), classes="field-hint"
                        )
                with TabPane("Display", id="tab-display"):
                    with VerticalScroll(classes="settings-scroll"):
                        yield Static("Conversation spacing", classes="field-label")
                        yield Select(
                            [("Comfortable", "comfortable"), ("Compact", "dense")],
                            value=v["density"],
                            allow_blank=False,
                            id="setting-density",
                        )
                        yield Static("Budget sidebar", classes="field-label")
                        yield Switch(v["sidebar"], id="setting-sidebar")
                        yield Static("Expand tool details by default", classes="field-label")
                        yield Switch(v["expand_tools"], id="setting-tools")
                        yield Static("Budget refresh interval", classes="field-label")
                        yield Select(
                            [
                                ("1 second", 1.0),
                                ("1.5 seconds", 1.5),
                                ("3 seconds", 3.0),
                                ("5 seconds", 5.0),
                            ],
                            value=v["refresh"],
                            allow_blank=False,
                            id="setting-refresh",
                        )
            yield Static("", id="settings-error")
            with Horizontal(id="settings-actions"):
                yield Button("Cancel", id="settings-cancel")
                yield Button("Apply settings", variant="warning", id="settings-apply")

    def ceiling_text(self):
        if not self.policy:
            return "App unavailable. Connect governor-web to adjust budget limits."
        return (
            f"${Decimal(self.policy['session_cap']) / 1_000_000} total · "
            f"${Decimal(self.policy['per_call_cap']) / 1_000_000} per call · "
            f"{self.policy['max_tool_calls']} calls"
        )

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "settings-cancel":
            self.action_cancel()
        elif event.button.id == "settings-apply":
            try:
                result = dict(self.values)
                result["model"] = self.query_one("#setting-model", Input).value.strip()
                result["effort"] = self.query_one("#setting-effort", Input).value.strip()
                if any(ord(c) < 32 for c in result["model"] + result["effort"]):
                    raise ValueError("Model and effort must be single-line values.")
                for key in ("density", "refresh"):
                    result[key] = self.query_one("#setting-" + key, Select).value
                result["sidebar"] = self.query_one("#setting-sidebar", Switch).value
                result["expand_tools"] = self.query_one("#setting-tools", Switch).value
                if not self.locked:
                    result["mode"] = self.query_one("#setting-mode", Select).value
                    result["tool_approval"] = self.query_one("#setting-approval", Select).value
                    if self.policy:
                        limits = {}
                        for key in (
                            "session_cap",
                            "per_call_cap",
                            "max_tool_calls",
                            "tool_timeout_seconds",
                        ):
                            value = self.query_one("#setting-" + key, Input).value.strip()
                            amount = usdc_atomic(value) if key.endswith("cap") else int(value)
                            ceiling = self.policy[
                                "run_timeout_seconds" if key == "tool_timeout_seconds" else key
                            ]
                            if not 1 <= int(amount) <= int(ceiling):
                                raise ValueError(
                                    "Selected limits must be positive and within operator ceilings."
                                )
                            limits[key] = amount
                        if int(limits["per_call_cap"]) > int(limits["session_cap"]):
                            raise ValueError("Per-call cap cannot exceed the session budget.")
                        result["limits"] = limits
                self.dismiss(result)
            except (ValueError, ArithmeticError) as exc:
                self.query_one("#settings-error", Static).update(Text(str(exc)))
