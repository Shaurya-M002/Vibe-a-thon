#!/usr/bin/env python3
"""Dependency-free CLI for the local Governor app. stdout is JSON or NDJSON."""

import argparse
import http.client
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit


class ClientError(ValueError):
    pass


def session_id(value):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise argparse.ArgumentTypeError("Invalid session ID")
    return value


class Client:
    def __init__(self, origin):
        url = urlsplit(origin)
        if (
            url.scheme != "http"
            or url.hostname not in ("127.0.0.1", "localhost")
            or url.username
            or url.password
            or url.path not in ("", "/")
            or url.query
            or url.fragment
        ):
            raise ClientError("Governor URL must be a loopback HTTP origin")
        self.port = url.port or 8787
        self.host = url.hostname
        self.origin = f"http://{self.host}:{self.port}"

    def request(self, path, payload=None, *, raw=False):
        # Direct HTTPConnection ignores proxy settings and never follows redirects.
        connection = http.client.HTTPConnection(self.host, self.port, timeout=125)
        try:
            connection.request(
                "GET" if payload is None else "POST",
                path,
                body=None if payload is None else json.dumps(payload).encode(),
                headers={"Origin": self.origin, "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = response.read(8_000_001)
            if len(body) > 8_000_000:
                raise ClientError("Response exceeds 8 MB; inspect the session in the app")
            if response.status not in (200, 202):
                raise ClientError(f"Governor HTTP {response.status}: {body.decode()[:400]}")
            return body.decode() if raw else json.loads(body)
        except (OSError, http.client.HTTPException) as exc:
            raise ClientError(
                "Governor unavailable. Start governor-web in the configured runtime directory. "
                "After a write error, reuse the SAME session/call ID and arguments; "
                "never create a replacement budget."
            ) from exc
        finally:
            connection.close()

    def link(self, sid):
        return f"{self.origin}/#sessions/{sid}"


def emit(value):
    print(json.dumps(value), flush=True)


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--url", default=os.environ.get("GOVERNOR_APP_URL", "http://127.0.0.1:8787"))
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("new-id", help="generate a stable ID before submitting a run")
    commands.add_parser("doctor", help="check the running app and policy")
    commands.add_parser("sessions", help="list app sessions")
    start = commands.add_parser("start", help="open a session controlled by Codex")
    start.add_argument("--session", type=session_id, required=True)
    start.add_argument("--mode", choices=("mock", "solana-devnet"), default="mock")
    task = start.add_mutually_exclusive_group()
    task.add_argument("--task")
    task.add_argument("--task-file", type=Path)
    start.add_argument("--task-list", type=Path)
    for command in ("status", "watch", "export", "reconcile", "call", "finish"):
        sub = commands.add_parser(command)
        sub.add_argument("session", type=session_id)
        if command == "call":
            sub.add_argument("--call-id", type=session_id, required=True)
            sub.add_argument(
                "--tool",
                choices=(
                    "get_budget",
                    "get_runway",
                    "list_services",
                    "purchase_service",
                    "summarize_local",
                ),
                required=True,
            )
            arguments = sub.add_mutually_exclusive_group()
            arguments.add_argument("--arguments", default="{}")
            arguments.add_argument("--arguments-file", type=Path)
        elif command == "finish":
            sub.add_argument("--status", choices=("COMPLETED", "STOPPED"), default="COMPLETED")
            answer = sub.add_mutually_exclusive_group(required=True)
            answer.add_argument("--answer")
            answer.add_argument("--answer-file", type=Path)
        elif command == "watch":
            sub.add_argument(
                "--seconds", type=int, default=30, choices=range(1, 61), metavar="1..60"
            )
            sub.add_argument(
                "--after", type=int, default=0, help="last event sequence already seen"
            )
        elif command == "export":
            sub.add_argument("--format", choices=("json", "csv"), default="json")
        elif command == "reconcile":
            sub.add_argument("attempt_id")
    return root


def run(args):
    if args.command == "new-id":
        emit({"session_id": "codex-" + uuid.uuid4().hex[:16]})
        return 0
    client = Client(args.url)
    if args.command in ("doctor", "sessions"):
        state = client.request("/api/state")
        emit(
            {
                "app_url": client.origin,
                **(
                    {"sessions": state["sessions"]}
                    if args.command == "sessions"
                    else {
                        "ready": True,
                        "plugin_api": state.get("plugin_api"),
                        "model": state["model"],
                        "policy": state["policy"],
                        "active_session": state["active_session"],
                        "vendor": state["vendor"],
                    }
                ),
            }
        )
        return 0
    sid = args.session
    if args.command == "start":
        if not sid.startswith("codex-"):
            raise ClientError("Use new-id or a session ID beginning with codex-")
        payload = {"session_id": sid, "mode": args.mode}
        task = args.task_file.read_text() if args.task_file else args.task
        if not task or not 1 <= len(task.strip()) <= 20000:
            raise ClientError("Provide --task or --task-file (1–20000 characters)")
        payload["task"] = task
        if args.task_list:
            payload["task_list"] = json.loads(args.task_list.read_text())
        result = client.request("/api/plugin/runs", payload)
        emit({**result, "app_url": client.link(sid)})
        return 0
    path = "/api/sessions/" + sid
    if args.command == "call":
        arguments = json.loads(
            args.arguments_file.read_text() if args.arguments_file else args.arguments
        )
        emit(
            client.request(
                "/api/plugin/calls",
                {
                    "session_id": sid,
                    "call_id": args.call_id,
                    "tool": args.tool,
                    "arguments": arguments,
                },
            )
        )
        return 0
    if args.command == "finish":
        answer = args.answer_file.read_text() if args.answer_file else args.answer
        emit(
            client.request(
                "/api/plugin/finish", {"session_id": sid, "answer": answer, "status": args.status}
            )
        )
        return 0
    if args.command == "export":
        print(client.request(path + "?format=" + args.format, raw=True), end="")
        return 0
    if args.command == "reconcile":
        if not re.fullmatch(r"[a-f0-9]{64}", args.attempt_id):
            raise ClientError("Invalid payment attempt ID")
        emit(client.request("/api/reconcile", {"session_id": sid, "attempt_id": args.attempt_id}))
        return 0
    if args.command == "status":
        emit({**client.request(path), "app_url": client.link(sid)})
        return 0
    deadline = time.monotonic() + args.seconds
    after = args.after
    while True:
        report = client.request(path)
        for event in report["events"]:
            if event["seq"] > after:
                emit({"type": "event", "session_id": sid, **event})
                after = event["seq"]
        if report["status"] != "RUNNING" or time.monotonic() >= deadline:
            emit(
                {
                    "type": "snapshot",
                    "session_id": sid,
                    "status": report["status"],
                    "budget": report["budget"],
                    "result": report["result"],
                    "last_seq": after,
                    "app_url": client.link(sid),
                }
            )
            return 0
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def main():
    try:
        return run(parser().parse_args())
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('{"error":"Watcher stopped; the app session continues."}', file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
