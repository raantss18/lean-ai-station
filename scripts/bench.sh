#!/usr/bin/env bash
# Benchmark matrix for BENCH.md (llama-bench, JSONL). Usage: scripts/bench.sh MODEL.gguf OUT.jsonl
# Valid combos only: a quantized V cache requires flash attention.
set -euo pipefail
M="${1:?model}"; OUT="${2:?out.jsonl}"
B="$HOME/lean-ai-station/vendor/llama.cpp/build/bin"
export LD_LIBRARY_PATH="$B"
: > "$OUT"
run() { "$B/llama-bench" -m "$M" -ngl 99 -p 2048 -n 128 -d 0,8192 -r 2 -o jsonl "$@" >> "$OUT"; }
run -fa 0 -ctk f16 -ctv f16 -b 2048 -ub 512
for kv in f16 q8_0 q4_0; do run -fa 1 -ctk $kv -ctv $kv -b 2048 -ub 512; done
run -fa 1 -ctk q8_0 -ctv q8_0 -b 2048 -ub 256,1024,2048
