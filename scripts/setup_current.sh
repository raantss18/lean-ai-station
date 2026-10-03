#!/usr/bin/env bash
# lean-current: Mathlib v4.34.1 from the live cache (network once).
set -u
export PATH="$HOME/.elan/bin:$PATH"
retry() { local n; for n in 1 2 3 4 5 6; do "$@" && return 0; echo "retry $n: $*"; sleep 15; done; return 1; }
W="${LAS_STATION_DIR:-$HOME/lean-ai-station}/workspaces"; mkdir -p "$W/lean-current"; cd "$W/lean-current"
[ -f lean-toolchain ] || echo 'leanprover/lean4:v4.34.1' > lean-toolchain
[ -f lakefile.toml ] || cat > lakefile.toml <<'T'
name = "LeanCurrent"
defaultTargets = ["LeanCurrent"]

[[require]]
name = "mathlib"
git = "https://github.com/leanprover-community/mathlib4"
rev = "v4.34.1"

[[lean_lib]]
name = "LeanCurrent"
T
[ -f LeanCurrent.lean ] || echo 'import Mathlib' > LeanCurrent.lean
TC="$(cat lean-toolchain)"; elan toolchain list | grep -q "^${TC}" || retry elan toolchain install "$TC"
[ -f lake-manifest.json ] || retry lake update
retry lake exe cache get
lake build 2>&1 | tail -3; echo "CUR build rc=${PIPESTATUS[0]}"
