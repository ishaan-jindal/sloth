#!/usr/bin/env bash
# Phase A of the official run: the ladder models already on disk.
# Ordered fastest first, so the cheap arms are banked before the slow ones.
# Run on an otherwise idle machine: latency is a pre-registered threshold.
set -euo pipefail
cd "$(dirname "$0")/.."

MODELS_A="${MODELS_A:-qwen3:0.6b qwen2.5-coder:1.5b gemma4:e2b}"
OUT=bench/results.jsonl

: > "$OUT"
first=1
for m in $MODELS_A; do
  echo "===== $m ====="
  date -Is
  if [ "$first" = 1 ]; then
    python3 bench/run.py --cases bench/cases.jsonl --models "$m" --out "$OUT"
    first=0
  else
    python3 bench/run.py --cases bench/cases.jsonl --models "$m" --no-baseline --out "$OUT"
  fi
  date -Is
done
echo "PHASE A DONE"
