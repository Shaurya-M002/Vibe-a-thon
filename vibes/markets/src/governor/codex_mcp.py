"""Small MCP stdio bridge, permanently bound to one existing Governor session."""

import argparse
import json
import sys

from governor.app_client import AppClient
from governor.codex_tools import CodexTools
from governor.plugin_sessions import identifier

PROTOCOL = "2024-11-05"
CALL_ID = {"type": "string", "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$"}


class Bridge:
    def __init__(self, client, sid):
        self.client, self.sid = client, identifier(sid)
        self.schemas = CodexTools(None).schemas
        self.initialized = False

    def tools(self):
        definitions = []
        for name, (model, description) in self.schemas.items():
            schema = model.model_json_schema()
            schema["properties"]["call_id"] = CALL_ID
            schema["required"] = [*schema.get("required", []), "call_id"]
            definitions.append(
                {
                    "name": name,
                    "description": description
                    + " Use a stable call_id; reuse identical arguments after a lost response.",
                    "inputSchema": schema,
                    "annotations": {
                        "readOnlyHint": name
                        in ("get_budget", "get_runway", "list_services", "get_vendor_search"),
                        "destructiveHint": name == "purchase_service",
                        "idempotentHint": True,
                        "openWorldHint": name in ("purchase_service", "discover_vendors"),
                    },
                }
            )
        for name, description, properties, required in [
            (
                "get_session",
                "Read this session's app link, audit, tool results and receipts.",
                {},
                [],
            ),
            (
                "reconcile_receipt",
                "Read existing Devnet receipt evidence. Never sign or resend a payment.",
                {"attempt_id": {"type": "string", "pattern": "^[a-f0-9]{64}$"}},
                ["attempt_id"],
            ),
            (
                "finish_session",
                "Record the final answer and close this session to new calls. "
                "Use only when finished or deliberately stopped.",
                {
                    "answer": {"type": "string", "minLength": 1, "maxLength": 20000},
                    "status": {"type": "string", "enum": ["COMPLETED", "STOPPED"]},
                },
                ["answer", "status"],
            ),
        ]:
            definitions.append(
                {
                    "name": name,
                    "description": description,
                    "inputSchema": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                }
            )
        return definitions

    def call(self, name, arguments):
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        if name in self.schemas:
            args = dict(arguments)
            cid = identifier(args.pop("call_id", None))
            # Local schema validation keeps session IDs, payees and arbitrary fields out.
            self.schemas[name][0].model_validate(args)
            return self.client.request(
                "/api/plugin/calls",
                {"session_id": self.sid, "call_id": cid, "tool": name, "arguments": args},
            )
        if name == "get_session" and not arguments:
            return {**self.client.report(self.sid), "app_url": self.client.link(self.sid)}
        if name == "reconcile_receipt" and set(arguments) == {"attempt_id"}:
            import re

            if not isinstance(arguments["attempt_id"], str) or not re.fullmatch(
                r"[a-f0-9]{64}", arguments["attempt_id"]
            ):
                raise ValueError("Invalid receipt ID")
            return self.client.request("/api/reconcile", {"session_id": self.sid, **arguments})
        if name == "finish_session" and set(arguments) == {"answer", "status"}:
            return self.client.request("/api/plugin/finish", {"session_id": self.sid, **arguments})
        raise ValueError("Unknown tool or invalid arguments")

    def dispatch(self, message):
        mid = message.get("id") if isinstance(message, dict) else None

        def error(code, text):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}

        if (
            not isinstance(message, dict)
            or message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("method"), str)
        ):
            return error(-32600, "Invalid JSON-RPC request")
        if "id" not in message:
            return None  # Lifecycle/cancellation notifications never produce responses.
        method, params = message["method"], message.get("params", {})
        if not isinstance(params, dict):
            return error(-32602, "Invalid params")
        if method == "initialize":
            try:
                report = self.client.report(self.sid)
                if report.get("client", {}).get("runner") != "codex":
                    raise ValueError("Session is not owned by Codex")
            except (ValueError, OSError):
                return error(-32603, "Governor session unavailable; bridge not initialized")
            self.initialized = True
            result = {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "governor", "version": "0.1.0"},
                "instructions": (
                    f"Tools are bound to Governor session {self.sid}. Codex is the agent. "
                    "Use these native tools, not the CLI plugin new-session workflow. "
                    "Budget is enforced only on Governor purchases. "
                    "Never create a replacement budget after refusal. "
                    f"Inspect get_session after uncertainty. App: {self.client.link(self.sid)}"
                ),
            }
        elif method == "ping":
            result = {}
        elif not self.initialized:
            return error(-32002, "Initialize first")
        elif method == "tools/list":
            result = {"tools": self.tools()}
        elif method == "tools/call":
            try:
                data = self.call(params.get("name"), params.get("arguments", {}))
                result = {
                    "content": [{"type": "text", "text": json.dumps(data)}],
                    "isError": data.get("ok") is False,
                }
            except (ValueError, OSError, TypeError):
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Governor tool failed or arguments were invalid. "
                                "Inspect get_session; "
                                "retry only with the same call_id and arguments. "
                                "Existing payment holds remain."
                            ),
                        }
                    ],
                    "isError": True,
                }
        else:
            return error(-32601, "Method not found")
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(bridge, incoming, outgoing):
    while line := incoming.readline(262145):
        if len(line) > 262144:
            return 2
        try:
            response = bridge.dispatch(json.loads(line))
        except (ValueError, UnicodeError):
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Invalid JSON"},
            }
        if response is not None:
            outgoing.write(json.dumps(response) + "\n")
            outgoing.flush()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--session", required=True)
    args = parser.parse_args()
    try:
        return serve(Bridge(AppClient(args.url), args.session), sys.stdin, sys.stdout)
    except (ValueError, OSError):
        print("Governor MCP bridge unavailable.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
