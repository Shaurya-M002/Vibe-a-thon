"""Loopback x402 merchant with persistent invoices and a separate seller console."""

import argparse
import asyncio
import hashlib
import json
import mimetypes
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from solders.pubkey import Pubkey
from x402.http.utils import (
    decode_payment_signature_header,
    encode_payment_required_header,
    encode_payment_response_header,
)
from x402.schemas import PaymentRequired, SettleResponse

from governor.devnet_chain import ChainError, DevnetChain, inspect_payment
from governor.mock import extractive_summary
from governor.networks import DEVNET_NETWORK, DEVNET_USDC, FACILITATOR
from governor.solana_wallet import DevnetRPC, create_wallet

VENDOR_ORIGIN = "http://127.0.0.1:8788"
VENDOR_AMOUNT = "2000"
STATIC = Path(__file__).with_name("static")


def vendor_public(data_dir: Path) -> dict:
    data = json.loads((data_dir / "vendor" / "public.json").read_text())
    if not isinstance(data, dict):
        raise ValueError("Invalid local vendor configuration")
    if (
        data.get("network") != DEVNET_NETWORK
        or data.get("amount") != VENDOR_AMOUNT
        or data.get("origin") != VENDOR_ORIGIN
    ):
        raise ValueError("Invalid local vendor configuration")
    Pubkey.from_string(data["address"])
    return data


class Vendor:
    def __init__(self, data_dir: Path):
        self.directory = data_dir / "vendor"
        address, _ = create_wallet(self.directory / "wallet.json")
        self.address = address
        self.db_path = self.directory / "orders.sqlite3"
        public = {
            "address": address,
            "network": DEVNET_NETWORK,
            "origin": VENDOR_ORIGIN,
            "amount": VENDOR_AMOUNT,
            "asset": DEVNET_USDC,
        }
        path = self.directory / "public.json"
        if path.exists() and json.loads(path.read_text()) != public:
            raise ValueError("Vendor identity differs from persisted configuration")
        if not path.exists():
            path.write_text(json.dumps(public, indent=2) + "\n")
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, amount TEXT NOT NULL,
                status TEXT NOT NULL, payer TEXT, message_hash TEXT UNIQUE,
                requirements TEXT, proof TEXT NOT NULL DEFAULT '{}',
                output TEXT NOT NULL, created_at TEXT NOT NULL)""")
        self.db_path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def order(self, order_id: str) -> dict | None:
        with self.db() as db:
            row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if row is None:
            return None
        return {
            **dict(row),
            "proof": json.loads(row["proof"]),
            "output": json.loads(row["output"]),
            "requirements": json.loads(row["requirements"]) if row["requirements"] else None,
        }

    def state(self) -> dict:
        with self.db() as db:
            rows = db.execute(
                "SELECT id,amount,status,payer,proof,created_at FROM orders "
                "ORDER BY rowid DESC LIMIT 100"
            ).fetchall()
            settled = db.execute("SELECT amount FROM orders WHERE status='SETTLED'").fetchall()
        return {
            "name": "Governor Demo Vendor",
            "address": self.address,
            "network": "Solana Devnet",
            "asset": DEVNET_USDC,
            "amount": VENDOR_AMOUNT,
            "service_id": "vendor-summary",
            "service": "Extractive summary",
            "revenue_atomic": str(sum(int(row["amount"]) for row in settled)),
            "settled_orders": len(settled),
            "orders": [{**dict(row), "proof": json.loads(row["proof"])} for row in rows],
        }

    async def requirements(self, order_id: str) -> dict:
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            fee_payer = await DevnetChain(client).fee_payer()
        return {
            "scheme": "exact",
            "network": DEVNET_NETWORK,
            "asset": DEVNET_USDC,
            "amount": VENDOR_AMOUNT,
            "payTo": self.address,
            "maxTimeoutSeconds": 60,
            "extra": {"feePayer": fee_payer, "memo": "governor:" + order_id},
        }

    async def handle(self, body: dict, payment: str | None) -> tuple[int, dict, dict]:
        if not isinstance(body, dict) or set(body) != {"id", "text"}:
            raise ValueError("Expected id and text")
        order_id, text = body["id"], body["text"]
        if (
            not isinstance(order_id, str)
            or not re.fullmatch(r"[a-f0-9]{64}", order_id)
            or not isinstance(text, str)
            or not 1 <= len(text) <= 12000
        ):
            raise ValueError("Invalid order")
        digest = hashlib.sha256(text.encode()).hexdigest()
        output = {"summary": extractive_summary(text), "method": "local extractive demo service"}
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO orders(id,input_hash,amount,status,output,created_at) "
                "VALUES (?,?,?,'QUOTED',?,?)",
                (
                    order_id,
                    digest,
                    VENDOR_AMOUNT,
                    json.dumps(output),
                    datetime.now(UTC).isoformat(),
                ),
            )
        order = self.order(order_id)
        if order["input_hash"] != digest:
            return 409, {"error": "ORDER_INPUT_MISMATCH"}, {}
        if not payment:
            if order["requirements"] is None:
                requirements = await self.requirements(order_id)
                with self.db() as db:
                    db.execute(
                        "UPDATE orders SET requirements=? WHERE id=? AND requirements IS NULL",
                        (json.dumps(requirements), order_id),
                    )
                order = self.order(order_id)
            required = PaymentRequired.model_validate(
                {
                    "x402Version": 2,
                    "resource": {
                        "url": VENDOR_ORIGIN + "/api/summary",
                        "mimeType": "application/json",
                        "description": "Three-sentence extractive summary from a local demo vendor",
                    },
                    "accepts": [order["requirements"]],
                }
            )
            return (
                402,
                required.model_dump(by_alias=True, exclude_none=True),
                {"PAYMENT-REQUIRED": encode_payment_required_header(required)},
            )
        payload = decode_payment_signature_header(payment)
        wire = payload.model_dump(by_alias=True, exclude_none=True)
        if (
            wire.get("x402Version") != 2
            or wire.get("accepted") != order["requirements"]
            or (wire.get("resource") or {}).get("url") != VENDOR_ORIGIN + "/api/summary"
        ):
            return 400, {"error": "PAYMENT_TERMS_MISMATCH"}, {}
        payer, fingerprint = inspect_payment(wire["payload"]["transaction"], order["requirements"])
        if order["message_hash"] and order["message_hash"] != fingerprint:
            return 409, {"error": "ORDER_ALREADY_HAS_PAYMENT"}, {}
        if order["status"] == "SETTLED":
            return self.receipt(order)
        with self.db() as db:
            changed = db.execute(
                "UPDATE orders SET status='SETTLING',payer=?,message_hash=? "
                "WHERE id=? AND status='QUOTED'",
                (payer, fingerprint, order_id),
            )
            if changed.rowcount != 1:
                return 409, {"error": "PAYMENT_PENDING", "order_id": order_id}, {}
        try:
            async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
                request = {
                    "x402Version": 2,
                    "paymentPayload": wire,
                    "paymentRequirements": order["requirements"],
                }
                verified = await client.post(FACILITATOR + "/verify", json=request, timeout=20)
                verified.raise_for_status()
                if verified.json().get("isValid") is not True:
                    raise ChainError("FACILITATOR_REFUSED")
                settled = await client.post(FACILITATOR + "/settle", json=request, timeout=35)
                settled.raise_for_status()
                data = settled.json()
                if data.get("success") is not True or data.get("network") != DEVNET_NETWORK:
                    raise ChainError("SETTLEMENT_UNCONFIRMED")
                chain = DevnetChain(client)
                await chain.verify_network()
                proof = None
                for _ in range(4):
                    proof = await chain.confirmed(data["transaction"], fingerprint)
                    if proof:
                        break
                    await asyncio.sleep(1)
                if proof is None:
                    raise ChainError("SETTLEMENT_UNCONFIRMED")
                self.save_proof(order_id, proof)
            return self.receipt(self.order(order_id))
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # No re-submission. A signature already exposed to the facilitator may still land.
            with self.db() as db:
                db.execute(
                    "UPDATE orders SET status='PENDING' WHERE id=? AND status='SETTLING'",
                    (order_id,),
                )
            return 202, {"error": "PAYMENT_PENDING", "order_id": order_id}, {}

    def save_proof(self, order_id: str, proof: dict):
        with self.db() as db:
            db.execute(
                "UPDATE orders SET status='SETTLED',proof=? WHERE id=?",
                (json.dumps(proof), order_id),
            )

    def receipt(self, order: dict) -> tuple[int, dict, dict]:
        response = SettleResponse.model_validate(
            {
                "success": True,
                "transaction": order["proof"]["signature"],
                "network": DEVNET_NETWORK,
                "payer": order["payer"],
            }
        )
        return (
            200,
            {
                "order_id": order["id"],
                "amount": order["amount"],
                "payer": order["payer"],
                "pay_to": self.address,
                "proof": order["proof"],
                "output": order["output"],
                "simulation": False,
            },
            {"PAYMENT-RESPONSE": encode_payment_response_header(response)},
        )

    async def reconcile(self, order_id: str) -> dict:
        order = self.order(order_id)
        if order is None:
            return {"status": "NOT_FOUND"}
        if order["status"] in ("PENDING", "SETTLING") and order["message_hash"]:
            async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
                proof = await DevnetChain(client).find_confirmed(
                    self.address, order["message_hash"], "governor:" + order_id
                )
            if proof:
                self.save_proof(order_id, proof)
                order = self.order(order_id)
        if order["status"] == "SETTLED":
            return {"status": "SETTLED", **self.receipt(order)[1]}
        return {"status": order["status"], "order_id": order_id}


def make_vendor_server(vendor: Vendor, port: int = 8788):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def allowed(self, mutation=False):
            host = self.headers.get("Host")
            if host not in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }:
                return False
            origin = self.headers.get("Origin")
            return (
                (not origin or origin == f"http://{host}")
                and self.headers.get("Sec-Fetch-Site") != "cross-site"
                and (not mutation or self.headers.get("Content-Type") == "application/json")
            )

        def respond(self, status, body, headers=None, mime="application/json"):
            if mime == "application/json":
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; "
                "style-src 'self'; connect-src 'self'; img-src 'self'; font-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.allowed():
                return self.respond(403, {"error": "Local vendor origin only"})
            path = urlsplit(self.path).path
            try:
                if path == "/api/state":
                    return self.respond(200, vendor.state())
                if path == "/api/balance":
                    with httpx.Client(timeout=12) as client:
                        balance = DevnetRPC(client).balances(vendor.address)
                    return self.respond(
                        200,
                        {
                            "status": "verified",
                            **balance,
                            "checked_at": datetime.now(UTC).isoformat(),
                        },
                    )
                if path.startswith("/api/orders/"):
                    order_id = path.removeprefix("/api/orders/")
                    if not re.fullmatch(r"[a-f0-9]{64}", order_id):
                        return self.respond(400, {"error": "Invalid order"})
                    return self.respond(200, asyncio.run(vendor.reconcile(order_id)))
                # Reuse packaged fonts/styles without exposing the rest of the workspace.
                if path.startswith("/assets/"):
                    file = (STATIC.parent.parent / "static" / path.lstrip("/")).resolve()
                    root = (STATIC.parent.parent / "static" / "assets").resolve()
                else:
                    root = STATIC.resolve()
                    file = (root / (path.lstrip("/") or "index.html")).resolve()
                if not file.is_relative_to(root) or not file.is_file():
                    return self.respond(404, {"error": "Not found"})
                return self.respond(
                    200,
                    file.read_bytes(),
                    mime=mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                )
            except Exception:
                return self.respond(
                    503, {"error": "Vendor data unavailable", "status": "unavailable"}
                )

        def do_POST(self):
            if not self.allowed(mutation=True):
                return self.respond(403, {"error": "Local vendor origin only"})
            if self.path != "/api/summary":
                return self.respond(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 50000 or self.headers.get("Transfer-Encoding"):
                    return self.respond(413, {"error": "Invalid body size"})
                self.connection.settimeout(10)
                body = json.loads(self.rfile.read(length))
                payment = self.headers.get("PAYMENT-SIGNATURE")
                if payment and len(payment) > 16000:
                    return self.respond(400, {"error": "Invalid payment header"})
                status, result, headers = asyncio.run(vendor.handle(body, payment))
                return self.respond(status, result, headers)
            except (ValueError, KeyError, TypeError):
                return self.respond(400, {"error": "Invalid order or payment"})
            except Exception:
                return self.respond(503, {"error": "Vendor unavailable; no settlement assumed"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description="Local x402 vendor with real Devnet USDC receipts")
    parser.add_argument("--data-dir", type=Path, default=Path(".governor"))
    args = parser.parse_args()
    vendor = Vendor(args.data_dir)
    server = make_vendor_server(vendor)
    print(f"Vendor console: {VENDOR_ORIGIN} / receiver {vendor.address}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
