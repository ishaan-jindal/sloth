#!/usr/bin/env bash
# Phase B of the official run: the remaining ladder models, plus the locally
# present model with non-standard provenance (reported separately, never used
# for the headline claim).
set -euo pipefail
cd "$(dirname "$0")/.."

MODELS_B="${MODELS_B:-qwen2.5-coder:3b llama3.2:3b gemma3:4b spike-qwen15b:latest}"

for m in $MODELS_B; do
  if ! ollama list | awk '{print $1}' | grep -qx "$m"; then
    echo "===== skipping $m (not pulled) ====="
    continue
  fi
  echo "===== $m ====="
  date -Is
  python3 bench/run.py --cases bench/cases.jsonl --models "$m" --no-baseline \
    --out bench/results.jsonl
  date -Is
done
echo "PHASE B DONE"
