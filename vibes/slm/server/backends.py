"""Two ways to run the same model, behind one function.

mlx      - Apple Silicon, loads the fused weights directly. Default on this Mac.
llamacpp - talks to a running `llama-server` over its OpenAI-compatible API.
           This is the Windows path, and it works on the Mac too, which is how
           we prove both laptops are running the same model.

Both must send the empty <think> block that Qwen3 was trained with here, or the
model returns nothing useful.
"""

from __future__ import annotations

import os
from typing import Protocol

DEFAULT_MLX_MODEL = os.environ.get("SLM_MODEL", "models/slm-clean")
DEFAULT_LLAMA_URL = os.environ.get("SLM_LLAMA_URL", "http://127.0.0.1:8080")
MAX_NEW_TOKENS = int(os.environ.get("SLM_MAX_TOKENS", "512"))  # bullet lists get long


class Backend(Protocol):
    name: str
    model: str

    def generate(self, messages: list[dict]) -> str: ...


class MLXBackend:
    name = "mlx"

    def __init__(self, model_path: str = DEFAULT_MLX_MODEL) -> None:
        from mlx_lm import load

        self.model_path = model_path
        self.model = model_path
        self._model, self._tokenizer = load(model_path)

    def generate(self, messages: list[dict]) -> str:
        from mlx_lm import generate as mlx_generate
        from mlx_lm.sample_utils import make_sampler

        prompt = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,  # must match how the data was templated
        )
        text = mlx_generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=MAX_NEW_TOKENS,
            sampler=make_sampler(temp=0.0),
            verbose=False,
        )
        return _strip_think(text)


class LlamaCppBackend:
    name = "llamacpp"

    def __init__(self, base_url: str = DEFAULT_LLAMA_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = os.environ.get("SLM_LLAMA_MODEL", "slm-clean")

    def generate(self, messages: list[dict]) -> str:
        import httpx

        response = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": MAX_NEW_TOKENS,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=120.0,
        )
        response.raise_for_status()
        return _strip_think(response.json()["choices"][0]["message"]["content"])


def _strip_think(text: str) -> str:
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def load_backend(kind: str | None = None) -> Backend:
    kind = (kind or os.environ.get("SLM_BACKEND") or "mlx").lower()
    if kind == "mlx":
        return MLXBackend()
    if kind in ("llamacpp", "llama.cpp", "gguf"):
        return LlamaCppBackend()
    raise ValueError(f"unknown backend {kind!r}")
