#!/usr/bin/env bash
# lean-prover49: Mathlib fork pinned by Goedel-Prover-V2 (submodule xinhjBrant/mathlib4 @ 2f65ba7, Lean v4.9.0-rc1).
# Built from source: the 2024 Mathlib cache artifacts are no longer served (HTTP 404). ~1.5 h CPU, then offline.
set -u
export PATH="$HOME/.elan/bin:$PATH"
retry() { local n; for n in 1 2 3 4 5 6; do "$@" && return 0; echo "retry $n: $*"; sleep 15; done; return 1; }
W="${LAS_STATION_DIR:-$HOME/lean-ai-station}/workspaces"; mkdir -p "$W"; cd "$W"
[ -d lean-prover49/.git ] || retry git clone --filter=blob:none https://github.com/xinhjBrant/mathlib4.git lean-prover49
cd lean-prover49
git checkout -q 2f65ba7f1a9144b20c8e7358513548e317d26de1
TC="$(cat lean-toolchain)"; elan toolchain list | grep -q "^${TC}" || retry elan toolchain install "$TC"
s=$(date +%s)
nice -n 10 lake build Mathlib 2>&1 | tail -5
rc=${PIPESTATUS[0]}
echo "P49 build rc=$rc seconds=$(( $(date +%s) - s ))"
exit $rc
