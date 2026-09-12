"""Payment boundary shared by mock services and the allowlisted Devnet vendor."""

import asyncio
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from solders.signature import Signature

from governor.config import atomic
from governor.ledger import Ledger, LedgerError, Reservation
from governor.networks import DEVNET_NETWORK
from governor.runway import purchase_identity


class Service(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    id: str
    description: str
    advertised_amount: str


class Quote(BaseModel):
    """Internal adapter contract, deliberately NOT an x402 wire payload."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    service_id: str
    amount: str
    network: Literal["solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"] = DEVNET_NETWORK
    mode: Literal["mock", "solana-devnet"] = "mock"
    payment_required: dict | None = None

    @field_validator("amount")
    @classmethod
    def positive_atomic_amount(cls, value: str) -> str:
        if atomic(value) == 0:
            raise ValueError("a paid quote must be positive")
        return value


class Settlement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    amount: str
    receipt: str
    data: dict
    mode: Literal["mock", "solana-devnet"] = "mock"

    @model_validator(mode="after")
    def validate_receipt(self):
        if self.mode == "mock":
            if not self.receipt.startswith("mock:"):
                raise ValueError("mock receipt required")
        else:
            Signature.from_string(self.receipt)
        return self


class PaymentAdapter(Protocol):
    def catalog(self) -> list[Service]: ...
    async def quote(self, service_id: str, text: str) -> Quote: ...
    async def authorize(self, quote: Quote, attempt_id: str) -> object: ...
    async def settle(self, authorization: object, text: str) -> Settlement: ...


class PaymentGate:
    def __init__(self, ledger: Ledger, session_id: str, adapter: PaymentAdapter):
        self.ledger = ledger
        self.session_id = session_id
        self.adapter = adapter
        self.mode = getattr(adapter, "mode", "mock")
        if self.ledger.snapshot(session_id)["payment_mode"] != self.mode:
            raise LedgerError("adapter differs from session payment mode")
        self.services = {service.id: service for service in adapter.catalog()}

    def _result(self, *, ok: bool, code: str, data: dict | None = None) -> dict:
        return {
            "ok": ok,
            "code": code,
            "data": data or {},
            "budget": self.ledger.snapshot(self.session_id),
            "runway": self.ledger.report(self.session_id)["runway"],
        }

    def _previous(self, previous: Reservation, attempt_id: str) -> dict:
        if previous.status == "SETTLED":
            return self._result(ok=True, code="ALREADY_SETTLED", data=previous.result)
        if previous.status in ("DENIED", "RELEASED"):
            self.ledger.record(self.session_id, "REFUSAL_RETURN", {"code": previous.result["code"]})
            return self._result(
                ok=False, code=previous.result["code"], data={"attempt_id": attempt_id}
            )
        return self._result(ok=False, code="PAYMENT_PENDING", data={"attempt_id": attempt_id})

    def _deny(self, attempt_id: str, code: str, service_id: str) -> dict:
        self.ledger.record(
            self.session_id,
            "DENIED",
            {
                "attempt_id": attempt_id,
                "service": service_id,
                "code": code,
                "budget": self.ledger.snapshot(self.session_id),
            },
        )
        return self._result(ok=False, code=code)

    async def purchase(self, service_id: str, text: str) -> dict:
        # Stable across model turns and process restarts. An identical purchase
        # within the same task is cached; a new task gets a new session identity.
        attempt_id = purchase_identity(self.session_id, service_id, text)
        previous = self.ledger.lookup(self.session_id, attempt_id)
        if previous:
            return self._previous(previous, attempt_id)
        service = self.services.get(service_id)
        if not service:
            return self._deny(attempt_id, "UNKNOWN_SERVICE", service_id)
        try:
            # Revalidate adapter output; seller data cannot set the network/mode.
            quote = Quote.model_validate((await self.adapter.quote(service_id, text)).model_dump())
        except (ValueError, ValidationError):
            return self._deny(attempt_id, "INVALID_CHALLENGE", service_id)
        except Exception:
            return self._deny(attempt_id, "QUOTE_UNAVAILABLE", service_id)
        if quote.service_id != service_id or quote.mode != self.mode:
            return self._deny(attempt_id, "SERVICE_MISMATCH", service_id)
        # Check hard caps before quote ceilings so cap refusals are attributable.
        # A quote mismatch is still refused before authorization.
        reserved = self.ledger.reserve(self.session_id, attempt_id, service_id, quote.amount)
        if not reserved.created or reserved.status != "RESERVED":
            return self._previous(reserved, attempt_id)
        if atomic(quote.amount) > atomic(service.advertised_amount):
            self.ledger.release_unsigned(self.session_id, attempt_id, "PRICE_CHANGED")
            return self._deny(attempt_id, "PRICE_CHANGED", service_id)

        if hasattr(self.adapter, "preflight"):
            try:
                await self.adapter.preflight(quote)
            except Exception:
                self.ledger.release_unsigned(self.session_id, attempt_id, "PAYMENT_SETUP_REQUIRED")
                return self._deny(attempt_id, "PAYMENT_SETUP_REQUIRED", service_id)

        self.ledger.mark_authorizing(self.session_id, attempt_id)
        try:
            authorization = await self.adapter.authorize(quote, attempt_id)
            settlement = Settlement.model_validate(
                (await self.adapter.settle(authorization, text)).model_dump()
            )
            result = self._commit(attempt_id, settlement)
            return self._result(ok=True, code="SETTLED", data=result)
        except asyncio.CancelledError:
            self.ledger.record(
                self.session_id,
                "PAYMENT_UNCERTAIN",
                {
                    "attempt_id": attempt_id,
                    "code": "CANCELLED",
                    "hold_retained": True,
                },
            )
            raise
        except Exception as exc:
            # Never use an HTTP error or exception as evidence that a signature
            # cannot settle. Do not log raw exception strings (may contain keys).
            code = "SETTLEMENT_MISMATCH" if isinstance(exc, LedgerError) else "PAYMENT_UNCERTAIN"
            self.ledger.record(
                self.session_id,
                code,
                {
                    "attempt_id": attempt_id,
                    "hold_retained": True,
                },
            )
            return self._result(ok=False, code=code, data={"attempt_id": attempt_id})

    def _commit(self, attempt_id: str, settlement: Settlement) -> dict:
        if settlement.mode != self.mode:
            raise LedgerError("settlement payment mode mismatch")
        result = {
            "attempt_id": attempt_id,
            "receipt": settlement.receipt,
            "simulation": self.mode == "mock",
            "output": settlement.data,
        }
        self.ledger.commit(self.session_id, attempt_id, settlement.amount, result)
        return result

    async def reconcile(self, attempt_id: str) -> dict:
        previous = self.ledger.lookup(self.session_id, attempt_id)
        if previous is None:
            return self._result(ok=False, code="UNKNOWN_ATTEMPT")
        if previous.status != "AUTHORIZING" or not hasattr(self.adapter, "reconcile"):
            return self._previous(previous, attempt_id)
        try:
            settlement = await self.adapter.reconcile(attempt_id)
            if settlement:
                data = self._commit(attempt_id, Settlement.model_validate(settlement.model_dump()))
                return self._result(ok=True, code="SETTLED", data=data)
        except (LedgerError, asyncio.CancelledError):
            raise
        except Exception:
            pass
        return self._result(ok=False, code="PAYMENT_PENDING", data={"attempt_id": attempt_id})
