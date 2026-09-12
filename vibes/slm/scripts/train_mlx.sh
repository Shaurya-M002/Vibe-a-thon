#!/usr/bin/env bash
# LoRA fine-tune Qwen3-0.6B on Apple Silicon, then fuse the adapter into weights
# the server can load directly.
set -euo pipefail

cd "$(dirname "$0")/.."

MODEL="${MODEL:-Qwen/Qwen3-0.6B}"
BATCH="${BATCH:-4}"
SEQ="${SEQ:-768}"
PY=.venv/bin/python

# QUICK=1 is the "did my data change do what I think" run: a fifth of the
# iterations, into throwaway paths. It must not touch models/slm-clean, because
# a smoke test that overwrites the working model costs you the full 15 minutes
# it was meant to save.
if [[ "${QUICK:-0}" == "1" ]]; then
  ITERS="${ITERS:-300}"
  ADAPTERS=adapters/quick
  OUT=models/slm-quick
else
  ITERS="${ITERS:-1200}"
  ADAPTERS=adapters
  OUT=models/slm-clean
fi

# Training and inference compete for the same memory, and the losers are slow in
# a way that looks like a hung job rather than a busy one.
if pgrep -qf "llama-server|uvicorn server.app"; then
  echo "warning: llama-server/uvicorn are running and will slow this down a lot." >&2
  echo "         stop them first: pkill -f llama-server; pkill -f 'uvicorn server.app'" >&2
fi

mkdir -p "$ADAPTERS"

$PY -m mlx_lm lora \
  --model "$MODEL" \
  --train \
  --data data/processed \
  --adapter-path "$ADAPTERS" \
  --iters "$ITERS" \
  --batch-size "$BATCH" \
  --max-seq-length "$SEQ" \
  --num-layers 16 \
  --learning-rate 1e-4 \
  --steps-per-report 25 \
  --steps-per-eval 200 \
  --save-every 200 \
  --mask-prompt

$PY -m mlx_lm fuse \
  --model "$MODEL" \
  --adapter-path "$ADAPTERS" \
  --save-path "$OUT"

echo "fused -> $OUT"
[[ "${QUICK:-0}" == "1" ]] && \
  echo "smoke test: SLM_MODEL=$OUT $PY scripts/eval_fixtures.py --backend mlx"
