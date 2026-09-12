"""POST /v1/clean — the sidecar Hex and Musly call after ASR.

Run (Mac):      .venv/bin/python -m uvicorn server.app:app --port 8742
Run (Windows):  set SLM_BACKEND=llamacpp && python -m uvicorn server.app:app --port 8742
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from style import STYLES, build_messages  # noqa: E402
from server.backends import load_backend  # noqa: E402
from server.guard import apply_guard  # noqa: E402

app = FastAPI(title="SLM ASR cleaner", version="1.0")
_backend = None


def backend():
    global _backend
    if _backend is None:
        _backend = load_backend()
    return _backend


class CleanRequest(BaseModel):
    text: str
    style: str = Field(default="chat", description=f"one of {STYLES}")


class CleanResponse(BaseModel):
    raw: str
    cleaned: str
    violations: list[dict]
    latency_ms: int
    backend: str
    model: str


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "backend": backend().name, "model": backend().model}


@app.post("/v1/clean", response_model=CleanResponse)
def clean(req: CleanRequest) -> CleanResponse:
    style = req.style if req.style in STYLES else "chat"
    started = time.perf_counter()

    raw = req.text.strip()
    if not raw:
        return CleanResponse(
            raw=raw, cleaned="", violations=[], latency_ms=0,
            backend=backend().name, model=backend().model,
        )

    generated = backend().generate(build_messages(raw, style))
    cleaned, violations = apply_guard(raw, generated)

    return CleanResponse(
        raw=raw,
        cleaned=cleaned,
        violations=violations,
        latency_ms=int((time.perf_counter() - started) * 1000),
        backend=backend().name,
        model=backend().model,
    )


@app.get("/")
def index() -> FileResponse:
    # No caching: during a build day the page changes more often than you reload.
    return FileResponse(
        ROOT / "server" / "static" / "index.html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )
