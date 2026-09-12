"""Launch the installed Codex CLI with native tools bound to a Governor app session."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from governor.app_client import AppClient, AppError
from governor.plugin_sessions import identifier


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("task", nargs="?")
    root.add_argument("--task-file", type=Path)
    root.add_argument("--task-list", type=Path)
    root.add_argument(
        "--exec",
        action="store_true",
        dest="headless",
        help="run one task without the interactive TUI",
    )
    root.add_argument("--native", action="store_true", help="use the original Codex terminal UI")
    root.add_argument("--session", help="reuse an existing open Codex-owned Governor budget")
    root.add_argument("--mode", choices=("mock", "solana-devnet"), default=None)
    root.add_argument("--url", default=os.environ.get("GOVERNOR_APP_URL", "http://127.0.0.1:8787"))
    root.add_argument("--cwd", type=Path, default=Path.cwd())
    root.add_argument(
        "--model", help="forward a model choice to Codex; otherwise use its configuration"
    )
    root.add_argument("--profile")
    root.add_argument(
        "--tool-approval",
        choices=("approve", "auto", "prompt", "writes"),
        default="approve",
        help="approval policy for this session-bound Governor connection; ledger caps always apply",
    )
    root.add_argument("--sandbox", choices=("read-only", "workspace-write", "danger-full-access"))
    root.add_argument(
        "--json", action="store_true", help="forward Codex JSONL output in --exec mode"
    )
    return root


def mcp_configuration(args, client, sid):
    mcp_args = ["-m", "governor.codex_mcp", "--url", client.origin, "--session", sid]
    return (
        "mcp_servers.governor={command="
        + json.dumps(sys.executable)
        + ",args="
        + json.dumps(mcp_args)
        + ",required=true,enabled=true,startup_timeout_sec=20,tool_timeout_sec=130"
        + ",default_tools_approval_mode="
        + json.dumps(args.tool_approval)
        + "}"
    )


def command(args, executable, client, sid, task, mode, output=None):
    configuration = mcp_configuration(args, client, sid)
    cmd = [executable]
    if args.headless:
        cmd.append("exec")
    cmd.extend(["-c", configuration, "--cd", str(args.cwd.resolve())])
    for flag in ("model", "profile", "sandbox"):
        if value := getattr(args, flag):
            cmd.extend(["--" + flag, value])
    if args.headless:
        cmd.extend(["--output-last-message", str(output)])
        if args.json:
            cmd.append("--json")
    else:
        cmd.append("--no-alt-screen")
    prompt = (
        f"You are running inside Governor. You remain the reasoning agent; do not invoke Gemini. "
        f"Your existing Governor session is {sid}, payment mode {mode}, app {client.link(sid)}. "
        "Native governor MCP tools are preloaded. Start by calling get_budget and list_services, "
        "using stable call IDs. Use these tools for Governor work; do not create another session "
        "via the CLI plugin. Assess Bazaar search results yourself. Respect tool refusals and "
        "uncertain holds; do not reset the budget or send funds outside the payment gate. "
        "get_session provides prior tool results when reconnecting. "
        + (
            "When done, call finish_session with your final answer and COMPLETED or STOPPED. "
            if args.headless
            else "Use finish_session only when the user is done with this Governor session; "
            "otherwise keep it open for follow-up work. "
        )
        + "\n\nUser task:\n"
        + task
    )
    # An argv list, never a shell string. User text is data even with shell metacharacters.
    cmd.extend(["--", prompt])
    return cmd


def launch(args):
    executable = shutil.which("codex")
    if not executable:
        raise AppError("Codex CLI is not on PATH. Install Codex and log in before launching.")
    if not args.cwd.is_dir():
        raise AppError("Working directory does not exist")
    if args.task and args.task_file:
        raise AppError("Use a task argument or --task-file, not both")
    if args.json and not args.headless:
        raise AppError("--json requires --exec")
    if not args.headless and not sys.stdin.isatty():
        raise AppError("Interactive Codex requires a terminal; use --exec for automation")
    task = args.task_file.read_text() if args.task_file else args.task
    if task is not None and not 1 <= len(task.strip()) <= 20000:
        raise AppError("Task must contain 1–20000 characters")
    if args.native and args.headless:
        raise AppError("Use --native or --exec, not both")
    if not args.headless and not args.native:
        from governor.terminal import run_terminal

        return run_terminal(args, executable, task)
    client = AppClient(args.url)
    state = client.request("/api/state")
    if state.get("plugin_api") != 1:
        raise AppError("Restart governor-web with the current Governor version")
    if args.session:
        sid = identifier(args.session)
        report = client.report(sid)
        if report.get("client", {}).get("runner") != "codex":
            raise AppError("Only an existing Codex-owned session can be attached")
        if report["status"] in ("COMPLETED", "STOPPED", "RUNNING"):
            raise AppError("Session is finished or executing a tool; it cannot be attached now")
        mode = report["budget"]["payment_mode"]
        if args.mode is not None and args.mode != mode:
            raise AppError("Cannot change the existing session's payment mode")
        if args.task_list:
            raise AppError("An attached session cannot replace its task plan")
        task = task or report["task"]
    else:
        if args.headless and not task:
            raise AppError("--exec requires a task or --task-file")
        task = task or "Work interactively with Codex using Governor's budget and service tools."
        sid = "codex-" + uuid.uuid4().hex[:16]
        mode = args.mode or "mock"
        payload = {"session_id": sid, "task": task, "mode": mode}
        if args.task_list:
            payload["task_list"] = json.loads(args.task_list.read_text())
        # Print identity before the mutating request, so a lost response is recoverable.
        print(f"Governor session: {sid}\nApp: {client.link(sid)}", file=sys.stderr, flush=True)
        client.request("/api/plugin/runs", payload)
    print(f"Starting Codex · {mode} · {client.link(sid)}", file=sys.stderr, flush=True)
    with tempfile.TemporaryDirectory(prefix="governor-codex-") as folder:
        output = Path(folder) / "final.txt"
        cmd = command(args, executable, client, sid, task, mode, output)
        child = subprocess.Popen(cmd, cwd=args.cwd, stdin=None, stdout=None, stderr=None)
        try:
            code = child.wait()
        except KeyboardInterrupt:
            # The terminal delivers SIGINT to Codex too. Give it time to save its state.
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait()
            print(
                f"Codex interrupted. Budget and holds remain at {client.link(sid)}", file=sys.stderr
            )
            return 130
        if args.headless and code == 0 and output.is_file():
            report = client.report(sid)
            if report["status"] not in ("COMPLETED", "STOPPED"):
                answer = output.read_text().strip()
                if answer:
                    client.request(
                        "/api/plugin/finish",
                        {"session_id": sid, "answer": answer[:20000], "status": "COMPLETED"},
                    )
        print(f"Governor session saved: {client.link(sid)}", file=sys.stderr, flush=True)
        return code if code >= 0 else 128 - code


def main():
    try:
        return launch(parser().parse_args())
    except (ValueError, OSError) as exc:
        print(f"Governor: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
