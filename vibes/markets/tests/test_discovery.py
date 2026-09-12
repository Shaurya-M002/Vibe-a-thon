import asyncio
import json

import httpx
import pytest
from google.genai import types

from governor.config import Settings
from governor.discovery import (
    BAZAAR_SEARCH,
    BazaarClient,
    VendorScout,
    discovery_from_events,
    normalize,
)
from governor.ledger import Ledger
from governor.mock import MockPaymentAdapter
from governor.networks import DEVNET_NETWORK, DEVNET_USDC
from governor.payments import PaymentGate


def listing(name="summary", *, amount="1000", network=DEVNET_NETWORK, asset=DEVNET_USDC):
    return {
        "resource": f"https://vendor.example/{name}",
        "description": "Summarizes documents into concise text.",
        "x402Version": 2,
        "accepts": [{"amount": amount, "network": network, "asset": asset, "scheme": "exact"}],
        "quality": {"l30DaysTotalCalls": 30, "l30DaysUniquePayers": 4},
    }


def response(name, args):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )


class ScriptedModel:
    def __init__(self, *, queries=None, assess=None, failure=None):
        self.queries = queries or ["summarization", "document condensation"]
        self.assess = assess
        self.failure = failure
        self.calls = []

    async def generate(self, history, tools, instruction):
        payload = json.loads(history[0].parts[0].text)
        self.calls.append((tools[0].name, payload))
        if self.failure:
            raise self.failure
        if tools[0].name == "submit_search_plan":
            return response("submit_search_plan", {"queries": self.queries})
        assessments = [
            {"id": row["id"], "task_fit": 100, "reason": "Explicit summarization capability."}
            for row in payload["listings"]
        ]
        if self.assess:
            assessments = self.assess(assessments)
        return response("assess_vendors", {"assessments": assessments})


class FakeClient:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        value = self.rows.get(query, [])
        if isinstance(value, Exception):
            raise value
        return {"resources": value, "partialResults": False}


@pytest.fixture
def setup(tmp_path):
    settings = Settings(data_dir=tmp_path)
    ledger = Ledger(tmp_path / "ledger.sqlite3", settings.policy)
    ledger.start("scout", "Summarize a document")
    return settings, ledger


async def run_scout(setup, model, client):
    settings, ledger = setup
    scout = VendorScout(model, ledger, "scout", settings, client=client)
    scout.start("Find a document summarization API")
    return scout, await asyncio.wait_for(scout.result(), 3)


async def test_queries_run_concurrently_and_snapshots_persist_without_payment_authority(setup):
    settings, ledger = setup
    before = ledger.snapshot("scout")
    model = ScriptedModel()

    class ConcurrentClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.all_started = asyncio.Event()

        async def search(self, query):
            self.queries.append(query)
            if len(self.queries) == 2:
                self.all_started.set()
            await asyncio.wait_for(self.all_started.wait(), 1)
            return {"resources": [listing()], "partialResults": False}

    scout, state = await run_scout(setup, model, ConcurrentClient())
    assert state["status"] == "COMPLETED"
    assert len(state["candidates"]) == 1  # Deduplicates identical resources across branches.
    assert state["selected_id"] == state["candidates"][0]["id"]
    assert state["candidates"][0]["payment_ready"] is False
    assert ledger.snapshot("scout") == before
    persisted = Ledger(ledger.path, settings.policy).report("scout")["events"]
    assert discovery_from_events(persisted) == state
    stages = [
        e["data"]["discovery"]["status"] for e in persisted if e["kind"].startswith("DISCOVERY_")
    ]
    assert all(stage in stages for stage in ["PLANNING", "SEARCHING", "RANKING", "COMPLETED"])
    kinds = [e["kind"] for e in persisted]
    assert "RESERVED" not in kinds and "SETTLED" not in kinds
    state["candidates"].clear()
    assert len((await scout.result())["candidates"]) == 1
    adapter = MockPaymentAdapter()
    result = await PaymentGate(ledger, "scout", adapter).purchase(
        scout.state["selected_id"], "Example document"
    )
    assert result["ok"] is False
    assert adapter.authorization_count == 0
    assert ledger.snapshot("scout") == before


async def test_ranking_never_promotes_incompatible_or_unaffordable_offers(setup):
    rows = [
        listing("expensive", amount="3001"),
        listing("mainnet", network="solana:mainnet"),
        listing("wrong-token", asset="other-mint"),
        listing("eligible"),
        None,
        {"resource": "https://127.0.0.1/secret"},
        {"resource": "not a URL"},
    ]
    _, state = await run_scout(setup, ScriptedModel(), FakeClient({"summarization": rows}))
    candidates = {c["resource"].rsplit("/", 1)[-1]: c for c in state["candidates"]}
    assert set(candidates) == {"expensive", "mainnet", "wrong-token", "eligible"}
    assert all(c["task_fit"] == 100 for c in candidates.values())
    assert state["selected_id"] == candidates["eligible"]["id"]
    assert candidates["expensive"]["within_budget"] is False
    assert candidates["mainnet"]["compatible"] is False
    assert candidates["wrong-token"]["compatible"] is False


async def test_budget_is_refreshed_after_assessment(setup):
    _, ledger = setup

    def consume_budget(assessments):
        for i in range(3):
            ledger.reserve("scout", f"parent-{i}", "summary", "3000")
        return assessments

    _, state = await run_scout(
        setup,
        ScriptedModel(assess=consume_budget),
        FakeClient({"summarization": [listing(amount="2000")]}),
    )
    assert state["status"] == "NO_MATCH"
    assert state["selected_id"] is None
    assert state["budget_at_ranking"]["available"] == "1000"
    assert state["candidates"][0]["within_budget"] is False


@pytest.mark.parametrize(
    "url",
    [
        "http://vendor.example/api",
        "https://localhost/api",
        "https://127.0.0.1/api",
        "https://10.0.0.1/api",
        "https://169.254.169.254/api",
        "https://[::1]/api",
        "https://user:password@vendor.example/api",
        "https://vendor.example:444/api",
        "https://vendor.example/api?secret=token",
        "https://vendor.example/api#fragment",
        "https://service.internal/api",
        "https://service.local/api",
        "https://vendor.example/\napi",
    ],
)
def test_unsafe_listing_urls_are_rejected(url):
    row = listing()
    row["resource"] = url
    assert normalize(row, {"available": "10000"}, 3000) is None


@pytest.mark.parametrize("amount", [True, 1000, -1, "01", "1.5", "-1", "0", "1" * 1000, None])
def test_malformed_or_free_offers_cannot_become_payment_candidates(amount):
    candidate = normalize(listing(amount=amount), {"available": "10000"}, 3000)
    assert candidate["compatible"] is False
    assert candidate["within_budget"] is False


async def test_empty_registry_is_no_match_and_skips_assessment(setup):
    model = ScriptedModel()
    _, state = await run_scout(setup, model, FakeClient())
    assert state["status"] == "NO_MATCH"
    assert state["selected_id"] is None
    assert len(model.calls) == 1


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout("secret upstream URL"), ValueError("secret"), TimeoutError("secret")],
)
async def test_partial_failure_keeps_successful_search_results(setup, failure):
    client = FakeClient({"summarization": failure, "document condensation": [listing()]})
    _, state = await run_scout(setup, ScriptedModel(), client)
    assert state["status"] == "COMPLETED"
    assert state["partial_results"] is True
    assert len(state["errors"]) == 1
    assert "secret" not in json.dumps(state)


async def test_all_searches_unavailable_is_failed_not_no_match(setup):
    client = FakeClient(
        {q: httpx.ConnectError("secret") for q in ["summarization", "document condensation"]}
    )
    _, state = await run_scout(setup, ScriptedModel(), client)
    assert state["status"] == "FAILED"
    assert state["selected_id"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda rows: [{**rows[0], "id": "invented-vendor"}],
        lambda rows: rows + rows,
        lambda rows: [{**rows[0], "task_fit": "100"}],
        lambda rows: [{**rows[0], "task_fit": 101}],
    ],
)
async def test_forged_duplicate_or_malformed_assessments_cannot_select_vendor(setup, mutation):
    _, state = await run_scout(
        setup, ScriptedModel(assess=mutation), FakeClient({"summarization": [listing()]})
    )
    assert state["status"] == "NO_MATCH"
    assert state["selected_id"] is None
    assert state["candidates"][0]["task_fit"] == 0


async def test_low_task_fit_cannot_be_overruled_by_low_price_or_high_usage(setup):
    row = listing(amount="1")
    row["quality"]["l30DaysUniquePayers"] = 999999
    _, state = await run_scout(
        setup,
        ScriptedModel(assess=lambda rows: [{**r, "task_fit": 59} for r in rows]),
        FakeClient({"summarization": [row]}),
    )
    assert state["status"] == "NO_MATCH"
    assert state["selected_id"] is None


async def test_model_exceptions_are_redacted_from_state_and_events(setup):
    secret = "credential=do-not-persist-this"
    _, state = await run_scout(setup, ScriptedModel(failure=RuntimeError(secret)), FakeClient())
    assert state["status"] == "FAILED"
    assert secret not in json.dumps(state)
    assert secret not in json.dumps(setup[1].report("scout"))


async def test_cancel_awaits_every_search_branch(setup):
    entered, stopped = set(), set()
    all_started = asyncio.Event()

    class HangingClient:
        async def search(self, query):
            entered.add(query)
            if len(entered) == 2:
                all_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.add(query)

    settings, ledger = setup
    scout = VendorScout(ScriptedModel(), ledger, "scout", settings, client=HangingClient())
    scout.start("Summarization API")
    await asyncio.wait_for(all_started.wait(), 1)
    await scout.cancel()
    assert stopped == entered
    assert scout.task.done()
    assert scout.state["status"] == "CANCELLED"
    assert discovery_from_events(ledger.report("scout")["events"])["status"] == "CANCELLED"


async def test_cancel_before_scout_first_runs_persists_terminal_status(setup):
    settings, ledger = setup
    model = ScriptedModel()
    scout = VendorScout(model, ledger, "scout", settings, client=FakeClient())
    scout.start("Summarization API")
    await scout.cancel()
    assert scout.task.done()
    assert scout.state["status"] == "CANCELLED"
    assert discovery_from_events(ledger.report("scout")["events"])["status"] == "CANCELLED"
    assert model.calls == []


async def test_unexpected_search_failure_cleans_up_sibling_branches(setup):
    entered, stopped = set(), set()
    both_entered = asyncio.Event()

    class FailingClient:
        async def search(self, query):
            entered.add(query)
            if len(entered) == 2:
                both_entered.set()
            try:
                await both_entered.wait()
                if query == "summarization":
                    raise RuntimeError("secret implementation exception")
                await asyncio.Event().wait()
            finally:
                stopped.add(query)

    _, state = await run_scout(setup, ScriptedModel(), FailingClient())
    assert state["status"] == "FAILED"
    assert stopped == entered
    assert len(stopped) == 2
    assert "secret" not in json.dumps(setup[1].report("scout"))


async def test_assessment_cannot_reference_vendor_outside_supplied_shortlist(setup):
    rows = [listing(f"vendor-{i}") for i in range(13)]
    omitted = normalize(rows[11], {"available": "10000"}, 3000)["id"]

    def forged_assessment(assessments):
        assert omitted not in {a["id"] for a in assessments}
        return [assessments[0], {"id": omitted, "task_fit": 100, "reason": "Invented."}]

    _, state = await run_scout(
        setup,
        ScriptedModel(assess=forged_assessment),
        FakeClient({"summarization": rows[:12], "document condensation": [rows[-1]]}),
    )
    assert len(state["candidates"]) == 12
    assert state["partial_results"] is True
    assert state["status"] == "NO_MATCH"
    assert state["selected_id"] is None


async def test_bazaar_requests_only_fixed_registry_with_compatible_filters():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"resources": [listing()] * 20, "partialResults": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        data = await BazaarClient(http).search("https://evil.example/?token=not-authority")
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url.copy_with(query=None)) == BAZAAR_SEARCH
    assert request.url.params["network"] == DEVNET_NETWORK
    assert request.url.params["asset"] == DEVNET_USDC
    assert request.url.params["scheme"] == "exact"
    assert request.headers.get("authorization") is None
    assert len(data["resources"]) == 12
    assert data["partialResults"] is True


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(302, headers={"location": "https://evil.example/steal"}),
        httpx.Response(200, content=b"x" * 2_000_001),
        httpx.Response(200, json={"resources": "wrong shape"}),
        httpx.Response(200, content=b"not JSON"),
    ],
)
async def test_bazaar_rejects_redirects_oversize_and_malformed_responses(reply):
    requests = []

    def handler(request):
        requests.append(request)
        return reply

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises((httpx.HTTPStatusError, ValueError)):
            await BazaarClient(http).search("summary")
    assert len(requests) == 1
