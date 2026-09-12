"""Narrow x402 exact-SVM transaction construction and independent chain evidence.

Only Circle USDC on Solana Devnet is supported. No arbitrary transactions or
seller-provided instructions are signed. The facilitator sponsors network fees.
"""

import base64
import hashlib

import httpx
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from governor.config import atomic
from governor.networks import DEVNET_GENESIS, DEVNET_NETWORK, DEVNET_RPC, DEVNET_USDC, FACILITATOR

TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
MEMO = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")


class ChainError(ValueError):
    """Safe error code, never a raw provider message or signed payload."""


def ata(owner: str) -> Pubkey:
    return Pubkey.find_program_address(
        [bytes(Pubkey.from_string(owner)), bytes(TOKEN), bytes(Pubkey.from_string(DEVNET_USDC))],
        ATA_PROGRAM,
    )[0]


def payment_message(
    payer: str, receiver: str, amount: str, fee_payer: str, memo: str, blockhash: Hash
) -> MessageV0:
    units = atomic(amount)
    if units <= 0 or payer == fee_payer or not memo or len(memo.encode()) > 256:
        raise ChainError("INVALID_PAYMENT_TERMS")
    transfer = Instruction(
        TOKEN,
        bytes([12]) + units.to_bytes(8, "little") + bytes([6]),
        [
            AccountMeta(ata(payer), False, True),
            AccountMeta(Pubkey.from_string(DEVNET_USDC), False, False),
            AccountMeta(ata(receiver), False, True),
            AccountMeta(Pubkey.from_string(payer), True, False),
        ],
    )
    return MessageV0.try_compile(
        payer=Pubkey.from_string(fee_payer),
        instructions=[
            # The 73-byte invoice memo exceeds the SDK's 20k default with TransferChecked.
            # Keep a fixed, bounded allowance for the complete transfer + memo execution.
            set_compute_unit_limit(40000),
            set_compute_unit_price(1),
            transfer,
            Instruction(MEMO, memo.encode(), []),
        ],
        address_lookup_table_accounts=[],
        recent_blockhash=blockhash,
    )


def message_hash(tx: VersionedTransaction) -> str:
    return hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest()


def inspect_payment(encoded: str, requirements: dict) -> tuple[str, str]:
    """Validate the exact locally supported layout and buyer signature, before forwarding."""
    if not isinstance(encoded, str) or len(encoded) > 3000:
        raise ChainError("INVALID_TRANSACTION")
    tx = VersionedTransaction.from_bytes(base64.b64decode(encoded, validate=True))
    if (
        not isinstance(tx.message, MessageV0)
        or len(tx.signatures) != 2
        or tx.message.header.num_required_signatures != 2
    ):
        raise ChainError("INVALID_SIGNERS")
    if (
        requirements.get("network") != DEVNET_NETWORK
        or requirements.get("asset") != DEVNET_USDC
        or requirements.get("scheme") != "exact"
    ):
        raise ChainError("INVALID_PAYMENT_TERMS")
    payer = str(tx.message.account_keys[1])
    expected = payment_message(
        payer,
        requirements["payTo"],
        requirements["amount"],
        requirements["extra"]["feePayer"],
        requirements["extra"]["memo"],
        tx.message.recent_blockhash,
    )
    if (
        to_bytes_versioned(expected) != to_bytes_versioned(tx.message)
        or not tx.verify_with_results()[1]
    ):
        raise ChainError("TRANSACTION_TERMS_MISMATCH")
    return payer, message_hash(tx)


class DevnetChain:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def rpc(self, method: str, params: list | None = None):
        response = await self.client.post(
            DEVNET_RPC,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []},
            timeout=12,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or "error" in data or "result" not in data:
            raise ChainError("RPC_UNAVAILABLE")
        return data["result"]

    async def verify_network(self):
        if await self.rpc("getGenesisHash") != DEVNET_GENESIS:
            raise ChainError("WRONG_NETWORK")

    async def fee_payer(self) -> str:
        response = await self.client.get(FACILITATOR + "/supported", timeout=12)
        response.raise_for_status()
        for kind in response.json().get("kinds", []):
            if (
                kind.get("x402Version") == 2
                and kind.get("network") == DEVNET_NETWORK
                and kind.get("scheme") == "exact"
            ):
                return str(Pubkey.from_string(kind["extra"]["feePayer"]))
        raise ChainError("FACILITATOR_UNAVAILABLE")

    async def token_balance(self, owner: str) -> int:
        value = (
            await self.rpc(
                "getAccountInfo",
                [str(ata(owner)), {"encoding": "jsonParsed", "commitment": "confirmed"}],
            )
        )["value"]
        if value is None:
            raise ChainError("USDC_ACCOUNT_NOT_INITIALIZED")
        info = value["data"]["parsed"]["info"]
        if (
            value["owner"] != str(TOKEN)
            or info["mint"] != DEVNET_USDC
            or info["owner"] != owner
            or info["tokenAmount"]["decimals"] != 6
        ):
            raise ChainError("INVALID_TOKEN_ACCOUNT")
        return atomic(info["tokenAmount"]["amount"])

    async def prepare(self, keypair: Keypair, requirements: dict) -> dict:
        await self.verify_network()
        if requirements["extra"]["feePayer"] != await self.fee_payer():
            raise ChainError("FEE_PAYER_CHANGED")
        payer = str(keypair.pubkey())
        if await self.token_balance(payer) < atomic(requirements["amount"]):
            raise ChainError("INSUFFICIENT_WALLET_USDC")
        await self.token_balance(requirements["payTo"])
        blockhash = (await self.rpc("getLatestBlockhash", [{"commitment": "confirmed"}]))["value"][
            "blockhash"
        ]
        message = payment_message(
            payer,
            requirements["payTo"],
            requirements["amount"],
            requirements["extra"]["feePayer"],
            requirements["extra"]["memo"],
            Hash.from_string(blockhash),
        )
        if str(message.account_keys[1]) != payer:
            raise ChainError("INVALID_SIGNER_ORDER")
        tx = VersionedTransaction.populate(
            message, [Signature.default(), keypair.sign_message(to_bytes_versioned(message))]
        )
        encoded = base64.b64encode(bytes(tx)).decode()
        inspect_payment(encoded, requirements)
        return {"transaction": encoded, "message_hash": message_hash(tx), "payer": payer}

    async def confirmed(self, signature: str, expected_hash: str) -> dict | None:
        Signature.from_string(signature)
        transaction = await self.rpc(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "base64",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        if transaction is None:
            return None
        if transaction["meta"]["err"] is not None:
            raise ChainError("ON_CHAIN_TRANSACTION_FAILED")
        tx = VersionedTransaction.from_bytes(
            base64.b64decode(transaction["transaction"][0], validate=True)
        )
        if (
            str(tx.signatures[0]) != signature
            or message_hash(tx) != expected_hash
            or not all(tx.verify_with_results())
        ):
            raise ChainError("RECEIPT_TRANSACTION_MISMATCH")
        return {
            "signature": signature,
            "slot": transaction["slot"],
            "confirmation": "confirmed",
            "network": DEVNET_NETWORK,
            "explorer": f"https://explorer.solana.com/tx/{signature}?cluster=devnet",
        }

    async def find_confirmed(self, receiver: str, expected_hash: str, memo: str) -> dict | None:
        await self.verify_network()
        recent = await self.rpc(
            "getSignaturesForAddress",
            [str(ata(receiver)), {"limit": 20, "commitment": "confirmed"}],
        )
        for entry in recent:
            if entry.get("err") is None and memo in (entry.get("memo") or ""):
                try:
                    proof = await self.confirmed(entry["signature"], expected_hash)
                    if proof:
                        return proof
                except ChainError as exc:
                    if str(exc) != "RECEIPT_TRANSACTION_MISMATCH":
                        raise
        return None
