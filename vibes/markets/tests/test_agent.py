import asyncio

import pytest
from google.genai import types

from governor.agent import Agent
from governor.config import Settings
from governor.ledger import Ledger
from governor.mock import MockPaymentAdapter
from governor.payments import PaymentGate
from governor.tools import ToolRegistry


def response(*parts, finish=types.FinishReason.STOP):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=list(parts)),
                finish_reason=finish,
            )
        ]
    )


def call(name, args=None, *, call_id="call-1", signature=None):
    return types.Part(
        function_call=types.FunctionCall(name=name, args=args or {}, id=call_id),
        thought_signature=signature,
    )


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.histories = []

    async def generate(self, history, tools, instruction):
        self.histories.append(list(history))
        return next(self.responses)


@pytest.fixture
def setup(tmp_path):
    settings = Settings(data_dir=tmp_path)
    ledger = Ledger(tmp_path / "ledger.sqlite3", settings.policy)
    ledger.start("test", "Summarize")
    tools = ToolRegistry(PaymentGate(ledger, "test", MockPaymentAdapter()))
    return settings, tools


async def test_tool_roundtrip_preserves_signatures_and_call_ids(setup):
    settings, tools = setup
    first = response(call("get_budget", signature=b"opaque-signature"))
    model = ScriptedModel([first, response(types.Part.from_text(text="Finished."))])
    result = await Agent(model, tools, settings).run("Summarize")
    assert result.status == "COMPLETED"
    assert result.tool_calls == 1
    history = model.histories[1]
    assert history[1] is first.candidates[0].content
    assert history[1].parts[0].thought_signature == b"opaque-signature"
    assert history[2].parts[0].function_response.id == "call-1"


async def test_denial_then_local_fallback(setup):
    settings, tools = setup
    model = ScriptedModel(
        [
            response(call("purchase_service", {"service_id": "overpriced", "text": "Example."})),
            response(call("summarize_local", {"text": "Example."})),
            response(types.Part.from_text(text="Used the local fallback: Example.")),
        ]
    )
    result = await Agent(model, tools, settings).run("Summarize")
    assert result.status == "COMPLETED"
    assert result.budget["settled"] == "0"
    assert tools.gate.adapter.authorization_count == 0
    assert (
        model.histories[1][2].parts[0].function_response.response["code"] == "PER_CALL_CAP_EXCEEDED"
    )


async def test_oversized_batch_cannot_execute_partial_purchases(setup):
    settings, tools = setup
    settings = settings.model_copy(update={"max_tool_calls": 1})
    purchase = call("purchase_service", {"service_id": "summary", "text": "Example."})
    result = await Agent(ScriptedModel([response(purchase, purchase)]), tools, settings).run(
        "Summarize"
    )
    assert result.status == "TOOL_LIMIT"
    assert tools.gate.adapter.authorization_count == 0


async def test_invalid_tool_arguments_cannot_change_budget(setup):
    _, tools = setup
    result = await tools.execute(
        "purchase_service",
        {
            "service_id": "summary",
            "text": "Example.",
            "session_cap": "999999999",
        },
    )
    assert result["code"] == "INVALID_ARGUMENTS"
    assert tools.gate.adapter.authorization_count == 0
    assert (await tools.execute("run_shell", {"command": "echo hi"}))["code"] == "UNKNOWN_TOOL"


async def test_turn_limit_stops_runaway_model(setup):
    settings, tools = setup
    settings = settings.model_copy(update={"max_turns": 2})
    model = ScriptedModel([response(call("get_budget"))] * 3)
    result = await Agent(model, tools, settings).run("Summarize")
    assert result.status == "TURN_LIMIT"
    assert len(model.histories) == 2


async def test_incomplete_response_never_executes_tools(setup):
    settings, tools = setup
    model = ScriptedModel(
        [
            response(
                call(
                    "purchase_service",
                    {
                        "service_id": "summary",
                        "text": "Example.",
                    },
                ),
                finish=types.FinishReason.MAX_TOKENS,
            )
        ]
    )
    result = await Agent(model, tools, settings).run("Summarize")
    assert result.status == "MODEL_INCOMPLETE"
    assert tools.gate.adapter.authorization_count == 0


async def test_model_timeout_returns_structured_failure(setup):
    settings, tools = setup
    settings = settings.model_copy(update={"model_timeout_seconds": 0.01})

    class SlowModel:
        async def generate(self, *args):
            await asyncio.sleep(1)

    result = await Agent(SlowModel(), tools, settings).run("Summarize")
    assert result.status == "MODEL_TIMEOUT"


async def test_model_exception_text_is_not_logged(setup):
    settings, tools = setup

    class BrokenModel:
        async def generate(self, *args):
            raise RuntimeError("secret-api-key")

    result = await Agent(BrokenModel(), tools, settings).run("Summarize")
    assert result.status == "MODEL_OR_TOOL_ERROR"
    assert "secret-api-key" not in str(tools.gate.ledger.report("test"))
