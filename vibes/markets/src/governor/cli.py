"""Local CLI. Gemini inference is live; all payment activity is explicitly mocked."""

import argparse
import asyncio
import csv
import io
import json
import re
import sqlite3
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

from governor.agent import Agent
from governor.audit import write_audit
from governor.config import ConfigurationError, Settings
from governor.demo import RUNWAY_PLAN, RUNWAY_TASK, TASK, DemoModel, RunwayDemoModel
from governor.discovery import VendorScout
from governor.gemini import GeminiModel
from governor.ledger import Ledger, LedgerError
from governor.mock import MockPaymentAdapter
from governor.payments import PaymentGate
from governor.runway import validate_plan
from governor.tools import ToolRegistry


def session_name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise argparse.ArgumentTypeError(
            "session must be 1–64 letters, digits, hyphens, or underscores"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Governor: Gemini agent with simulated spending caps"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a task with Gemini and simulated payment tools")
    run.add_argument("task")
    run.add_argument("--session", type=session_name)
    run.add_argument(
        "--discover-vendors",
        action="store_true",
        help="run a read-only Bazaar scout alongside the main agent",
    )
    run.add_argument(
        "--task-list", type=Path, help="caller-ordered JSON task list for budget runway"
    )
    resume = commands.add_parser("resume", help="resume the original task with its existing budget")
    resume.add_argument("session", type=session_name)
    demo = commands.add_parser(
        "demo", help="run the deterministic offline scenario; no API key needed"
    )
    demo.add_argument("--session", type=session_name)
    demo.add_argument(
        "--resume", action="store_true", help="reuse this demo session and its ledger"
    )
    runway_demo = commands.add_parser(
        "runway-demo", help="demonstrate early runway warning and approved local fallback"
    )
    runway_demo.add_argument("--session", type=session_name)
    report = commands.add_parser("report", help="read the audit or export settled expenses")
    report.add_argument("session", type=session_name)
    report.add_argument("--format", choices=["json", "csv"], default="json")
    for command in (run, resume, demo, runway_demo, report):
        command.add_argument("--data-dir", type=Path, help="override GOVERNOR_DATA_DIR")
    return parser


def expense_csv(report: dict) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "session_id",
            "attempt_id",
            "service",
            "amount_atomic_usdc",
            "receipt",
            "payment_mode",
        ],
    )
    writer.writeheader()
    for attempt in report["attempts"]:
        if attempt["status"] == "SETTLED":
            writer.writerow(
                {
                    "session_id": report["session_id"],
                    "attempt_id": attempt["attempt_id"],
                    "service": attempt["service"],
                    "amount_atomic_usdc": attempt["amount"],
                    "receipt": attempt["result"]["receipt"],
                    "payment_mode": report["budget"]["payment_mode"],
                }
            )
    return output.getvalue()


async def execute(args, settings: Settings) -> int:
    is_runway_demo = args.command == "runway-demo"
    is_demo = args.command in ("demo", "runway-demo")
    should_resume = args.command == "resume" or (is_demo and getattr(args, "resume", False))
    if should_resume and not args.session:
        raise ValueError("resuming a demo requires --session")
    path = settings.data_dir / "ledger.sqlite3"
    if (should_resume or args.command == "report") and not path.is_file():
        raise LedgerError("ledger does not exist; refusing to create a replacement budget")
    model = None
    plan = RUNWAY_PLAN if is_runway_demo else None
    if getattr(args, "task_list", None):
        plan = validate_plan(json.loads(args.task_list.read_text()))
    try:
        if args.command != "report" and not is_demo:
            model = GeminiModel(settings)
        ledger = Ledger(path, settings.policy)
        if args.command == "report":
            report = ledger.report(args.session)
            print(expense_csv(report) if args.format == "csv" else json.dumps(report, indent=2))
            return 0
        session_id = args.session or uuid.uuid4().hex[:12]
        task = (
            (RUNWAY_TASK if is_runway_demo else TASK)
            if is_demo
            else (ledger.task(session_id) if should_resume else args.task.strip())
        )
        if not task or len(task) > 20000:
            raise ValueError("task must contain 1–20000 characters")
        payment_mode = "mock" if is_demo else settings.payment_mode
        ledger.start(session_id, task, resume=should_resume, plan=plan, payment_mode=payment_mode)
        adapter = MockPaymentAdapter()
        if payment_mode == "solana-devnet":
            from governor.live_payments import DevnetPaymentAdapter

            adapter = DevnetPaymentAdapter(settings.data_dir, ledger, session_id)
        scout = None if is_demo else VendorScout(model, ledger, session_id, settings)
        tools = ToolRegistry(
            PaymentGate(ledger, session_id, adapter),
            scout=scout,
            auto_discover=getattr(args, "discover_vendors", False),
        )
        if is_demo:
            model = RunwayDemoModel() if is_runway_demo else DemoModel()
            settings = settings.model_copy(update={"model": "offline-scripted-demo"})
        try:
            result = await Agent(model, tools, settings).run(task)
        finally:
            # A cancelled or failed run still leaves a recoverable ledger and audit.
            audit_path = write_audit(settings.data_dir, session_id, ledger.report(session_id))
        print(
            json.dumps(
                {
                    **result.to_dict(),
                    "audit_path": str(audit_path),
                    "model_mode": "scripted" if is_demo else "gemini",
                    "payment_mode": payment_mode,
                    "simulated_authorizations_this_run": adapter.authorization_count
                    if payment_mode == "mock"
                    else 0,
                    "devnet_authorizations_this_run": adapter.authorization_count
                    if payment_mode == "solana-devnet"
                    else 0,
                },
                indent=2,
            )
        )
        return 0 if result.status == "COMPLETED" else 1
    finally:
        if isinstance(model, GeminiModel):
            await model.close()


def main() -> int:
    args = build_parser().parse_args()
    # Only load the .env in the working directory, never a parent project's secrets.
    load_dotenv(Path.cwd() / ".env", override=False)
    try:
        settings = Settings.from_env()
        if args.data_dir is not None:
            settings = settings.model_copy(update={"data_dir": args.data_dir})
        return asyncio.run(execute(args, settings))
    except KeyboardInterrupt:
        print("Interrupted. Outstanding reservations remain in the ledger.", file=sys.stderr)
        return 130
    except LedgerError as exc:
        print(f"Ledger refused the operation: {exc}", file=sys.stderr)
        return 2
    except ConfigurationError as exc:
        print(f"Configuration: {exc}", file=sys.stderr)
        return 2
    except (ValueError, sqlite3.Error, OSError):
        print(
            "Cannot start: check task, session, .env settings, credentials, and data directory.",
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            "Provider initialization failed. Check Gemini credentials or Google Cloud ADC.",
            file=sys.stderr,
        )
        return 2
