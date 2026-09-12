"""Operator configuration. Model-generated input never changes these limits."""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

MAX_ATOMIC = 2**63 - 1  # SQLite's signed integer range.


class ConfigurationError(ValueError):
    """A configuration message that is safe to show without exposing input values."""


def atomic(value: str) -> int:
    """Parse canonical, unsigned atomic units without accepting floats or booleans."""
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", value):
        raise ValueError("amount must be a canonical decimal string of atomic units")
    amount = int(value)
    if amount > MAX_ATOMIC:
        raise ValueError("amount exceeds the supported integer range")
    return amount


class BudgetPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_cap: int = Field(default=10000, gt=0, le=MAX_ATOMIC)
    per_call_cap: int = Field(default=3000, gt=0, le=MAX_ATOMIC)


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: Literal["developer", "vertex"] = "developer"
    api_key: SecretStr | None = None
    model: str = Field(default="gemini-3.5-flash", min_length=1)
    project: str | None = None
    location: str = Field(default="global", min_length=1)
    data_dir: Path = Path(".governor")
    policy: BudgetPolicy = BudgetPolicy()
    max_turns: int = Field(default=8, gt=0, le=100)
    max_tool_calls: int = Field(default=16, gt=0, le=100)
    run_timeout_seconds: int = Field(default=120, gt=0, le=3600)
    model_timeout_seconds: int = Field(default=30, gt=0, le=300)
    payment_mode: Literal["mock", "solana-devnet"] = "mock"

    @model_validator(mode="after")
    def validate_timeouts(self) -> "Settings":
        if self.model_timeout_seconds > self.run_timeout_seconds:
            raise ValueError("model timeout cannot exceed run timeout")
        return self

    def require_credentials(self) -> None:
        if self.backend == "developer" and not self.api_key:
            raise ConfigurationError("set GEMINI_API_KEY in .env, or use the offline demo command")
        if self.backend == "vertex" and not self.project:
            raise ConfigurationError(
                "set GOOGLE_CLOUD_PROJECT and configure Application Default Credentials"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if env is None else env
        return cls(
            backend=values.get("GEMINI_BACKEND", "developer"),
            api_key=values.get("GEMINI_API_KEY") or None,
            model=values.get("GEMINI_MODEL", "gemini-3.5-flash"),
            project=values.get("GOOGLE_CLOUD_PROJECT") or None,
            location=values.get("GOOGLE_CLOUD_LOCATION", "global"),
            data_dir=Path(values.get("GOVERNOR_DATA_DIR", ".governor")),
            policy=BudgetPolicy(
                session_cap=atomic(values.get("GOVERNOR_SESSION_CAP", "10000")),
                per_call_cap=atomic(values.get("GOVERNOR_PER_CALL_CAP", "3000")),
            ),
            max_turns=values.get("GOVERNOR_MAX_TURNS", 8),
            max_tool_calls=values.get("GOVERNOR_MAX_TOOL_CALLS", 16),
            run_timeout_seconds=values.get("GOVERNOR_RUN_TIMEOUT_SECONDS", 120),
            model_timeout_seconds=values.get("GOVERNOR_MODEL_TIMEOUT_SECONDS", 30),
            payment_mode=values.get("GOVERNOR_PAYMENT_MODE", "mock"),
        )
