import json

import pytest
from google.genai import types

from governor import runway
from governor.agent import Agent
from governor.config import BudgetPolicy, Settings
from governor.demo import RUNWAY_PLAN, RUNWAY_TASK, RunwayDemoModel
from governor.ledger import Ledger, LedgerError
from governor.mock import MockPaymentAdapter
from governor.payments import PaymentGate
from governor.runway import distribution, forecast, validate_plan
from governor.tools import ToolRegistry


def inputs(costs, remaining=39000, pending=6, kind="summary"):
    return {
        "remainingAtomic": str(remaining),
        "spentAtomic": str(sum(costs)),
        "heldAtomic": "0",
        "hasPlan": pending is not None,
        "tasksCompleted": len(costs),
        "samples": [
            {"id": str(i), "type": kind, "costAtomic": str(c)} for i, c in enumerate(costs)
        ],
        "pending": None
        if pending is None
        else [{"id": f"next-{i}", "type": kind, "allow_local": True} for i in range(pending)],
    }


@pytest.mark.parametrize("costs", [[], [1000], [1000, 2000]])
def test_minimum_samples_suppresses_every_projection(costs):
    result = forecast(inputs(costs))
    assert result["state"] == "UNKNOWN"
    assert not {"projected", "burnRate", "runwayTasks", "shortfall"} & result.keys()


def test_example_uses_p90_and_only_proposes_scope_that_fits():
    result = forecast(inputs([3200, 3200, 3200, 9800]))
    assert result["state"] == "SHORTFALL"
    assert result["burnRate"] == {"p50": "3200", "p90": "9800"}
    assert result["projected"] == {"p50": "19200", "p90": "58800"}
    assert result["runwayTasks"] == 3
    assert result["shortfall"] == "19800"
    scope = next(o for o in result["options"] if o["action"] == "reduce-scope")
    assert scope["dropTasks"] == 3  # Dropping two still costs 39200 > 39000.
    assert 58800 - int(scope["savings"]) <= 39000
    assert next(o for o in result["options"] if o["action"] == "raise-cap")["needed"] == "19800"
    assert (
        next(o for o in result["options"] if o["action"] == "route-local")["qualityDelta"] is None
    )


@pytest.mark.parametrize(
    "price,state", [(700, "HEALTHY"), (701, "TIGHT"), (1000, "TIGHT"), (1001, "SHORTFALL")]
)
def test_threshold_boundaries_are_exact(price, state):
    assert forecast(inputs([price] * 3, remaining=1000, pending=1))["state"] == state


def test_unknown_workload_reports_only_affordable_calls():
    result = forecast(inputs([2000] * 3, remaining=1000, pending=None))
    assert result["state"] == "UNKNOWN"
    assert result["tasksRemaining"] is None
    assert result["runwayTasks"] == 0
    assert "projected" not in result and "shortfall" not in result
    assert result["options"] == []


def test_mixed_types_use_the_remaining_mix_not_a_pooled_average():
    data = inputs([1000] * 3, remaining=39000, pending=6, kind="fx")
    data["samples"] += [
        {"id": f"s-{i}", "type": "summary", "costAtomic": "10000"} for i in range(3)
    ]
    data["tasksCompleted"] = 6
    for task in data["pending"]:
        task["type"] = "summary"
    result = forecast(data)
    assert result["projected"]["p90"] == "60000"
    assert result["state"] == "SHORTFALL"
    assert result["mixedTaskTypes"]
    data["pending"] = None
    result = forecast(data)
    assert result["burnRate"] == {"p50": "1000", "p90": "10000"}


def test_unseen_type_never_inherits_a_cheaper_sample():
    data = inputs([1000] * 3)
    data["pending"][0]["type"] = "expensive"
    result = forecast(data)
    assert result["state"] == "UNKNOWN"
    assert result["reason"] == "INSUFFICIENT_TYPE_SAMPLES"
    assert "projected" not in result


def test_zero_cost_and_large_integers_are_json_safe():
    zero = forecast(inputs([0] * 3))
    assert zero["state"] == "HEALTHY"
    assert zero["runwayTasks"] is None
    assert zero["projected"]["p90"] == "0"
    large = forecast(inputs([2**60] * 3, remaining=2**61, pending=6))
    assert large["projected"]["p90"] == str(6 * 2**60)
    json.dumps(large, allow_nan=False)


def test_variance_is_exact():
    assert distribution([1000, 2000, 3000])["varianceAtomicSquared"] == {
        "numerator": "6000000",
        "denominator": "9",
    }


def test_local_fallbacks_cannot_dilute_the_paid_baseline():
    data = inputs([2000] * 3, remaining=4000, pending=6)
    data["samples"] += [
        {"id": f"local-{i}", "type": "summary", "costAtomic": "0", "route": "local"}
        for i in range(50)
    ]
    data["tasksCompleted"] = 53
    result = forecast(data)
    assert result["tasksCompleted"] == 53
    assert result["sampleCount"] == 3
    assert result["projected"]["p90"] == "12000"
    assert result["state"] == "SHORTFALL"


def test_local_scope_and_cap_options_require_caller_authority():
    data = inputs([3200, 3200, 3200, 9800])
    for task in data["pending"]:
        task["allow_local"] = False
    options = forecast(data)["options"]
    for option in options:
        if option["action"] != "compare-quotes":
            assert option["requiresApproval"] is True
    assert "savings" not in next(o for o in options if o["action"] == "route-local")


@pytest.fixture
def gate(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3", BudgetPolicy())
    ledger.start("planned", RUNWAY_TASK, plan=RUNWAY_PLAN)
    return PaymentGate(ledger, "planned", MockPaymentAdapter())


async def pay_three(gate):
    for task in RUNWAY_PLAN[:3]:
        await gate.purchase("summary", task["text"])


async def test_shortfall_never_blocks_a_ledger_allowed_payment(gate):
    await pay_three(gate)
    assert gate.ledger.report("planned")["runway"]["state"] == "SHORTFALL"
    allowed = await gate.purchase("summary", RUNWAY_PLAN[3]["text"])
    assert allowed["code"] == "SETTLED"
    assert gate.adapter.authorization_count == 4


async def test_optimistic_or_broken_forecast_never_changes_the_gate(gate, monkeypatch):
    monkeypatch.setattr(runway, "forecast", lambda _: {"state": "HEALTHY"})
    refused = await gate.purchase("overpriced", "Too expensive.")
    assert refused["code"] == "PER_CALL_CAP_EXCEEDED"
    assert gate.adapter.authorization_count == 0

    def broken(_):
        raise RuntimeError("private error text must not enter the audit")

    monkeypatch.setattr(runway, "forecast", broken)
    settled = await gate.purchase("summary", RUNWAY_PLAN[0]["text"])
    assert settled["code"] == "SETTLED"
    assert settled["runway"]["state"] == "UNKNOWN"
    assert gate.ledger.snapshot("planned")["settled"] == "2000"
    assert "private error" not in json.dumps(gate.ledger.report("planned"))


async def test_each_settlement_and_refusal_is_recomputed_with_replayable_inputs(gate):
    await pay_three(gate)
    await gate.purchase("price-change", "A changed price.")
    await gate.purchase("overpriced", "Too much.")
    await gate.purchase("overpriced", "Too much.")  # A cached refusal still recomputes.
    report = gate.ledger.report("planned")
    causes = [e for e in report["events"] if e["kind"] in ("SETTLED", "DENIED")]
    checks = [e for e in report["events"] if e["kind"] == "RUNWAY_CHECK"]
    for cause in causes:
        check = next(e for e in checks if e["data"]["triggerSeq"] == cause["seq"])
        assert forecast(check["data"]["inputs"]) == check["data"]["forecast"]
    transitions = [e for e in report["events"] if e["kind"] == "RUNWAY_STATE_CHANGED"]
    assert any(
        e["data"]["from"] == "UNKNOWN" and e["data"]["to"] == "SHORTFALL" for e in transitions
    )
    assert report["runway"]["tasksCompleted"] == 3
    assert report["runway"]["tasksRemaining"] == 7


async def test_plan_samples_and_holds_survive_restart_without_double_counting(gate):
    await pay_three(gate)
    await gate.purchase("timeout", RUNWAY_PLAN[3]["text"])
    before = gate.ledger.report("planned")["runway"]
    assert before["remaining"] == "2000"  # Available excludes the unresolved hold.
    restarted = Ledger(gate.ledger.path, gate.ledger.policy)
    assert restarted.plan("planned") == RUNWAY_PLAN
    assert restarted.report("planned")["runway"] == before
    again = PaymentGate(restarted, "planned", MockPaymentAdapter())
    result = await again.purchase("summary", RUNWAY_PLAN[0]["text"])
    assert result["code"] == "ALREADY_SETTLED"
    assert result["runway"]["tasksCompleted"] == 3
    assert again.adapter.authorization_count == 0
    with pytest.raises(LedgerError):
        restarted.start("planned", RUNWAY_TASK, resume=True, plan=RUNWAY_PLAN[:5])


async def test_local_completion_requires_prior_permission_and_is_idempotent(tmp_path):
    plan = [dict(t, allow_local=False) for t in RUNWAY_PLAN]
    ledger = Ledger(tmp_path / "ledger.sqlite3", BudgetPolicy())
    ledger.start("unapproved", RUNWAY_TASK, plan=plan)
    assert ledger.complete_local("unapproved", plan[0]["text"]) == []
    assert ledger.report("unapproved")["runway"]["tasksCompleted"] == 0
    ledger.start("approved", RUNWAY_TASK, plan=RUNWAY_PLAN)
    for _ in range(2):
        assert ledger.complete_local("approved", plan[0]["text"]) == ["item-01"]
    assert ledger.report("approved")["runway"]["tasksCompleted"] == 1


async def test_demo_finishes_under_budget_and_preserves_hard_refusal(gate):
    result = await Agent(RunwayDemoModel(), ToolRegistry(gate), Settings()).run(RUNWAY_TASK)
    assert result.status == "COMPLETED"
    assert result.runway["tasksCompleted"] == 10
    assert result.runway["tasksRemaining"] == 0
    assert result.budget["settled"] == "6000"
    assert result.budget["available"] == "4000"
    assert gate.adapter.authorization_count == 3
    report = gate.ledger.report("planned")
    early = next(
        e["data"]["forecast"]
        for e in report["events"]
        if e["kind"] == "RUNWAY_STATE_CHANGED" and e["data"]["to"] == "SHORTFALL"
    )
    assert early["tasksCompleted"] == 3 and early["remaining"] == "4000"
    assert early["projected"]["p90"] == "14000"
    assert any(e["kind"] == "DENIED" for e in report["events"])


def test_invalid_plans_are_rejected_without_guessing():
    for bad in (
        [RUNWAY_PLAN[0]] * 2,
        [{**RUNWAY_PLAN[0], "allow_local": "yes"}],
        [{**RUNWAY_PLAN[0], "remaining": 10}],
    ):
        with pytest.raises(ValueError):
            validate_plan(bad)


async def test_model_cannot_claim_an_unfinished_plan_is_complete(gate):
    class FinishesEarly:
        async def generate(self, *_args):
            return types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(
                            role="model", parts=[types.Part.from_text(text="Done.")]
                        ),
                        finish_reason=types.FinishReason.STOP,
                    )
                ]
            )

    result = await Agent(FinishesEarly(), ToolRegistry(gate), Settings()).run(RUNWAY_TASK)
    assert result.status == "PLAN_INCOMPLETE"
    assert result.runway["tasksRemaining"] == 10
    assert result.budget["available"] == "10000"
