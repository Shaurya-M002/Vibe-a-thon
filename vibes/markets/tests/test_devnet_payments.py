"""Exercise the full 402→gate→signature→merchant→confirmation path without a network."""

import base64
import json
import stat
from types import SimpleNamespace

import httpx
import pytest
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

from governor.config import BudgetPolicy
from governor.devnet_chain import TOKEN, ata, inspect_payment, message_hash
from governor.ledger import Ledger, LedgerError
from governor.live_payments import DevnetPaymentAdapter
from governor.networks import DEVNET_GENESIS, DEVNET_NETWORK, DEVNET_RPC, DEVNET_USDC, FACILITATOR
from governor.payments import PaymentGate
from governor.solana_wallet import create_wallet
from governor.vendor.server import VENDOR_ORIGIN, Vendor


@pytest.fixture
def env(tmp_path, monkeypatch):
    buyer, _ = create_wallet(tmp_path / "wallets" / "solana-devnet.json")
    vendor = Vendor(tmp_path)
    fee = Keypair()
    data = SimpleNamespace(
        fee=fee,
        buyer=buyer,
        vendor=vendor,
        paid_tx=None,
        verifies=0,
        settles=0,
        lost=False,
        quote_amount=None,
        account_missing=False,
        wrong_genesis=False,
        calls=[],
    )

    async def transport(request):
        url = str(request.url)
        body = json.loads(request.content) if request.content else None
        data.calls.append((request.method, url))
        if url == FACILITATOR + "/supported":
            return httpx.Response(
                200,
                json={
                    "kinds": [
                        {
                            "x402Version": 2,
                            "scheme": "exact",
                            "network": DEVNET_NETWORK,
                            "extra": {"feePayer": str(fee.pubkey())},
                        }
                    ]
                },
            )
        if url == FACILITATOR + "/verify":
            data.verifies += 1
            inspect_payment(
                body["paymentPayload"]["payload"]["transaction"], body["paymentRequirements"]
            )
            return httpx.Response(200, json={"isValid": True})
        if url == FACILITATOR + "/settle":
            data.settles += 1
            tx = VersionedTransaction.from_bytes(
                base64.b64decode(body["paymentPayload"]["payload"]["transaction"])
            )
            data.paid_tx = VersionedTransaction.populate(
                tx.message, [fee.sign_message(to_bytes_versioned(tx.message)), tx.signatures[1]]
            )
            data.memo = body["paymentRequirements"]["extra"]["memo"]
            if data.lost:
                raise httpx.ReadTimeout("private-provider-diagnostic")
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "network": DEVNET_NETWORK,
                    "transaction": str(data.paid_tx.signatures[0]),
                },
            )
        if url == DEVNET_RPC:
            method, params = body["method"], body["params"]
            if method == "getGenesisHash":
                result = "wrong-network" if data.wrong_genesis else DEVNET_GENESIS
            elif method == "getAccountInfo":
                owner = buyer if params[0] == str(ata(buyer)) else vendor.address
                result = {
                    "value": None
                    if data.account_missing
                    else {
                        "owner": str(TOKEN),
                        "data": {
                            "parsed": {
                                "info": {
                                    "mint": DEVNET_USDC,
                                    "owner": owner,
                                    "tokenAmount": {"decimals": 6, "amount": "20000000"},
                                }
                            }
                        },
                    }
                }
            elif method == "getLatestBlockhash":
                result = {"value": {"blockhash": str(Hash.default())}}
            elif method == "getTransaction":
                result = (
                    None
                    if not data.paid_tx
                    else {
                        "meta": {"err": None},
                        "slot": 123,
                        "transaction": [base64.b64encode(bytes(data.paid_tx)).decode(), "base64"],
                    }
                )
            elif method == "getSignaturesForAddress":
                result = (
                    []
                    if not data.paid_tx
                    else [
                        {
                            "err": None,
                            "memo": data.memo,
                            "signature": str(data.paid_tx.signatures[0]),
                        }
                    ]
                )
            else:
                raise AssertionError("Unexpected RPC " + method)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})
        if url == VENDOR_ORIGIN + "/api/summary":
            status, result, headers = await vendor.handle(
                body, request.headers.get("PAYMENT-SIGNATURE")
            )
            if data.quote_amount and status == 402:
                from x402.http.utils import encode_payment_required_header
                from x402.schemas import PaymentRequired

                result["accepts"][0]["amount"] = data.quote_amount
                headers["PAYMENT-REQUIRED"] = encode_payment_required_header(
                    PaymentRequired.model_validate(result)
                )
            return httpx.Response(status, json=result, headers=headers)
        if url.startswith(VENDOR_ORIGIN + "/api/orders/"):
            return httpx.Response(200, json=await vendor.reconcile(url.rsplit("/", 1)[-1]))
        raise AssertionError("Unexpected URL " + url)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(transport)),
    )
    policy = BudgetPolicy()
    ledger = Ledger(tmp_path / "ledger.sqlite3", policy)
    ledger.start("live", "Buy a summary", payment_mode="solana-devnet")
    adapter = DevnetPaymentAdapter(tmp_path, ledger, "live")
    data.ledger, data.adapter, data.gate = ledger, adapter, PaymentGate(ledger, "live", adapter)
    return data


async def test_complete_x402_flow_deducts_ledger_and_credits_confirmed_vendor_revenue(env):
    result = await env.gate.purchase("vendor-summary", "First sentence. Second sentence.")
    assert result["code"] == "SETTLED"
    assert result["data"]["simulation"] is False
    assert result["budget"]["payment_mode"] == "solana-devnet"
    assert result["budget"]["settled"] == "2000"
    assert result["budget"]["available"] == "8000"
    assert env.vendor.state()["revenue_atomic"] == "2000"
    assert env.vendor.state()["settled_orders"] == 1
    assert env.verifies == env.settles == env.adapter.authorization_count == 1
    assert all(env.paid_tx.verify_with_results())
    attempt = result["data"]["attempt_id"]
    saved = json.loads(env.adapter.outbox(attempt).read_text())
    assert saved["message_hash"] == message_hash(env.paid_tx)
    assert stat.S_IMODE(env.adapter.outbox(attempt).stat().st_mode) == 0o600
    assert saved["payload"]["payload"]["transaction"] not in json.dumps(env.ledger.report("live"))
    again = await env.gate.purchase("vendor-summary", "First sentence. Second sentence.")
    assert again["code"] == "ALREADY_SETTLED"
    assert env.settles == 1


async def test_lost_settlement_reply_retains_hold_then_recovers_without_resending(env):
    env.lost = True
    result = await env.gate.purchase("vendor-summary", "Example.")
    assert result["code"] == "PAYMENT_UNCERTAIN"
    assert result["budget"]["held"] == "2000"
    assert result["budget"]["settled"] == "0"
    assert env.vendor.state()["revenue_atomic"] == "0"
    recovered = await env.gate.reconcile(result["data"]["attempt_id"])
    assert recovered["code"] == "SETTLED"
    assert recovered["budget"]["held"] == "0"
    assert env.vendor.state()["revenue_atomic"] == "2000"
    assert env.settles == env.adapter.authorization_count == 1
    assert "private-provider-diagnostic" not in json.dumps(env.ledger.report("live"))


@pytest.mark.parametrize(
    "amount,code", [("3001", "PER_CALL_CAP_EXCEEDED"), ("2500", "PRICE_CHANGED")]
)
async def test_caps_and_quote_ceiling_apply_before_real_signing(env, amount, code):
    env.quote_amount = amount
    result = await env.gate.purchase("vendor-summary", "Example.")
    assert result["code"] == code
    assert result["budget"]["available"] == "10000"
    assert env.adapter.authorization_count == env.settles == env.verifies == 0


@pytest.mark.parametrize("failure", ["account_missing", "wrong_genesis"])
async def test_preflight_failure_releases_unsigned_hold(env, failure):
    setattr(env, failure, True)
    result = await env.gate.purchase("vendor-summary", "Example.")
    assert result["code"] == "PAYMENT_SETUP_REQUIRED"
    assert result["budget"]["held"] == "0"
    assert env.adapter.authorization_count == env.settles == 0


async def test_bazaar_id_cannot_enter_live_signer(env):
    result = await env.gate.purchase("bazaar-attacker", "Example.")
    assert result["code"] == "UNKNOWN_SERVICE"
    assert env.calls == []


async def test_invoice_replay_cannot_change_input_or_create_second_transfer(env):
    result = await env.gate.purchase("vendor-summary", "Original.")
    attempt = result["data"]["attempt_id"]
    status, _, _ = await env.vendor.handle({"id": attempt, "text": "Changed."}, None)
    assert status == 409
    from x402.http.utils import encode_payment_signature_header
    from x402.schemas import PaymentPayload

    saved = json.loads(env.adapter.outbox(attempt).read_text())
    header = encode_payment_signature_header(PaymentPayload.model_validate(saved["payload"]))
    status, body, _ = await env.vendor.handle({"id": attempt, "text": "Original."}, header)
    assert status == 200
    assert body["proof"]["signature"] == result["data"]["receipt"]
    assert env.settles == 1


async def test_transaction_cannot_be_reused_for_different_recipient_amount_or_order(env):
    result = await env.gate.purchase("vendor-summary", "Example.")
    saved = json.loads(env.adapter.outbox(result["data"]["attempt_id"]).read_text())
    encoded, requirements = saved["payload"]["payload"]["transaction"], saved["payload"]["accepted"]
    for changed in (
        {**requirements, "payTo": str(Keypair().pubkey())},
        {**requirements, "amount": "2001"},
        {**requirements, "asset": str(Keypair().pubkey())},
        {**requirements, "extra": {**requirements["extra"], "memo": "another-order"}},
    ):
        with pytest.raises(ValueError):
            inspect_payment(encoded, changed)


def test_session_mode_cannot_be_changed_or_used_with_wrong_adapter(env):
    with pytest.raises(LedgerError, match="payment mode"):
        env.ledger.start("live", "Buy a summary", resume=True, payment_mode="mock")
    env.ledger.start("mock-session", "Mock")
    with pytest.raises(LedgerError, match="payment mode"):
        PaymentGate(env.ledger, "mock-session", env.adapter)
