import asyncio

import pytest

from governor.config import BudgetPolicy
from governor.ledger import Ledger
from governor.mock import MockPaymentAdapter
from governor.networks import DEVNET_NETWORK
from governor.payments import PaymentGate, Quote


@pytest.fixture
def gate(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3", BudgetPolicy(session_cap=5000, per_call_cap=3000))
    ledger.start("test", "Summarize")
    return PaymentGate(ledger, "test", MockPaymentAdapter())


@pytest.mark.parametrize(
    "service,code",
    [
        ("overpriced", "PER_CALL_CAP_EXCEEDED"),
        ("price-change", "PRICE_CHANGED"),
        ("unknown", "UNKNOWN_SERVICE"),
    ],
)
async def test_denial_never_reaches_authorizer(gate, service, code):
    result = await gate.purchase(service, "Example.")
    assert result["code"] == code
    assert gate.adapter.authorization_count == 0
    assert result["budget"]["held"] == "0"
    assert result["budget"]["settled"] == "0"


async def test_session_cap_counts_previous_purchases(gate):
    await gate.purchase("summary", "First.")
    await gate.purchase("summary", "Second.")
    result = await gate.purchase("summary", "Third.")
    assert result["code"] == "SESSION_CAP_EXCEEDED"
    assert gate.adapter.authorization_count == 2


async def test_duplicate_purchase_is_cached_after_restart(gate):
    original = await gate.purchase("summary", "Example.")
    restarted = PaymentGate(
        Ledger(gate.ledger.path, gate.ledger.policy), "test", MockPaymentAdapter()
    )
    replay = await restarted.purchase("summary", "Example.")
    assert replay["data"] == original["data"]
    assert replay["code"] == "ALREADY_SETTLED"
    assert restarted.adapter.authorization_count == 0


async def test_unknown_settlement_retains_hold_and_never_reauthorizes(gate):
    result = await gate.purchase("timeout", "Example.")
    assert result["code"] == "PAYMENT_UNCERTAIN"
    assert result["budget"]["held"] == "2000"
    restarted = PaymentGate(
        Ledger(gate.ledger.path, gate.ledger.policy), "test", MockPaymentAdapter()
    )
    replay = await restarted.purchase("timeout", "Example.")
    assert replay["code"] == "PAYMENT_PENDING"
    assert restarted.adapter.authorization_count == 0


async def test_invalid_challenge_never_reaches_authorizer(gate):
    async def malformed(*args):
        return Quote(service_id="summary", amount="0.01")

    gate.adapter.quote = malformed
    result = await gate.purchase("summary", "Example.")
    assert result["code"] == "INVALID_CHALLENGE"
    assert gate.adapter.authorization_count == 0


def test_quotes_only_accept_solana_devnet():
    assert Quote(service_id="summary", amount="2000").network == DEVNET_NETWORK
    for network in ("eip155:84532", "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"):
        with pytest.raises(ValueError):
            Quote(service_id="summary", amount="2000", network=network)


async def test_cancellation_after_authorization_keeps_hold(gate):
    async def cancelled(*args):
        raise asyncio.CancelledError()

    gate.adapter.settle = cancelled
    with pytest.raises(asyncio.CancelledError):
        await gate.purchase("summary", "Example.")
    assert gate.ledger.snapshot("test")["held"] == "2000"
