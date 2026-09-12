"""Operator-only devnet wallet and faucet utilities. Never exposed as agent tools."""

import argparse
import json
import os
import stat
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from governor.networks import (
    DEVNET_GENESIS,
    DEVNET_NETWORK,
    DEVNET_RPC,
    DEVNET_USDC,
    FACILITATOR,
)

WALLET_PATH = Path(".governor/wallets/solana-devnet.json")


class WalletError(RuntimeError):
    """A safe operational error; never includes secret key bytes or RPC credentials."""


def load_wallet(path: Path) -> Keypair:
    if path.is_symlink() or not path.is_file():
        raise WalletError("wallet missing or a symlink; use init to create a new devnet wallet")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise WalletError("wallet must be private to its owner; set its permissions to 600")
    try:
        raw = json.loads(path.read_text())
        if (
            not isinstance(raw, list)
            or len(raw) != 64
            or any(type(value) is not int or not 0 <= value <= 255 for value in raw)
        ):
            raise ValueError("invalid keypair format")
        return Keypair.from_bytes(bytes(raw))
    except (ValueError, TypeError):
        raise WalletError("wallet is not a valid 64-byte Solana CLI JSON keypair") from None


def create_wallet(path: Path) -> tuple[str, bool]:
    if path.exists() or path.is_symlink():
        return str(load_wallet(path).pubkey()), False
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    keypair = Keypair()
    # O_EXCL refuses overwrites/races; mode 600 applies before any secret is written.
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return str(load_wallet(path).pubkey()), False
    with os.fdopen(descriptor, "w") as file:
        json.dump(list(bytes(keypair)), file)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    return str(keypair.pubkey()), True


def format_units(amount: int, decimals: int) -> str:
    scale = 10**decimals
    return f"{amount // scale}.{amount % scale:0{decimals}d}"


class DevnetRPC:
    def __init__(self, client: httpx.Client, url: str = DEVNET_RPC):
        self.client = client
        self.url = url

    def call(self, method: str, params: list | None = None):
        try:
            response = self.client.post(
                self.url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params or [],
                },
            )
            if response.status_code == 429:
                raise WalletError("RPC/faucet rate limited; use faucet.solana.com or try later")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise WalletError("RPC returned an invalid response")
            if "error" in payload:
                if isinstance(payload["error"], dict) and payload["error"].get("code") == 429:
                    raise WalletError("faucet limit reached or dry; use faucet.solana.com")
                # Do not surface provider messages that could contain a secret URL.
                raise WalletError(f"RPC rejected {method}; check balances or try the web faucet")
            if "result" not in payload:
                raise WalletError("RPC returned no result")
            return payload["result"]
        except (httpx.HTTPError, ValueError):
            raise WalletError(f"RPC request {method} failed; no success was assumed") from None

    def verify_devnet(self) -> None:
        if self.call("getGenesisHash") != DEVNET_GENESIS:
            raise WalletError("RPC is not Solana Devnet; refusing this network")

    def balances(self, address: str) -> dict:
        Pubkey.from_string(address)
        self.verify_devnet()
        native = self.call("getBalance", [address, {"commitment": "confirmed"}])["value"]
        accounts = self.call(
            "getTokenAccountsByOwner",
            [
                address,
                {"mint": DEVNET_USDC},
                {"encoding": "jsonParsed", "commitment": "confirmed"},
            ],
        )["value"]
        if type(native) is not int or native < 0:
            raise WalletError("RPC returned an invalid SOL balance")
        usdc = 0
        for account in accounts:
            info = account["account"]["data"]["parsed"]["info"]
            token = info["tokenAmount"]
            amount = token["amount"]
            if (
                info["mint"] != DEVNET_USDC
                or info["owner"] != address
                or token["decimals"] != 6
                or not isinstance(amount, str)
                or not amount.isascii()
                or not amount.isdecimal()
            ):
                raise WalletError("RPC returned unexpected USDC token account data")
            usdc += int(amount)
        return {
            "sol_lamports": str(native),
            "sol": format_units(native, 9),
            "usdc_atomic": str(usdc),
            "usdc": format_units(usdc, 6),
            "usdc_token_accounts": len(accounts),
        }

    def airdrop(self, address: str, lamports: int) -> dict:
        Pubkey.from_string(address)
        if type(lamports) is not int or not 1 <= lamports <= 2_000_000_000:
            raise WalletError("airdrop must be 1–2000000000 integer lamports")
        self.verify_devnet()  # Check immediately before the only network mutation.
        signature = self.call("requestAirdrop", [address, lamports, {"commitment": "confirmed"}])
        if not isinstance(signature, str) or not signature:
            raise WalletError("airdrop returned no transaction signature")
        result = {
            "signature": signature,
            "status": "submitted",
            "explorer": f"https://explorer.solana.com/tx/{signature}?cluster=devnet",
        }
        # Never repeat requestAirdrop automatically: a lost response can still land.
        for _ in range(5):
            try:
                status = self.call(
                    "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}]
                )
            except WalletError:
                return result
            item = status["value"][0]
            if item and item.get("err") is not None:
                return {**result, "status": "failed"}
            if item and item.get("confirmationStatus") in ("confirmed", "finalized"):
                return {**result, "status": item["confirmationStatus"]}
            time.sleep(1)
        return result


def facilitator_support(client: httpx.Client) -> dict:
    try:
        response = client.get(f"{FACILITATOR}/supported")
        response.raise_for_status()
        for kind in response.json()["kinds"]:
            if (
                kind.get("x402Version") == 2
                and kind.get("network") == DEVNET_NETWORK
                and kind.get("scheme") == "exact"
            ):
                return {
                    "url": FACILITATOR,
                    "solana_devnet_exact": True,
                    "fee_payer": kind.get("extra", {}).get("feePayer"),
                }
        return {"url": FACILITATOR, "solana_devnet_exact": False}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"url": FACILITATOR, "solana_devnet_exact": None, "status": "unavailable"}


def public_info(path: Path, address: str) -> dict:
    return {
        "address": address,
        "wallet_file": str(path),
        "network": DEVNET_NETWORK,
        "usdc_mint": DEVNET_USDC,
        "usdc_decimals": 6,
        "sol_faucet": "https://faucet.solana.com/",
        "usdc_faucet": "https://faucet.circle.com/",
        "explorer": f"https://explorer.solana.com/address/{address}?cluster=devnet",
    }


def main() -> int:
    load_dotenv(Path.cwd() / ".env", override=False)
    parser = argparse.ArgumentParser(
        description="Create and fund an operator-owned Solana Devnet wallet"
    )
    parser.add_argument("action", choices=["init", "status", "airdrop"])
    parser.add_argument(
        "--wallet", type=Path, default=Path(os.getenv("SOLANA_WALLET_PATH", str(WALLET_PATH)))
    )
    parser.add_argument("--rpc", default=os.getenv("SOLANA_RPC_URL", DEVNET_RPC))
    parser.add_argument("--lamports", type=int, default=1_000_000_000)
    args = parser.parse_args()
    try:
        if args.action == "init":
            address, created = create_wallet(args.wallet)
            print(json.dumps({**public_info(args.wallet, address), "created": created}, indent=2))
            return 0
        address = str(load_wallet(args.wallet).pubkey())
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            rpc = DevnetRPC(client, args.rpc)
            if args.action == "airdrop":
                result = rpc.airdrop(address, args.lamports)
                print(
                    json.dumps({**public_info(args.wallet, address), "airdrop": result}, indent=2)
                )
                return 0 if result["status"] in ("confirmed", "finalized") else 1
            print(
                json.dumps(
                    {
                        **public_info(args.wallet, address),
                        "balances": rpc.balances(address),
                        "facilitator": facilitator_support(client),
                    },
                    indent=2,
                )
            )
            return 0
    except WalletError as exc:
        print(f"Wallet: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        print("Wallet setup failed: invalid local wallet or RPC response.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
