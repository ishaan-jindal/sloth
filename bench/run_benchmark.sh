#!/usr/bin/env bash
# The full benchmark, end to end. Run it on an otherwise idle machine: latency
# is a pre-registered threshold and a busy CPU makes it meaningless.
#
#   ./bench/run_benchmark.sh            # full run
#   LIMIT=5 ./bench/run_benchmark.sh    # quick smoke test
set -euo pipefail

cd "$(dirname "$0")/.."

LIMIT="${LIMIT:-0}"
MODELS="${MODELS:-qwen3:0.6b qwen2.5-coder:1.5b qwen2.5-coder:3b llama3.2:3b gemma3:4b}"
JUDGES="${JUDGES:-gemma4:e2b qwen2.5-coder:3b}"
PER_MODEL="${PER_MODEL:-20}"
URL="${SLOTH_URL:-http://127.0.0.1:11434}"

echo "== models present =="
ollama list

echo
echo "== 1. generation =="
: > bench/results.jsonl
for m in $MODELS; do
  echo "-- $m"
  python3 bench/run.py \
    --cases bench/cases.jsonl \
    --models "$m" \
    --limit "$LIMIT" \
    --base-url "$URL" \
    --out bench/results.jsonl
done

echo
echo "== 2. deterministic fidelity + blinded tasks =="
python3 bench/rate.py \
  --results bench/results.jsonl \
  --cases bench/cases.jsonl \
  --styles bench/styles.json \
  --per-model "$PER_MODEL" \
  --out-tasks bench/tasks_human.jsonl \
  --out-tasks-baseline bench/tasks_baseline.jsonl \
  --out-fidelity bench/fidelity.jsonl

echo
echo "== 3. blind judging =="
: > bench/judgments.jsonl
for j in $JUDGES; do
  for tf in bench/tasks_human.jsonl bench/tasks_baseline.jsonl; do
    echo "-- judge $j on $tf"
    python3 bench/judge.py \
      --tasks "$tf" \
      --judge "$j" \
      --out bench/judgments.jsonl
  done
done

echo
echo "== 4. analysis =="
python3 bench/analyze.py \
  --results bench/results.jsonl \
  --fidelity bench/fidelity.jsonl \
  --judgments bench/judgments.jsonl \
  --cases bench/cases.jsonl \
  --out-json bench/summary.json \
  --out-md bench/REPORT.md
python3 bench/analyze.py \
  --results bench/results.jsonl \
  --fidelity bench/fidelity.jsonl \
  --judgments bench/judgments.jsonl \
  --cases bench/cases.jsonl \
  --safe-only \
  --label "headline (post-cutoff-safe cases only)" \
  --out-json bench/summary_headline.json \
  --out-md bench/REPORT_headline.md

echo
echo "== done =="
