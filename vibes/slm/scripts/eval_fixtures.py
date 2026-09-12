"""Run the 20 held-out fixtures and print the before/after table for the TV.

    .venv/bin/python scripts/eval_fixtures.py            # mlx, local weights
    SLM_BACKEND=llamacpp .venv/bin/python scripts/eval_fixtures.py

Writes results/fixtures-<backend>.md so the Mac and Windows runs can be diffed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from style import build_messages  # noqa: E402
from server.backends import load_backend  # noqa: E402
from server.guard import apply_guard  # noqa: E402

FIXTURES = ROOT / "data" / "fixtures.jsonl"


def normalise(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default=os.environ.get("SLM_BACKEND", "mlx"))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    backend = load_backend(args.backend)
    fixtures = [json.loads(line) for line in FIXTURES.read_text(encoding="utf-8").splitlines() if line.strip()]

    rows = []
    exact = 0
    latencies = []
    guard_saves = 0

    for fx in fixtures:
        started = time.perf_counter()
        generated = backend.generate(build_messages(fx["raw"], fx.get("style", "chat")))
        cleaned, violations = apply_guard(fx["raw"], generated)
        latency = int((time.perf_counter() - started) * 1000)
        latencies.append(latency)
        if violations:
            guard_saves += 1

        ok = normalise(cleaned) == normalise(fx["expected"])
        exact += ok
        rows.append({**fx, "got": cleaned, "ok": ok, "latency_ms": latency, "violations": violations})
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {fx['id']} ({latency} ms)")
        print(f"   raw      : {fx['raw']}")
        print(f"   expected : {fx['expected']!r}")
        print(f"   got      : {cleaned!r}")
        if violations:
            print(f"   guard    : {[v['kind'] + ':' + v['value'] for v in violations]}")

    n = len(rows)
    latencies.sort()
    p50 = latencies[n // 2]
    print(f"\nexact match {exact}/{n}   p50 {p50} ms   max {max(latencies)} ms   guard fired {guard_saves}x")

    out = Path(args.out) if args.out else ROOT / "results" / f"fixtures-{backend.name}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Fixtures — backend `{backend.name}`, model `{backend.model}`",
        "",
        f"Exact match **{exact}/{n}**. Median latency **{p50} ms**, slowest {max(latencies)} ms.",
        "",
        "| # | raw (ASR) | cleaned | expected | ok |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        def cell(text: str) -> str:
            return text.replace("|", "\\|").replace("\n", "<br>") or "*(empty)*"

        lines.append(
            f"| {row['id']} | {cell(row['raw'])} | {cell(row['got'])} | "
            f"{cell(row['expected'])} | {'yes' if row['ok'] else 'no'} |"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
