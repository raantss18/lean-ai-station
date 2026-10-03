#!/usr/bin/env bash
# install.sh — reproducible, idempotent install of Lean AI Station (user level, no sudo).
#
# Prerequisites (Arch/EndeavourOS names; install them yourself, e.g. `sudo pacman -S --needed ...`):
#   nvidia driver with CUDA (nvidia-smi works), cuda, base-devel, cmake, git, python, pyside6, zstd, util-linux
# Usage: ./install.sh [--no-prover49] [--no-current] [--q5]
#   Needs Internet once (~5 GB model + Mathlib), then everything runs offline.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export LAS_STATION_DIR="$ROOT"
LLAMA_COMMIT=436f6f89e1e581249900b37a5b8a12a36a6d0912
P49=1; CUR=1; QUANTS=(Q4_K_M)
for a in "$@"; do case "$a" in --no-prover49) P49=0 ;; --no-current) CUR=0 ;; --q5) QUANTS+=(Q5_K_M) ;; esac; done
step() { printf '\n==> %s\n' "$*"; }

step "1/7 Checking prerequisites"
missing=()
for c in nvidia-smi nvcc cmake git python3 zstd setpriv curl; do
  command -v "$c" >/dev/null || [ "$c" = nvcc -a -x /opt/cuda/bin/nvcc ] || missing+=("$c")
done
python3 -c "import PySide6" 2>/dev/null || missing+=("pyside6 (python module)")
if ((${#missing[@]})); then
  echo "Missing: ${missing[*]}"
  echo "Arch/EndeavourOS: sudo pacman -S --needed nvidia-utils cuda base-devel cmake git python pyside6 zstd util-linux curl"
  exit 1
fi
export PATH="/opt/cuda/bin:$PATH"
df -h "$HOME" | tail -1

step "2/7 Lean toolchain manager (elan)"
if ! command -v elan >/dev/null && [ ! -x "$HOME/.elan/bin/elan" ]; then
  curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh -s -- -y --default-toolchain none
fi
export PATH="$HOME/.elan/bin:$PATH"

step "3/7 llama.cpp (CUDA) @ $LLAMA_COMMIT"
L="$ROOT/vendor/llama.cpp"
if [ ! -d "$L/.git" ]; then
  mkdir -p "$ROOT/vendor"
  git clone https://github.com/ggml-org/llama.cpp "$L"
fi
git -C "$L" fetch -q origin "$LLAMA_COMMIT" 2>/dev/null || true
git -C "$L" checkout -q "$LLAMA_COMMIT"
ARCH=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d '.')
if [ ! -x "$L/build/bin/llama-server" ]; then
  cmake -S "$L" -B "$L/build" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$ARCH" -DCMAKE_BUILD_TYPE=Release \
        -DLLAMA_CURL=OFF -DGGML_NATIVE=ON
  cmake --build "$L/build" -j"$(nproc)" --target llama-server llama-bench llama-cli llama-quantize
fi

step "4/7 Python environment"
[ -x "$ROOT/.venv/bin/python" ] || python3 -m venv --system-site-packages "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -q pytest pytest-qt psutil

step "5/7 Model (Goedel-Prover-V2-8B GGUF, sha256-verified)"
mkdir -p "$HOME/models"
"$ROOT/scripts/download_model.sh" "${QUANTS[@]}"

step "6/7 Lean workspaces"
[ $CUR = 1 ] && "$ROOT/scripts/setup_current.sh"
[ $P49 = 1 ] && "$ROOT/scripts/build_prover49.sh"   # long: builds Mathlib from source

step "7/7 Desktop launcher"
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
cp "$ROOT/app/lean_ai_station/assets/icon.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/lean-ai-station.svg"
sed "s|@ROOT@|$ROOT|" "$ROOT/packaging/lean-ai-station.desktop.in" > "$HOME/.local/share/applications/lean-ai-station.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo; echo "Installation terminée. Lancez « Lean AI Station » depuis le menu, ou : $ROOT/bin/lean-ai-station"
