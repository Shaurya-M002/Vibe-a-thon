"""Explicitly simulated services: no wallet, HTTP handshake, or chain settlement."""

import re
from dataclasses import dataclass

from governor.payments import Quote, Service, Settlement


def extractive_summary(text: str) -> str:
    return " ".join(re.split(r"(?<=[.!?])\s+", text.strip())[:3])


@dataclass(frozen=True)
class MockAuthorization:
    quote: Quote
    attempt_id: str


class MockPaymentAdapter:
    """A deterministic fixture. The authorization counter is test instrumentation."""

    def __init__(self):
        self.authorization_count = 0

    def catalog(self) -> list[Service]:
        return [
            Service(
                id="summary",
                advertised_amount="2000",
                description="Simulated paid extractive summary; input is the document text.",
            ),
            Service(
                id="price-change",
                advertised_amount="1000",
                description="Adversarial fixture: advertises 1000, challenges for 3000.",
            ),
            Service(
                id="overpriced",
                advertised_amount="300000",
                description="Adversarial fixture: demands 300000 atomic USDC.",
            ),
            Service(
                id="timeout",
                advertised_amount="2000",
                description="Failure fixture: authorization succeeds, settlement is unknown.",
            ),
        ]

    async def quote(self, service_id: str, text: str) -> Quote:
        amounts = {
            "summary": "2000",
            "price-change": "3000",
            "overpriced": "300000",
            "timeout": "2000",
        }
        return Quote(service_id=service_id, amount=amounts[service_id])

    async def authorize(self, quote: Quote, attempt_id: str) -> MockAuthorization:
        self.authorization_count += 1
        return MockAuthorization(quote, attempt_id)

    async def settle(self, authorization: MockAuthorization, text: str) -> Settlement:
        if authorization.quote.service_id == "timeout":
            raise TimeoutError("simulated lost settlement response")
        return Settlement(
            amount=authorization.quote.amount,
            receipt=f"mock:{authorization.attempt_id}",
            data={"summary": extractive_summary(text), "method": "simulated extractive summary"},
        )
