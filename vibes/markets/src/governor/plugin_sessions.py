"""Codex owns reasoning; Governor supplies bounded tools and durable session evidence."""

import asyncio
import hashlib
import json
import re

from governor.audit import write_audit
from governor.codex_tools import CodexTools
from governor.config import BudgetPolicy, atomic
from governor.ledger import LedgerError
from governor.mock import MockPaymentAdapter
from governor.payments import PaymentGate
from governor.runway import validate_plan
from governor.vendor.server import vendor_public


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise ValueError("Invalid identifier")
    return value


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def source(report):
    return next((e["data"] for e in report["events"] if e["kind"] == "CLIENT_REQUEST"), {})


class PluginSessions:
    def __init__(self, app):
        self.app, self.ledger, self.settings = app, app.ledger, app.settings

    def create(self, payload):
        if not isinstance(payload, dict) or set(payload) - {
            "session_id",
            "task",
            "mode",
            "task_list",
            "limits",
        }:
            raise ValueError("Expected session_id, task, mode and optional task_list")
        sid = identifier(payload.get("session_id"))
        if not sid.startswith("codex-"):
            raise ValueError("Codex session IDs start with codex-")
        task, mode = payload.get("task"), payload.get("mode", "mock")
        if not isinstance(task, str) or not 1 <= len(task.strip()) <= 20000:
            raise ValueError("Task must contain 1–20000 characters")
        if mode not in ("mock", "solana-devnet"):
            raise ValueError("Invalid payment mode")
        plan = validate_plan(payload.get("task_list"))
        metadata = {"source": "codex-cli", "runner": "codex", "request_hash": fingerprint(payload)}
        limits = self.validate_limits(payload.get("limits"))
        if limits is not None:
            metadata["limits"] = limits
        with self.app.lock:
            if any(s["session_id"] == sid for s in self.ledger.sessions()):
                if source(self.ledger.report(sid)) != metadata:
                    raise LedgerError("Session already belongs to a different request")
                return sid
            if mode == "solana-devnet":
                vendor_public(self.settings.data_dir)
            self.ledger.start(
                sid,
                task.strip(),
                plan=plan,
                payment_mode=mode,
                client_request=metadata,
                budget_policy=BudgetPolicy(
                    session_cap=int(limits["session_cap"]),
                    per_call_cap=int(limits["per_call_cap"]),
                )
                if limits
                else None,
            )
        return sid

    def validate_limits(self, requested):
        if requested is None:
            return None
        ceilings = {
            "session_cap": self.settings.policy.session_cap,
            "per_call_cap": self.settings.policy.per_call_cap,
            "max_tool_calls": self.settings.max_tool_calls,
            "tool_timeout_seconds": self.settings.run_timeout_seconds,
        }
        if not isinstance(requested, dict) or set(requested) - ceilings.keys():
            raise ValueError("Invalid session limits")
        result = {}
        for key, ceiling in ceilings.items():
            value = requested.get(key, str(ceiling) if key.endswith("cap") else ceiling)
            number = atomic(value) if key.endswith("cap") else value
            if type(number) is not int or not 1 <= number <= ceiling:
                raise ValueError("Session limits must be positive and within operator ceilings")
            result[key] = str(number) if key.endswith("cap") else number
        if int(result["per_call_cap"]) > int(result["session_cap"]):
            raise ValueError("Per-call cap cannot exceed the session cap")
        return result

    def owned(self, sid):
        report = self.ledger.report(identifier(sid))
        if source(report).get("runner") != "codex":
            raise LedgerError("Only Codex-owned sessions accept plugin tool calls")
        return report

    def registry(self, sid, mode):
        adapter = MockPaymentAdapter()
        if mode == "solana-devnet":
            from governor.live_payments import DevnetPaymentAdapter

            adapter = DevnetPaymentAdapter(self.settings.data_dir, self.ledger, sid)
        return CodexTools(PaymentGate(self.ledger, sid, adapter))

    def call(self, payload):
        if not isinstance(payload, dict) or set(payload) != {
            "session_id",
            "call_id",
            "tool",
            "arguments",
        }:
            raise ValueError("Expected session_id, call_id, tool, arguments")
        sid, call_id = identifier(payload["session_id"]), identifier(payload["call_id"])
        if not isinstance(payload["tool"], str) or not isinstance(payload["arguments"], dict):
            raise ValueError("Invalid tool call")
        digest = fingerprint(payload)
        with self.app.lock:
            report = self.owned(sid)
            requests = [e["data"] for e in report["events"] if e["kind"] == "CODEX_TOOL_REQUEST"]
            previous = next((r for r in requests if r["call_id"] == call_id), None)
            if previous:
                if previous["request_hash"] != digest:
                    raise LedgerError("Call ID already belongs to different arguments")
                result = next(
                    (
                        e["data"]["result"]
                        for e in report["events"]
                        if e["kind"] == "CODEX_TOOL_RESULT" and e["data"]["call_id"] == call_id
                    ),
                    None,
                )
                return result or {"ok": False, "code": "CALL_PENDING", "call_id": call_id}
            if any(e["kind"] in ("RUN_FINISHED", "CODEX_FINISHED") for e in report["events"]):
                raise LedgerError("Session is finished")
            if self.app.active:
                raise LedgerError("Another tool or agent is running; wait and reuse this call ID")
            limits = source(report).get("limits", {})
            max_calls = min(self.settings.max_tool_calls, limits.get("max_tool_calls", 100))
            timeout = min(
                self.settings.run_timeout_seconds, limits.get("tool_timeout_seconds", 3600)
            )
            if len(requests) >= max_calls:
                self.ledger.record(sid, "CODEX_CALL_REFUSED", {"code": "TOOL_CALL_LIMIT"})
                return {"ok": False, "code": "TOOL_CALL_LIMIT"}
            registry = self.registry(sid, report["budget"]["payment_mode"])
            if payload["tool"] not in registry.schemas:
                raise ValueError("Tool is not available to Codex")
            # A durable request precedes execution. A lost response never repeats the tool.
            self.ledger.record(
                sid,
                "CODEX_TOOL_REQUEST",
                {
                    "call_id": call_id,
                    "name": payload["tool"],
                    "request_hash": digest,
                },
            )
            self.app.active = sid

        async def execute():
            async with asyncio.timeout(timeout):
                return await registry.execute(payload["tool"], payload["arguments"])

        try:
            try:
                result = asyncio.run(execute())
            except Exception:
                result = {"ok": False, "code": "TOOL_ERROR", "holds_preserved": True}
            self.ledger.record(
                sid,
                "CODEX_TOOL_RESULT",
                {"call_id": call_id, "name": payload["tool"], "result": result},
            )
            return result
        finally:
            with self.app.lock:
                self.app.active = None

    def message(self, payload):
        """Mirror visible conversation messages without consuming a tool call or changing caps."""
        if not isinstance(payload, dict) or set(payload) != {
            "session_id",
            "message_id",
            "role",
            "text",
        }:
            raise ValueError("Expected session_id, message_id, role, text")
        sid = identifier(payload["session_id"])
        identifier(payload["message_id"])
        if payload["role"] not in ("user", "assistant") or not isinstance(payload["text"], str):
            raise ValueError("Invalid conversation message")
        if not 1 <= len(payload["text"]) <= 20000:
            raise ValueError("Message must contain 1–20000 characters")
        with self.app.lock:
            report = self.owned(sid)
            messages = [e["data"] for e in report["events"] if e["kind"] == "CODEX_MESSAGE"]
            previous = next((m for m in messages if m["message_id"] == payload["message_id"]), None)
            if previous:
                if previous != payload:
                    raise LedgerError("Message ID already belongs to different content")
                return previous
            if (
                len(messages) >= 500
                or sum(len(m["text"].encode()) for m in messages) + len(payload["text"].encode())
                > 2_000_000
            ):
                raise LedgerError("Conversation message limit reached")
            self.ledger.record(sid, "CODEX_MESSAGE", payload)
        return payload

    def finish(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"session_id", "answer", "status"}:
            raise ValueError("Expected session_id, answer, status")
        sid = identifier(payload["session_id"])
        if (
            payload["status"] not in ("COMPLETED", "STOPPED")
            or not isinstance(payload["answer"], str)
            or not 1 <= len(payload["answer"]) <= 20000
        ):
            raise ValueError("Invalid final result")
        with self.app.lock:
            report = self.owned(sid)
            result = {
                "session_id": sid,
                "status": payload["status"],
                "answer": payload["answer"],
                "model_mode": "codex",
                "payment_mode": report["budget"]["payment_mode"],
            }
            old = next((e["data"] for e in report["events"] if e["kind"] == "CODEX_FINISHED"), None)
            if old:
                if old != result:
                    raise LedgerError("Final result already recorded")
                return old
            if self.app.active == sid:
                raise LedgerError("Wait for the active tool before finishing")
            # Final result is stored in SQLite as well as the downloadable audit.
            self.ledger.record(sid, "CODEX_FINISHED", result)
            self.ledger.record(sid, "RUN_FINISHED", {"status": payload["status"], "agent": "codex"})
            write_audit(self.settings.data_dir, sid, {**self.ledger.report(sid), "result": result})
            return result

    @staticmethod
    def decorate(report, active):
        report["client"] = source(report)
        if report["client"].get("runner") != "codex":
            return
        events = report["events"]
        report["conversation"] = [e["data"] for e in events if e["kind"] == "CODEX_MESSAGE"]
        report["tool_results"] = [e["data"] for e in events if e["kind"] == "CODEX_TOOL_RESULT"]
        final = next((e["data"] for e in events if e["kind"] == "CODEX_FINISHED"), None)
        pending = {e["data"]["call_id"] for e in events if e["kind"] == "CODEX_TOOL_REQUEST"} - {
            r["call_id"] for r in report["tool_results"]
        }
        report["status"] = (
            "RUNNING"
            if active
            else final["status"]
            if final
            else "INTERRUPTED"
            if pending
            else "WAITING"
        )
        if final:
            report["result"] = final
