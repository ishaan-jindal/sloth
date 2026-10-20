#!/usr/bin/env bash
# The official run. Ordered fastest first, so the cheap arms are banked before
# the slow ones and a truncated session still leaves usable data.
# Run on an otherwise idle machine: latency is a pre-registered threshold and
# the dedicated idle pass comes afterwards.
set -euo pipefail
cd "$(dirname "$0")/.."

MODELS="${MODELS:-qwen3:0.6b qwen2.5-coder:1.5b spike-qwen15b:latest qwen2.5-coder:3b llama3.2:3b gemma4:e2b gemma3:4b}"
OUT=bench/results.jsonl

: > "$OUT"
first=1
for m in $MODELS; do
  if ! ollama list | awk '{print $1}' | grep -qx "$m"; then
    echo "===== skipping $m (not pulled) ====="
    continue
  fi
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
echo "GENERATION DONE"
