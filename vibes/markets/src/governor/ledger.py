"""Durable, atomic reservations. Caps come from the operator's current config."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from governor import runway
from governor.config import BudgetPolicy, atomic


class LedgerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Reservation:
    status: str
    created: bool
    result: dict


class Ledger:
    def __init__(self, path: Path, policy: BudgetPolicy):
        self.path = path
        self.policy = policy
        self.policy_hash = hashlib.sha256(policy.model_dump_json().encode()).hexdigest()
        existing = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._transaction() as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise LedgerError("ledger integrity check failed")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise LedgerError("unsupported ledger schema")
            if existing:
                tables = {
                    row[0]
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if version != 1 or not {"sessions", "attempts", "events"} <= tables:
                    raise LedgerError("existing ledger is incomplete; refusing to recreate state")
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, task TEXT NOT NULL, policy_hash TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS attempts (
                session_id TEXT NOT NULL REFERENCES sessions(id),
                id TEXT NOT NULL, service TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK(amount > 0),
                status TEXT NOT NULL CHECK(status IN
                    ('RESERVED','AUTHORIZING','SETTLED','RELEASED','DENIED')),
                result TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(session_id, id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                time TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL)""")
            db.execute("PRAGMA user_version = 1")
        path.chmod(0o600)

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA synchronous = FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _session(self, db, session_id):
        row = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            raise LedgerError("session does not exist; explicitly create a new session")
        if row["policy_hash"] != self.policy_hash:
            raise LedgerError("session policy differs from configuration; refusing to resume")
        return row

    def _event(self, db, session_id, kind, data):
        seq = db.execute(
            "INSERT INTO events(session_id,time,kind,data) VALUES (?,?,?,?)",
            (session_id, datetime.now(UTC).isoformat(), kind, json.dumps(data)),
        ).lastrowid
        if kind in {
            "SESSION_CREATED",
            "TASK_PLAN",
            "SETTLED",
            "DENIED",
            "RESERVED",
            "RELEASED",
            "LOCAL_TASK_COMPLETED",
            "REFUSAL_RETURN",
        }:
            self._runway_check(db, session_id, kind, seq)

    def _runway_check(self, db, session_id, trigger, seq):
        previous = db.execute(
            "SELECT data FROM events WHERE session_id=? AND kind='RUNWAY_CHECK' "
            "ORDER BY seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        old_state = json.loads(previous[0])["forecast"]["state"] if previous else None
        inputs = None
        try:
            attempts = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM attempts WHERE session_id=? ORDER BY rowid",
                    (session_id,),
                )
            ]
            events = [
                {"kind": r["kind"], "data": json.loads(r["data"])}
                for r in db.execute(
                    "SELECT kind,data FROM events WHERE session_id=? "
                    "AND kind IN ('TASK_PLAN','LOCAL_TASK_COMPLETED') ORDER BY seq",
                    (session_id,),
                )
            ]
            inputs = runway.inputs_for(session_id, self._snapshot(db, session_id), attempts, events)
            estimate = runway.forecast(inputs)
            if not isinstance(estimate, dict) or estimate.get("state") not in {
                "UNKNOWN",
                "HEALTHY",
                "TIGHT",
                "SHORTFALL",
            }:
                raise ValueError("invalid advisory forecast")
        except Exception:
            # A broken planner must never reverse a settlement or pre-empt a cap decision.
            # Database/audit write failures still follow the ledger's fail-closed behavior.
            estimate = {
                "state": "UNKNOWN",
                "reason": "FORECAST_UNAVAILABLE",
                "advisoryOnly": True,
            }
        data = {"trigger": trigger, "triggerSeq": seq, "inputs": inputs, "forecast": estimate}
        now = datetime.now(UTC).isoformat()
        db.execute(
            "INSERT INTO events(session_id,time,kind,data) VALUES (?,?,?,?)",
            (session_id, now, "RUNWAY_CHECK", json.dumps(data)),
        )
        if old_state != estimate["state"]:
            db.execute(
                "INSERT INTO events(session_id,time,kind,data) VALUES (?,?,?,?)",
                (
                    session_id,
                    now,
                    "RUNWAY_STATE_CHANGED",
                    json.dumps(
                        {
                            **data,
                            "from": old_state,
                            "to": estimate["state"],
                        }
                    ),
                ),
            )

    def start(
        self,
        session_id: str,
        task: str,
        *,
        resume: bool = False,
        plan: list | None = None,
        payment_mode: str = "mock",
        client_request: dict | None = None,
        budget_policy: BudgetPolicy | None = None,
    ) -> None:
        if payment_mode not in ("mock", "solana-devnet"):
            raise LedgerError("unsupported payment mode")
        plan = runway.validate_plan(plan)
        if budget_policy and (
            budget_policy.session_cap > self.policy.session_cap
            or budget_policy.per_call_cap > self.policy.per_call_cap
        ):
            raise LedgerError("Session limits exceed the operator policy")
        with self._transaction() as db:
            if resume:
                row = self._session(db, session_id)
                if budget_policy and budget_policy != self._budget_policy(db, session_id):
                    raise LedgerError("A resumed session cannot replace its budget limits")
                if row["task"] != task:
                    raise LedgerError("a session must resume its original task")
                if self._payment_mode(db, session_id) != payment_mode:
                    raise LedgerError("a resumed session cannot change its payment mode")
                if plan is not None and plan != self._plan(db, session_id):
                    raise LedgerError("a resumed session cannot replace its caller task list")
            else:
                if db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
                    raise LedgerError("session already exists; use resume")
                db.execute(
                    "INSERT INTO sessions VALUES (?,?,?)", (session_id, task, self.policy_hash)
                )
            if budget_policy and not resume:
                self._event(db, session_id, "SESSION_LIMITS", budget_policy.model_dump())
            self._event(
                db,
                session_id,
                "SESSION_RESUMED" if resume else "SESSION_CREATED",
                {"payment_mode": payment_mode},
            )
            if client_request is not None and not resume:
                self._event(db, session_id, "CLIENT_REQUEST", client_request)
            if not resume and plan is not None:
                self._event(db, session_id, "TASK_PLAN", {"items": plan})

    @staticmethod
    def _plan(db, session_id):
        row = db.execute(
            "SELECT data FROM events WHERE session_id=? AND kind='TASK_PLAN' ORDER BY seq LIMIT 1",
            (session_id,),
        ).fetchone()
        return json.loads(row[0])["items"] if row else None

    def plan(self, session_id: str) -> list | None:
        with self._transaction() as db:
            self._session(db, session_id)
            return self._plan(db, session_id)

    def complete_local(self, session_id: str, text: str) -> list[str]:
        """Only caller-approved local output can complete a matching planned item."""
        with self._transaction() as db:
            self._session(db, session_id)
            completed = {
                json.loads(r[0])["task_id"]
                for r in db.execute(
                    "SELECT data FROM events WHERE session_id=? AND kind='LOCAL_TASK_COMPLETED'",
                    (session_id,),
                )
            }
            matched = []
            for item in self._plan(db, session_id) or []:
                if item["text"] == text and item["allow_local"]:
                    matched.append(item["id"])
                    if item["id"] not in completed:
                        self._event(
                            db,
                            session_id,
                            "LOCAL_TASK_COMPLETED",
                            {
                                "task_id": item["id"],
                                "type": item["type"],
                                "payment_amount": "0",
                            },
                        )
            return matched

    def task(self, session_id: str) -> str:
        with self._transaction() as db:
            return self._session(db, session_id)["task"]

    def sessions(self) -> list[dict]:
        """List local sessions without applying a new policy to historical budgets."""
        with self._transaction() as db:
            return [
                {
                    "session_id": row["id"],
                    "task": row["task"],
                    "compatible": row["policy_hash"] == self.policy_hash,
                    "created_at": row["created_at"],
                }
                for row in db.execute(
                    "SELECT s.*, (SELECT MIN(time) FROM events WHERE session_id=s.id) "
                    "AS created_at FROM sessions s ORDER BY s.rowid DESC"
                )
            ]

    def _payment_mode(self, db, session_id):
        row = db.execute(
            "SELECT data FROM events WHERE session_id=? AND kind='SESSION_CREATED' "
            "ORDER BY seq LIMIT 1",
            (session_id,),
        ).fetchone()
        return json.loads(row[0]).get("payment_mode", "mock") if row else "mock"

    def _budget_policy(self, db, session_id):
        row = db.execute(
            "SELECT data FROM events WHERE session_id=? AND kind='SESSION_LIMITS' "
            "ORDER BY seq LIMIT 1",
            (session_id,),
        ).fetchone()
        selected = BudgetPolicy.model_validate_json(row[0]) if row else self.policy
        if (
            selected.session_cap > self.policy.session_cap
            or selected.per_call_cap > self.policy.per_call_cap
        ):
            raise LedgerError("Stored session limits exceed the operator policy")
        return selected

    def _snapshot(self, db, session_id):
        self._session(db, session_id)
        rows = db.execute(
            "SELECT status, SUM(amount) AS total FROM attempts WHERE session_id=? GROUP BY status",
            (session_id,),
        ).fetchall()
        totals = {row["status"]: row["total"] for row in rows}
        settled = totals.get("SETTLED", 0)
        held = totals.get("RESERVED", 0) + totals.get("AUTHORIZING", 0)
        policy = self._budget_policy(db, session_id)
        available = policy.session_cap - settled - held
        if available < 0:
            raise LedgerError("ledger exposure exceeds configured budget")
        return {
            "session_cap": str(policy.session_cap),
            "per_call_cap": str(policy.per_call_cap),
            "settled": str(settled),
            "held": str(held),
            "available": str(available),
            "unit": "atomic_usdc",
            "payment_mode": self._payment_mode(db, session_id),
        }

    def snapshot(self, session_id: str) -> dict:
        with self._transaction() as db:
            return self._snapshot(db, session_id)

    def reserve(self, session_id: str, attempt_id: str, service: str, amount: str) -> Reservation:
        units = atomic(amount)
        if units == 0:
            raise ValueError("paid reservations must have a positive amount")
        with self._transaction() as db:
            snapshot = self._snapshot(db, session_id)
            previous = db.execute(
                "SELECT * FROM attempts WHERE session_id=? AND id=?", (session_id, attempt_id)
            ).fetchone()
            if previous:
                if previous["service"] != service or previous["amount"] != units:
                    raise LedgerError("attempt identity reused with different payment terms")
                return Reservation(previous["status"], False, json.loads(previous["result"]))
            reason = None
            if units > int(snapshot["per_call_cap"]):
                reason = "PER_CALL_CAP_EXCEEDED"
            elif units > int(snapshot["available"]):
                reason = "SESSION_CAP_EXCEEDED"
            status = "DENIED" if reason else "RESERVED"
            result = {"code": reason} if reason else {}
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?,?)",
                (session_id, attempt_id, service, units, status, json.dumps(result)),
            )
            self._event(
                db,
                session_id,
                status,
                {
                    "attempt_id": attempt_id,
                    "service": service,
                    "amount": amount,
                    "code": reason,
                    "budget": self._snapshot(db, session_id),
                },
            )
            return Reservation(status, True, result)

    def lookup(self, session_id: str, attempt_id: str) -> Reservation | None:
        with self._transaction() as db:
            self._session(db, session_id)
            row = db.execute(
                "SELECT * FROM attempts WHERE session_id=? AND id=?", (session_id, attempt_id)
            ).fetchone()
            return Reservation(row["status"], False, json.loads(row["result"])) if row else None

    def mark_authorizing(self, session_id: str, attempt_id: str) -> None:
        """Durably mark potential signature exposure BEFORE calling the signer."""
        with self._transaction() as db:
            self._snapshot(db, session_id)
            changed = db.execute(
                "UPDATE attempts SET status='AUTHORIZING' "
                "WHERE session_id=? AND id=? AND status='RESERVED'",
                (session_id, attempt_id),
            ).rowcount
            if changed != 1:
                raise LedgerError("attempt is not available for authorization")
            self._event(db, session_id, "AUTHORIZING", {"attempt_id": attempt_id})

    def commit(self, session_id: str, attempt_id: str, actual: str, result: dict) -> None:
        units = atomic(actual)
        mismatch = False
        with self._transaction() as db:
            self._session(db, session_id)
            row = db.execute(
                "SELECT * FROM attempts WHERE session_id=? AND id=?", (session_id, attempt_id)
            ).fetchone()
            if not row or row["status"] not in ("AUTHORIZING", "SETTLED"):
                raise LedgerError("attempt has no outstanding authorization")
            if units != row["amount"]:
                mismatch = True
                self._event(
                    db,
                    session_id,
                    "SETTLEMENT_MISMATCH",
                    {
                        "attempt_id": attempt_id,
                        "reported": actual,
                        "reserved": str(row["amount"]),
                    },
                )
            elif row["status"] != "SETTLED":
                db.execute(
                    "UPDATE attempts SET status='SETTLED', result=? WHERE session_id=? AND id=?",
                    (json.dumps(result), session_id, attempt_id),
                )
                self._event(
                    db,
                    session_id,
                    "SETTLED",
                    {
                        "attempt_id": attempt_id,
                        "amount": actual,
                        "budget": self._snapshot(db, session_id),
                    },
                )
        if mismatch:
            raise LedgerError("settlement amount differs from reservation; hold retained")

    def release_unsigned(self, session_id: str, attempt_id: str, reason: str) -> None:
        """Only an unsigned reservation is safe to release without chain reconciliation."""
        with self._transaction() as db:
            self._session(db, session_id)
            changed = db.execute(
                "UPDATE attempts SET status='RELEASED',result=? "
                "WHERE session_id=? AND id=? AND status='RESERVED'",
                (json.dumps({"code": reason}), session_id, attempt_id),
            ).rowcount
            if changed != 1:
                raise LedgerError("cannot release an authorization that may still settle")
            self._event(
                db,
                session_id,
                "RELEASED",
                {
                    "attempt_id": attempt_id,
                    "reason": reason,
                    "budget": self._snapshot(db, session_id),
                },
            )

    def record(self, session_id: str, kind: str, data: dict) -> None:
        with self._transaction() as db:
            self._session(db, session_id)
            self._event(db, session_id, kind, data)

    def report(self, session_id: str) -> dict:
        with self._transaction() as db:
            snapshot = self._snapshot(db, session_id)
            events = [
                {
                    "seq": row["seq"],
                    "time": row["time"],
                    "kind": row["kind"],
                    "data": json.loads(row["data"]),
                }
                for row in db.execute(
                    "SELECT * FROM events WHERE session_id=? ORDER BY seq", (session_id,)
                )
            ]
            attempts = [
                {
                    "attempt_id": row["id"],
                    "service": row["service"],
                    "amount": str(row["amount"]),
                    "status": row["status"],
                    "result": json.loads(row["result"]),
                }
                for row in db.execute(
                    "SELECT * FROM attempts WHERE session_id=? ORDER BY rowid", (session_id,)
                )
            ]
            return {
                "session_id": session_id,
                "budget": snapshot,
                "attempts": attempts,
                "events": events,
                "discovery": next(
                    (
                        e["data"]["discovery"]
                        for e in reversed(events)
                        if e["kind"].startswith("DISCOVERY_") and "discovery" in e["data"]
                    ),
                    {
                        "status": "IDLE",
                        "query": "",
                        "queries": [],
                        "candidates": [],
                        "selected_id": None,
                        "summary": "",
                        "errors": [],
                        "partial_results": False,
                    },
                ),
                "runway": next(
                    (
                        e["data"]["forecast"]
                        for e in reversed(events)
                        if e["kind"] == "RUNWAY_CHECK"
                    ),
                    {"state": "UNKNOWN", "reason": "NO_OBSERVATIONS", "advisoryOnly": True},
                ),
                "task_plan": self._plan(db, session_id),
            }
