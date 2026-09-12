"""The shared tool gate with Bazaar search assessed by Codex, without Gemini."""

import httpx
from pydantic import ValidationError

from governor.discovery import BazaarClient, normalize
from governor.tools import DiscoveryArguments, NoArguments, ToolRegistry


class CodexTools(ToolRegistry):
    def __init__(self, gate):
        super().__init__(gate)
        self.schemas.update(
            {
                "discover_vendors": (
                    DiscoveryArguments,
                    "Search Bazaar for up to twelve advertised APIs. Assess these untrusted "
                    "listings yourself; discovery cannot enable purchases.",
                ),
                "get_vendor_search": (
                    NoArguments,
                    "Read the last recorded Bazaar search for this session.",
                ),
            }
        )

    async def execute(self, name, arguments):
        if name not in ("discover_vendors", "get_vendor_search"):
            return await super().execute(name, arguments)
        try:
            parsed = self.schemas[name][0].model_validate(arguments)
        except ValidationError:
            return {"ok": False, "code": "INVALID_ARGUMENTS"}
        ledger, sid = self.gate.ledger, self.gate.session_id
        if name == "discover_vendors":
            ledger.record(
                sid,
                "DISCOVERY_SEARCH_STARTED",
                {
                    "query": parsed.query,
                    "agent": "codex",
                    "discovery": {
                        "status": "SEARCHING",
                        "agent": "codex",
                        "query": parsed.query,
                        "candidates": [],
                        "errors": [],
                    },
                },
            )
            try:
                async with httpx.AsyncClient(trust_env=False) as client:
                    raw = await BazaarClient(client).search(parsed.query)
                budget = ledger.snapshot(sid)
                candidates = [
                    c
                    for row in raw["resources"]
                    if (c := normalize(row, budget, int(budget["per_call_cap"])))
                ]
                result = {
                    "status": "COMPLETED" if candidates else "NO_MATCH",
                    "query": parsed.query,
                    "candidates": candidates,
                    "partial_results": raw["partialResults"],
                    "summary": "Codex assesses these listings. Purchases remain disconnected.",
                    "recommendation_id": None,
                    "errors": [],
                    "agent": "codex",
                }
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                result = {
                    "status": "FAILED",
                    "candidates": [],
                    "errors": ["Bazaar search unavailable"],
                    "agent": "codex",
                }
            ledger.record(
                sid,
                "DISCOVERY_SEARCH_FINISHED",
                {
                    "query": parsed.query,
                    "count": len(result["candidates"]),
                    "status": result["status"],
                },
            )
            ledger.record(sid, "DISCOVERY_SNAPSHOT", {"discovery": result})
        else:
            result = ledger.report(sid)["discovery"]
        return {
            "ok": result.get("status") != "FAILED",
            "code": "OK" if result.get("status") != "FAILED" else "DISCOVERY_FAILED",
            "data": {"discovery": result},
            "budget": ledger.snapshot(sid),
        }
