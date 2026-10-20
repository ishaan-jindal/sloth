#!/usr/bin/env bash
# The dedicated latency pass. T4 is a latency threshold, so it is measured here,
# on an otherwise idle machine, rather than during the main run where the box
# was also downloading models and building the Rust workspace.
#
# Do not run anything else on the machine while this runs.
set -euo pipefail
cd "$(dirname "$0")/.."

MODELS_L="${MODELS_L:-qwen3:0.6b qwen2.5-coder:1.5b qwen2.5-coder:3b gemma4:e2b spike-qwen15b:latest}"
N="${N:-15}"
OUT=bench/latency.jsonl

: > "$OUT"
echo "idle latency pass, $N cases per model, $(date -Is)"
for m in $MODELS_L; do
  if ! ollama list | awk '{print $1}' | grep -qx "$m"; then
    echo "-- skipping $m (not pulled)"
    continue
  fi
  echo "-- $m"
  python3 bench/run.py \
    --cases bench/cases.jsonl \
    --models "$m" \
    --limit "$N" \
    --no-baseline \
    --out "$OUT"
done
echo "LATENCY PASS DONE $(date -Is)"
