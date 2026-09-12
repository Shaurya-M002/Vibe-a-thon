"""The child scout runs beside the parent, without acquiring payment authority."""

import asyncio

from google.genai import types

from governor.agent import Agent
from governor.config import Settings
from governor.discovery import VendorScout
from governor.ledger import Ledger
from governor.mock import MockPaymentAdapter
from governor.payments import PaymentGate
from governor.tools import ToolRegistry


def response(name=None, args=None, text=None):
    part = (
        types.Part(function_call=types.FunctionCall(name=name, args=args or {}))
        if name
        else types.Part.from_text(text=text)
    )
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[part]),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )


async def test_parent_works_during_scout_and_receives_findings(tmp_path):
    settings = Settings(data_dir=tmp_path)
    ledger = Ledger(tmp_path / "ledger.sqlite3", settings.policy)
    ledger.start("parallel", "Find a weather API; do not purchase anything")
    search_started, parent_worked = asyncio.Event(), asyncio.Event()

    class Registry:
        async def search(self, query):
            search_started.set()
            await parent_worked.wait()
            return {"resources": [], "partialResults": False}

    class Model:
        main_calls = 0

        async def generate(self, history, tools, instruction):
            if tools[0].name == "submit_search_plan":
                assert len(history) == 1  # Separate child context, not the parent's history.
                return response("submit_search_plan", {"queries": ["weather", "forecast"]})
            self.main_calls += 1
            if self.main_calls == 1:
                await asyncio.wait_for(search_started.wait(), 1)
                parent_worked.set()
                return response("get_budget")
            if self.main_calls == 2:
                return response("get_vendor_search")
            result = history[-1].parts[0].function_response.response
            assert result["data"]["discovery"]["status"] == "NO_MATCH"
            return response(text="No matching Devnet weather listing was returned.")

    model = Model()
    scout = VendorScout(model, ledger, "parallel", settings, client=Registry())
    adapter = MockPaymentAdapter()
    tools = ToolRegistry(PaymentGate(ledger, "parallel", adapter), scout=scout, auto_discover=True)
    result = await asyncio.wait_for(Agent(model, tools, settings).run(ledger.task("parallel")), 3)
    assert result.status == "COMPLETED"
    assert result.discovery["status"] == "NO_MATCH"
    assert result.budget["available"] == "10000"
    assert adapter.authorization_count == 0
    events = ledger.report("parallel")["events"]
    budget_event = next(
        e["seq"] for e in events if e["kind"] == "TOOL_RESULT" and e["data"]["name"] == "get_budget"
    )
    finish = next(e["seq"] for e in events if e["kind"] == "DISCOVERY_FINISHED")
    assert budget_event < finish
    assert (
        await tools.execute(
            "purchase_service", {"service_id": "bazaar-unapproved", "text": "Example"}
        )
    )["code"] == "UNKNOWN_SERVICE"
    assert adapter.authorization_count == 0


async def test_early_final_answer_waits_for_scout(tmp_path):
    settings = Settings(data_dir=tmp_path)
    ledger = Ledger(tmp_path / "ledger.sqlite3", settings.policy)
    ledger.start("early", "Find a weather service")

    class Registry:
        async def search(self, query):
            return {"resources": [], "partialResults": False}

    class Model:
        main_calls = 0

        async def generate(self, history, tools, instruction):
            if tools[0].name == "submit_search_plan":
                return response("submit_search_plan", {"queries": ["weather"]})
            self.main_calls += 1
            if self.main_calls == 1:
                return response(text="Premature answer")
            assert '"vendor_scout_result"' in history[-1].parts[0].text
            return response(text="The scout returned no match.")

    model = Model()
    scout = VendorScout(model, ledger, "early", settings, client=Registry())
    tools = ToolRegistry(
        PaymentGate(ledger, "early", MockPaymentAdapter()), scout=scout, auto_discover=True
    )
    result = await Agent(model, tools, settings).run(ledger.task("early"))
    assert result.answer == "The scout returned no match."
    assert model.main_calls == 2


async def test_parent_turn_limit_cancels_child_searches(tmp_path):
    settings = Settings(data_dir=tmp_path, max_turns=1)
    ledger = Ledger(tmp_path / "ledger.sqlite3", settings.policy)
    ledger.start("cancel", "Find a weather API")
    started, cancelled = asyncio.Event(), asyncio.Event()

    class Registry:
        async def search(self, query):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    class Model:
        async def generate(self, history, tools, instruction):
            if tools[0].name == "submit_search_plan":
                return response("submit_search_plan", {"queries": ["weather"]})
            await asyncio.wait_for(started.wait(), 1)
            return response("get_budget")

    model = Model()
    scout = VendorScout(model, ledger, "cancel", settings, client=Registry())
    tools = ToolRegistry(
        PaymentGate(ledger, "cancel", MockPaymentAdapter()), scout=scout, auto_discover=True
    )
    result = await asyncio.wait_for(Agent(model, tools, settings).run(ledger.task("cancel")), 3)
    assert result.status == "TURN_LIMIT"
    assert result.discovery["status"] == "CANCELLED"
    assert cancelled.is_set()
    assert scout.task.done()
