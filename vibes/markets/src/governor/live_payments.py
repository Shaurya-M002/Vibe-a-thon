"""Allowlisted local vendor adapter. Real Devnet USDC; no arbitrary seller signing."""

import asyncio
import json
from pathlib import Path

import httpx
from x402.http.utils import (
    decode_payment_required_header,
    decode_payment_response_header,
    encode_payment_signature_header,
)
from x402.schemas import PaymentPayload

from governor.audit import write_audit
from governor.devnet_chain import ChainError, DevnetChain
from governor.ledger import Ledger
from governor.networks import DEVNET_NETWORK, DEVNET_USDC
from governor.payments import Quote, Service, Settlement
from governor.runway import purchase_identity
from governor.solana_wallet import load_wallet
from governor.vendor.server import VENDOR_AMOUNT, VENDOR_ORIGIN, vendor_public


class DevnetPaymentAdapter:
    mode = "solana-devnet"

    def __init__(self, data_dir: Path, ledger: Ledger, session_id: str):
        self.data_dir, self.ledger, self.session_id = data_dir, ledger, session_id
        self.vendor = vendor_public(data_dir)
        self.authorization_count = 0

    def catalog(self):
        return [
            Service(
                id="vendor-summary",
                advertised_amount=VENDOR_AMOUNT,
                description="Local demo vendor: three-sentence extractive summary. "
                "Real Solana Devnet USDC payment through x402.",
            )
        ]

    async def quote(self, service_id: str, text: str) -> Quote:
        order_id = purchase_identity(self.session_id, service_id, text)
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                VENDOR_ORIGIN + "/api/summary", json={"id": order_id, "text": text}, timeout=20
            )
        if response.status_code != 402 or not response.headers.get("PAYMENT-REQUIRED"):
            raise ChainError("INVALID_X402_CHALLENGE")
        required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
        wire = required.model_dump(by_alias=True, exclude_none=True)
        if (
            wire.get("x402Version") != 2
            or len(wire.get("accepts", [])) != 1
            or wire.get("resource", {}).get("url") != VENDOR_ORIGIN + "/api/summary"
        ):
            raise ChainError("INVALID_X402_CHALLENGE")
        offer = wire["accepts"][0]
        if (
            offer.get("scheme") != "exact"
            or offer.get("network") != DEVNET_NETWORK
            or offer.get("asset") != DEVNET_USDC
            or offer.get("payTo") != self.vendor["address"]
            or offer.get("maxTimeoutSeconds") != 60
            or offer.get("extra", {}).get("memo") != "governor:" + order_id
        ):
            raise ChainError("VENDOR_TERMS_MISMATCH")
        self.ledger.record(
            self.session_id,
            "X402_CHALLENGE",
            {
                "attempt_id": order_id,
                "amount": offer["amount"],
                "pay_to": self.vendor["address"],
                "network": DEVNET_NETWORK,
                "resource": wire["resource"]["url"],
            },
        )
        return Quote(
            service_id=service_id, amount=offer["amount"], mode=self.mode, payment_required=wire
        )

    async def preflight(self, quote: Quote):
        key = load_wallet(self.data_dir / "wallets" / "solana-devnet.json")
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            chain = DevnetChain(client)
            await chain.verify_network()
            if await chain.token_balance(str(key.pubkey())) < int(quote.amount):
                raise ChainError("INSUFFICIENT_WALLET_USDC")
            await chain.token_balance(self.vendor["address"])

    def outbox(self, attempt_id: str) -> Path:
        return self.data_dir / "payments" / "reports" / (attempt_id + ".json")

    async def authorize(self, quote: Quote, attempt_id: str) -> dict:
        requirements = quote.payment_required["accepts"][0]
        if (
            requirements["amount"] != quote.amount
            or requirements["extra"]["memo"] != "governor:" + attempt_id
        ):
            raise ChainError("QUOTE_IDENTITY_MISMATCH")
        key = load_wallet(self.data_dir / "wallets" / "solana-devnet.json")
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            prepared = await DevnetChain(client).prepare(key, requirements)
        payload = PaymentPayload.model_validate(
            {
                "x402Version": 2,
                "resource": quote.payment_required["resource"],
                "accepted": requirements,
                "payload": {"transaction": prepared["transaction"]},
            }
        )
        authorization = {
            "attempt_id": attempt_id,
            "message_hash": prepared["message_hash"],
            "payer": prepared["payer"],
            "amount": quote.amount,
            "payload": payload.model_dump(by_alias=True, exclude_none=True),
        }
        # Save the signed payload privately before sending. Public audits expose only its hash.
        write_audit(self.data_dir / "payments", attempt_id, authorization)
        self.ledger.record(
            self.session_id,
            "X402_SIGNED",
            {k: authorization[k] for k in ("attempt_id", "message_hash", "payer", "amount")},
        )
        self.authorization_count += 1
        return authorization

    async def _settlement(self, client, authorization: dict, body: dict) -> Settlement:
        if (
            body.get("order_id") != authorization["attempt_id"]
            or body.get("amount") != authorization["amount"]
            or body.get("payer") != authorization["payer"]
            or body.get("pay_to") != self.vendor["address"]
        ):
            raise ChainError("RECEIPT_TERMS_MISMATCH")
        chain = DevnetChain(client)
        await chain.verify_network()
        proof = await chain.confirmed(body["proof"]["signature"], authorization["message_hash"])
        if proof is None:
            raise ChainError("RECEIPT_NOT_CONFIRMED")
        self.ledger.record(
            self.session_id,
            "X402_CONFIRMED",
            {
                "attempt_id": authorization["attempt_id"],
                "amount": authorization["amount"],
                "pay_to": self.vendor["address"],
                **proof,
            },
        )
        return Settlement(
            amount=authorization["amount"],
            receipt=proof["signature"],
            mode=self.mode,
            data={
                **body["output"],
                "proof": proof,
                "pay_to": self.vendor["address"],
                "payer": authorization["payer"],
            },
        )

    async def settle(self, authorization: dict, text: str) -> Settlement:
        payload = PaymentPayload.model_validate(authorization["payload"])
        self.ledger.record(
            self.session_id,
            "X402_SUBMITTED",
            {"attempt_id": authorization["attempt_id"], "pay_to": self.vendor["address"]},
        )
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                VENDOR_ORIGIN + "/api/summary",
                json={"id": authorization["attempt_id"], "text": text},
                headers={"PAYMENT-SIGNATURE": encode_payment_signature_header(payload)},
                timeout=65,
            )
            if response.status_code != 200:
                raise ChainError("PAYMENT_PENDING")
            receipt = decode_payment_response_header(response.headers["PAYMENT-RESPONSE"])
            body = response.json()
            if (
                not receipt.success
                or str(receipt.network) != DEVNET_NETWORK
                or receipt.transaction != body["proof"]["signature"]
            ):
                raise ChainError("INVALID_PAYMENT_RESPONSE")
            return await self._settlement(client, authorization, body)

    async def reconcile(self, attempt_id: str) -> Settlement | None:
        path = self.outbox(attempt_id)
        if not path.is_file():
            return None
        authorization = json.loads(path.read_text())
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            async with asyncio.timeout(45):
                response = await client.get(VENDOR_ORIGIN + "/api/orders/" + attempt_id, timeout=40)
                response.raise_for_status()
                body = response.json()
                if body.get("status") != "SETTLED":
                    return None
                return await self._settlement(client, authorization, body)
