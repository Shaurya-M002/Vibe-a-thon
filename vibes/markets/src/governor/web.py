"""Loopback-only dashboard. Reuses the CLI agent, policy and durable ledger."""

import argparse
import asyncio
import json
import mimetypes
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from dotenv import load_dotenv

from governor.agent import Agent
from governor.cli import expense_csv, session_name, write_audit
from governor.config import ConfigurationError, Settings
from governor.demo import (
    RUNWAY_PLAN,
    RUNWAY_TASK,
    TASK,
    VENDOR_TASK,
    DemoModel,
    RunwayDemoModel,
    VendorDemoModel,
)
from governor.discovery import VendorScout
from governor.gemini import GeminiModel
from governor.ledger import Ledger, LedgerError
from governor.mock import MockPaymentAdapter
from governor.networks import DEVNET_RPC
from governor.payments import PaymentGate
from governor.plugin_sessions import PluginSessions
from governor.runway import validate_plan
from governor.solana_wallet import DevnetRPC, WalletError, load_wallet
from governor.tools import ToolRegistry
from governor.vendor.server import VENDOR_AMOUNT, VENDOR_ORIGIN, vendor_public

STATIC = Path(__file__).with_name("static")


class BusyError(RuntimeError):
    pass


class PacedDemo(DemoModel):
    async def generate(self, *args):
        await asyncio.sleep(0.25)
        return await super().generate(*args)


class PacedRunwayDemo(RunwayDemoModel):
    async def generate(self, *args):
        # Give the local dashboard time to display the early warning before
        # the scripted model follows its approved fallback option.
        history = args[0]
        warning = any(
            part.function_response
            and part.function_response.response.get("runway", {}).get("state") == "SHORTFALL"
            for part in history[-1].parts or []
        )
        await asyncio.sleep(2 if warning else 0.5)
        return await super().generate(*args)


class Dashboard:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ledger = Ledger(settings.data_dir / "ledger.sqlite3", settings.policy)
        self.lock = threading.Lock()
        self.active: str | None = None
        self.plugin = PluginSessions(self)

    def state(self) -> dict:
        with self.lock:
            active = self.active
        return {
            "model": self.settings.model,
            "backend": self.settings.backend,
            "project": self.settings.project,
            "location": self.settings.location,
            "payment_mode": "mock",
            "plugin_api": 1,
            "network": "Solana Devnet",
            "policy": {
                "session_cap": str(self.settings.policy.session_cap),
                "per_call_cap": str(self.settings.policy.per_call_cap),
                "max_turns": self.settings.max_turns,
                "max_tool_calls": self.settings.max_tool_calls,
                "run_timeout_seconds": self.settings.run_timeout_seconds,
            },
            "active_session": active,
            "sessions": self.ledger.sessions(),
            "services": [s.model_dump() for s in MockPaymentAdapter().catalog()],
            "discovery": {
                "source": "Coinbase Bazaar",
                "network": "Solana Devnet",
                "payment_ready": False,
            },
            "vendor": self.vendor_info(),
        }

    def vendor_info(self):
        try:
            return {
                "configured": True,
                **vendor_public(self.settings.data_dir),
                "service_id": "vendor-summary",
            }
        except (OSError, ValueError, KeyError):
            return {"configured": False, "origin": VENDOR_ORIGIN, "amount": VENDOR_AMOUNT}

    def report(self, session_id: str) -> dict:
        session_name(session_id)
        # Snapshot liveness with the ledger: a worker finishing between these reads
        # must not make a completed run appear interrupted.
        with self.lock:
            active = self.active == session_id
            report = self.ledger.report(session_id)
        report["task"] = self.ledger.task(session_id)
        report["result"] = None
        path = self.settings.data_dir / "reports" / f"{session_id}.json"
        if path.is_file():
            try:
                saved = json.loads(path.read_text())
                if saved.get("session_id") == session_id:
                    report["result"] = saved.get("result")
            except (OSError, ValueError):
                pass  # The SQLite ledger remains the authority.
        finished = next(
            (e for e in reversed(report["events"]) if e["kind"] == "RUN_FINISHED"), None
        )
        report["status"] = (
            "RUNNING" if active else finished["data"]["status"] if finished else "INTERRUPTED"
        )
        self.plugin.decorate(report, active)
        if not active and report["discovery"]["status"] in ("PLANNING", "SEARCHING", "RANKING"):
            report["discovery"] = {
                **report["discovery"],
                "status": "CANCELLED",
                "summary": "The scout run was interrupted.",
            }
        return report

    def start(self, payload: dict) -> str:
        if not isinstance(payload, dict) or set(payload) - {
            "mode",
            "task",
            "task_list",
            "discover",
        }:
            raise ValueError("Expected mode, task, and optional task_list only.")
        mode = payload.get("mode")
        if mode not in ("demo", "gemini", "runway-demo", "gemini-devnet", "vendor-demo"):
            raise ValueError("Choose Gemini or the offline demo.")
        discover = payload.get("discover", False)
        if type(discover) is not bool or (discover and mode not in ("gemini", "gemini-devnet")):
            raise ValueError("Vendor discovery is available for Gemini runs only.")
        task = (
            VENDOR_TASK
            if mode == "vendor-demo"
            else (
                RUNWAY_TASK
                if mode == "runway-demo"
                else (TASK if mode == "demo" else payload.get("task"))
            )
        )
        if mode not in ("gemini", "gemini-devnet") and payload.get("task_list") is not None:
            raise ValueError("Scripted demos use their own fixed task lists.")
        plan = validate_plan(RUNWAY_PLAN if mode == "runway-demo" else payload.get("task_list"))
        if not isinstance(task, str) or not 1 <= len(task.strip()) <= 20000:
            raise ValueError("Enter a task between 1 and 20,000 characters.")
        if mode in ("gemini", "gemini-devnet"):
            self.settings.require_credentials()
        if mode in ("gemini-devnet", "vendor-demo"):
            vendor_public(self.settings.data_dir)
        return self._start_worker(task.strip(), mode, plan, discover)

    def discover(self, payload: dict) -> str:
        if not isinstance(payload, dict) or set(payload) != {"query"}:
            raise ValueError("Provide a service search query only.")
        query = payload["query"]
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 400:
            raise ValueError("Search query must contain 1–400 characters.")
        self.settings.require_credentials()
        return self._start_worker(query.strip(), "discovery", None, True)

    def _start_worker(self, task: str, mode: str, plan, discover: bool) -> str:
        with self.lock:
            if self.active:
                raise BusyError("An agent is already running. Wait for it to finish.")
            session_id = f"{mode}-{uuid.uuid4().hex[:10]}"
            payment_mode = "solana-devnet" if mode in ("gemini-devnet", "vendor-demo") else "mock"
            self.ledger.start(session_id, task.strip(), plan=plan, payment_mode=payment_mode)
            self.active = session_id
            worker = threading.Thread(
                target=self._run, args=(session_id, task.strip(), mode, discover), daemon=True
            )
            worker.start()
        return session_id

    def _run(self, session_id: str, task: str, mode: str, discover: bool = False) -> None:
        async def run():
            model = None
            try:
                settings = self.settings
                if mode in ("demo", "runway-demo", "vendor-demo"):
                    model = (
                        VendorDemoModel()
                        if mode == "vendor-demo"
                        else PacedRunwayDemo()
                        if mode == "runway-demo"
                        else PacedDemo()
                    )
                    settings = settings.model_copy(update={"model": "offline-scripted-demo"})
                else:
                    model = GeminiModel(settings)
                adapter = MockPaymentAdapter()
                if mode in ("gemini-devnet", "vendor-demo"):
                    from governor.live_payments import DevnetPaymentAdapter

                    adapter = DevnetPaymentAdapter(settings.data_dir, self.ledger, session_id)
                scout = (
                    VendorScout(model, self.ledger, session_id, settings)
                    if mode in ("gemini", "gemini-devnet", "discovery")
                    else None
                )
                if mode == "discovery":
                    self.ledger.record(
                        session_id,
                        "RUN_STARTED",
                        {"model": settings.model, "agent": "vendor-scout"},
                    )
                    scout.start(task)
                    try:
                        discovery = await scout.result()
                    finally:
                        await scout.cancel()
                    status = (
                        "COMPLETED"
                        if discovery["status"] in ("COMPLETED", "NO_MATCH")
                        else "DISCOVERY_FAILED"
                    )
                    self.ledger.record(
                        session_id, "RUN_FINISHED", {"status": status, "usage": discovery["usage"]}
                    )
                    return {
                        "session_id": session_id,
                        "status": status,
                        "answer": discovery["summary"],
                        "discovery": discovery,
                        "usage": discovery["usage"],
                        "model_mode": mode,
                        "payment_mode": "mock",
                        "simulated_authorizations_this_run": 0,
                    }
                registry = ToolRegistry(
                    PaymentGate(self.ledger, session_id, adapter),
                    scout=scout,
                    auto_discover=discover,
                )
                result = await Agent(model, registry, settings).run(task)
                return {
                    **result.to_dict(),
                    "model_mode": mode,
                    "payment_mode": registry.gate.mode,
                    "simulated_authorizations_this_run": adapter.authorization_count
                    if registry.gate.mode == "mock"
                    else 0,
                    "devnet_authorizations_this_run": adapter.authorization_count
                    if registry.gate.mode == "solana-devnet"
                    else 0,
                }
            finally:
                if isinstance(model, GeminiModel):
                    await model.close()

        result = None
        try:
            result = asyncio.run(run())
        except Exception:
            # Provider errors must not expose credential values or secret URLs.
            result = {
                "status": "RUN_ERROR",
                "answer": "Run stopped. Check the local credentials and ledger configuration.",
                "model_mode": mode,
                "payment_mode": self.ledger.snapshot(session_id)["payment_mode"],
            }
            try:
                self.ledger.record(session_id, "RUN_FINISHED", {"status": "RUN_ERROR"})
            except (LedgerError, sqlite3.Error, OSError):
                pass
        finally:
            try:
                report = self.ledger.report(session_id)
                write_audit(self.settings.data_dir, session_id, {**report, "result": result})
            finally:
                with self.lock:
                    self.active = None

    def wallet(self) -> dict:
        path = self.settings.data_dir / "wallets" / "solana-devnet.json"
        if not path.exists():
            return {"status": "not_configured", "network": "Solana Devnet"}
        address = str(load_wallet(path).pubkey())
        try:
            with httpx.Client(timeout=8) as client:
                balances = DevnetRPC(client, DEVNET_RPC).balances(address)
            return {
                "status": "verified",
                "address": address,
                "network": "Solana Devnet",
                "checked_at": datetime.now(UTC).isoformat(),
                **balances,
            }
        except (WalletError, ValueError, KeyError, TypeError, httpx.HTTPError):
            return {"status": "unavailable", "address": address, "network": "Solana Devnet"}

    def reconcile(self, payload: dict) -> dict:
        from governor.live_payments import DevnetPaymentAdapter

        if not isinstance(payload, dict) or set(payload) != {"session_id", "attempt_id"}:
            raise ValueError("Expected session_id and attempt_id")
        if not isinstance(payload["session_id"], str):
            raise ValueError("Invalid session")
        session_id = session_name(payload["session_id"])
        attempt_id = payload["attempt_id"]
        if not isinstance(attempt_id, str) or len(attempt_id) != 64:
            raise ValueError("Invalid attempt")
        gate = PaymentGate(
            self.ledger,
            session_id,
            DevnetPaymentAdapter(self.settings.data_dir, self.ledger, session_id),
        )
        result = asyncio.run(gate.reconcile(attempt_id))
        # The ledger is authoritative; preserve the original model answer as historical text.
        return result


def make_server(app: Dashboard, port: int = 8787) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, code: int, body, content_type="application/json", download=None):
            if content_type == "application/json":
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self'; font-src 'self'; connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            )
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(body)

        def allowed(self, *, mutation=False) -> bool:
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host = self.headers.get("Host")
            origin = self.headers.get("Origin")
            if host not in hosts or (origin and origin != f"http://{host}"):
                self.respond(403, {"error": "Only the local dashboard origin is allowed."})
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.respond(403, {"error": "Cross-site requests are not allowed."})
                return False
            if mutation and (
                origin != f"http://{host}" or self.headers.get("Content-Type") != "application/json"
            ):
                self.respond(403, {"error": "Use the local dashboard to start an agent."})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            url = urlsplit(self.path)
            try:
                if url.path == "/api/state":
                    return self.respond(200, app.state())
                if url.path == "/api/wallet":
                    return self.respond(200, app.wallet())
                if url.path.startswith("/api/sessions/"):
                    session_id = session_name(url.path.removeprefix("/api/sessions/"))
                    report = app.report(session_id)
                    export = parse_qs(url.query).get("format", [None])[0]
                    if export == "csv":
                        return self.respond(
                            200, expense_csv(report), "text/csv", f"{session_id}.csv"
                        )
                    return self.respond(
                        200, report, download=f"{session_id}.json" if export == "json" else None
                    )
                path = (STATIC / (url.path.lstrip("/") or "index.html")).resolve()
                if not path.is_relative_to(STATIC.resolve()) or not path.is_file():
                    return self.respond(404, {"error": "Page not found."})
                return self.respond(
                    200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "text/plain"
                )
            except (ValueError, argparse.ArgumentTypeError):
                self.respond(400, {"error": "Invalid session identifier."})
            except LedgerError:
                self.respond(
                    409, {"error": "Session unavailable or policy changed. Check the CLI."}
                )
            except (OSError, sqlite3.Error, WalletError):
                self.respond(
                    503, {"error": "Local state unavailable. Check the CLI configuration."}
                )

        def do_POST(self):
            if not self.allowed(mutation=True):
                return
            if self.path not in (
                "/api/runs",
                "/api/discovery",
                "/api/reconcile",
                "/api/plugin/runs",
                "/api/plugin/calls",
                "/api/plugin/finish",
                "/api/plugin/messages",
            ):
                return self.respond(404, {"error": "Endpoint not found."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 100000 or self.headers.get("Transfer-Encoding"):
                    return self.respond(413, {"error": "Request is too large or empty."})
                self.connection.settimeout(10)
                payload = json.loads(self.rfile.read(length))
                if self.path == "/api/plugin/calls":
                    return self.respond(200, app.plugin.call(payload))
                if self.path == "/api/plugin/messages":
                    return self.respond(200, app.plugin.message(payload))
                if self.path == "/api/plugin/finish":
                    return self.respond(200, app.plugin.finish(payload))
                if self.path == "/api/reconcile":
                    return self.respond(200, app.reconcile(payload))
                if self.path == "/api/plugin/runs":
                    return self.respond(202, {"session_id": app.plugin.create(payload)})
                start = app.discover if self.path == "/api/discovery" else app.start
                return self.respond(202, {"session_id": start(payload)})
            except BusyError as exc:
                self.respond(409, {"error": str(exc)})
            except ConfigurationError as exc:
                self.respond(400, {"error": str(exc)})
            except (ValueError, UnicodeDecodeError, argparse.ArgumentTypeError):
                self.respond(400, {"error": "Provide a valid task or service search query."})
            except (LedgerError, sqlite3.Error, OSError):
                self.respond(503, {"error": "Cannot start a run. Local state is unavailable."})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Governor local dashboard")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env", override=False)
    settings = Settings.from_env()
    if args.data_dir:
        settings = settings.model_copy(update={"data_dir": args.data_dir})
    app = Dashboard(settings)
    server = make_server(app, args.port)
    print(f"Governor dashboard: http://127.0.0.1:{server.server_port}", flush=True)
    print(
        "Local access only. Sandbox payments are simulated; Devnet modes transfer USDC.", flush=True
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
