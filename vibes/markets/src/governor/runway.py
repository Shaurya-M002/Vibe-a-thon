"""Pure advisory forecasts. This module cannot reserve, authorize, or change caps."""

import hashlib
import json
from collections import Counter, defaultdict

from pydantic import BaseModel, ConfigDict, Field


class TaskItem(BaseModel):
    """One caller-ordered text item; one successful service result completes it."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    type: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    text: str = Field(min_length=1, max_length=12000)
    allow_local: bool = False


def validate_plan(items: list | None) -> list[dict] | None:
    if items is None:
        return None
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("task plan must be a list of at most 100 items")
    plan = [TaskItem.model_validate(item).model_dump() for item in items]
    if len({t["id"] for t in plan}) != len(plan):
        raise ValueError("task IDs must be unique")
    if len({t["text"] for t in plan}) != len(plan) or any(not t["text"].strip() for t in plan):
        raise ValueError("task texts must be nonblank and unique for completion tracking")
    if sum(len(t["text"]) for t in plan) > 60000:
        raise ValueError("combined task text must not exceed 60000 characters")
    return plan


def purchase_identity(session_id: str, service: str, text: str) -> str:
    identity = json.dumps([session_id, service, text], separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


def inputs_for(session_id: str, budget: dict, attempts: list, events: list) -> dict:
    plan_event = next((e for e in events if e["kind"] == "TASK_PLAN"), None)
    settled = [a for a in attempts if a["status"] == "SETTLED"]
    inputs = {
        "remainingAtomic": budget["available"],
        "spentAtomic": budget["settled"],
        "heldAtomic": budget["held"],
        "hasPlan": plan_event is not None,
        "samples": [],
        "pending": None,
        "tasksCompleted": 0,
    }
    if plan_event is None:
        inputs["samples"] = [
            {"id": a["id"], "type": a["service"], "costAtomic": str(a["amount"])} for a in settled
        ]
        inputs["tasksCompleted"] = len(settled)
        return inputs
    local = {e["data"]["task_id"] for e in events if e["kind"] == "LOCAL_TASK_COMPLETED"}
    inputs["pending"] = []
    for task in plan_event["data"]["items"]:
        matched = [
            a
            for a in attempts
            if a["id"] == purchase_identity(session_id, a["service"], task["text"])
        ]
        paid = [a for a in matched if a["status"] == "SETTLED"]
        complete = bool(paid) or task["id"] in local
        if complete:
            inputs["tasksCompleted"] += 1
            # A completed local result may still have an unresolved paid attempt.
            # Its eventual total cost is unknown, so it is not a cost observation.
            if not any(a["status"] in ("RESERVED", "AUTHORIZING") for a in matched):
                inputs["samples"].append(
                    {
                        "id": task["id"],
                        "type": task["type"],
                        "costAtomic": str(sum(a["amount"] for a in paid)),
                        "route": "paid" if paid else "local",
                    }
                )
        else:
            inputs["pending"].append(
                {
                    "id": task["id"],
                    "type": task["type"],
                    "allow_local": task["allow_local"],
                }
            )
    return inputs


def distribution(costs: list[int]) -> dict:
    """Empirical nearest-rank quantiles and exact population variance, all integers."""
    ordered = sorted(costs)
    n = len(ordered)
    total = sum(ordered)
    return {
        "samples": n,
        "p50": str(ordered[(n * 50 + 99) // 100 - 1]),
        "p90": str(ordered[(n * 90 + 99) // 100 - 1]),
        "meanCeil": str((total + n - 1) // n),
        "varianceAtomicSquared": {
            "numerator": str(n * sum(c * c for c in ordered) - total * total),
            "denominator": str(n * n),
        },
    }


def forecast(inputs: dict) -> dict:
    """Describe observed cost risk. Never grant authority or rewrite the task list."""
    remaining = int(inputs["remainingAtomic"])
    # Keep the paid baseline separate: many zero-cost fallbacks must not make
    # a later item that requires paid quality look free. Completion still counts.
    samples = [s for s in inputs["samples"] if s.get("route", "paid") == "paid"]
    pending = inputs["pending"]
    result = {
        "state": "UNKNOWN",
        "tasksCompleted": inputs["tasksCompleted"],
        "tasksRemaining": len(pending) if pending is not None else None,
        "remaining": str(remaining),
        "sampleCount": len(samples),
        "minimumSamples": 3,
        "sampleUnit": "paid_caller_task" if inputs["hasPlan"] else "settled_payment",
        "basis": "observed paid costs; local completions tracked separately",
        "method": "empirical-nearest-rank-per-type",
        "advisoryOnly": True,
        "options": [],
    }
    if pending is not None:
        result["pendingTaskIds"] = [t["id"] for t in pending]
    if len(samples) < 3:
        return {**result, "reason": "INSUFFICIENT_SAMPLES"}
    groups = defaultdict(list)
    for sample in samples:
        groups[sample["type"]].append(int(sample["costAtomic"]))
    by_type = {kind: distribution(costs) for kind, costs in sorted(groups.items())}
    result["byTaskType"] = by_type
    result["mixedTaskTypes"] = len(groups) > 1
    # Unknown mix uses an envelope, not an average that hides expensive tasks.
    burn = {
        "p50": str(min(int(d["p50"]) for d in by_type.values())),
        "p90": str(max(int(d["p90"]) for d in by_type.values())),
    }
    result["burnRate"] = burn
    if pending is None:
        rate = int(burn["p90"])
        return {
            **result,
            "reason": "TASKS_REMAINING_UNKNOWN" if rate else "NO_OBSERVED_SPEND",
            "runwayTasks": remaining // rate if rate else None,
        }
    missing = sorted({t["type"] for t in pending if len(groups[t["type"]]) < 3})
    if missing:
        return {**result, "reason": "INSUFFICIENT_TYPE_SAMPLES", "unknownTaskTypes": missing}
    counts = Counter(t["type"] for t in pending)
    projected = {
        q: sum(count * int(by_type[kind][q]) for kind, count in counts.items())
        for q in ("p50", "p90")
    }
    if pending:
        # The weighted rates use the caller's remaining mix, rounded upwards.
        result["burnRate"] = {
            q: str((projected[q] + len(pending) - 1) // len(pending)) for q in projected
        }
    rate = int(result["burnRate"]["p90"])
    p90 = projected["p90"]
    state = (
        "HEALTHY" if p90 * 10 <= remaining * 7 else ("TIGHT" if p90 <= remaining else "SHORTFALL")
    )
    result.update(
        {
            "state": state,
            "reason": "P90_PROJECTION" if pending else "PLAN_COMPLETE",
            "projected": {q: str(cost) for q, cost in projected.items()},
            "runwayTasks": remaining // rate if rate else None,
            "shortfall": str(max(0, p90 - remaining)),
        }
    )
    if state in ("TIGHT", "SHORTFALL"):
        local = [t for t in pending if t["allow_local"]]
        if local:
            result["options"].append(
                {
                    "action": "route-local",
                    "taskIds": [t["id"] for t in local],
                    "savings": str(sum(int(by_type[t["type"]]["p90"]) for t in local)),
                    "qualityDelta": None,
                    "basis": "p90 paid-cost avoided; local extractive fallback has zero payment",
                    "requiresApproval": False,
                }
            )
        else:
            result["options"].append(
                {
                    "action": "route-local",
                    "requiresApproval": True,
                    "qualityDelta": None,
                    "basis": "Caller has not approved extractive fallback for remaining tasks.",
                }
            )
        result["options"].append(
            {
                "action": "compare-quotes",
                "basis": "Request prices before choosing a provider.",
            }
        )
    if state == "SHORTFALL":
        # Caller order defines priority. Never silently drop or reorder items.
        keep, cost = [], 0
        for task in pending:
            price = int(by_type[task["type"]]["p90"])
            if cost + price > remaining:
                break
            keep.append(task["id"])
            cost += price
        result["options"].extend(
            [
                {
                    "action": "reduce-scope",
                    "dropTasks": len(pending) - len(keep),
                    "taskIds": [t["id"] for t in pending[len(keep) :]],
                    "savings": str(p90 - cost),
                    "requiresApproval": True,
                },
                {"action": "prioritized-subset", "taskIds": keep, "requiresApproval": True},
                {"action": "raise-cap", "needed": str(p90 - remaining), "requiresApproval": True},
            ]
        )
    return result
