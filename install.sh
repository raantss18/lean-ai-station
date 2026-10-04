#!/usr/bin/env bash
# install.sh — reproducible, idempotent, user-level install of Lean AI Station (Arch/Fedora/Debian/Ubuntu/openSUSE).
#
#   ./install.sh [options]
#     --install-deps      install the base build packages with your package manager (uses sudo; drivers and CUDA
#                         are never installed for you)
#     --backend B         auto (default) | cuda | cpu     (cpu = works anywhere, but is much slower)
#     --no-models         skip the model downloads (≈ 15 GB for the three models)
#     --no-translator     skip the French→Lean translator model      --no-explainer   skip the explanation model
#     --no-prover49       skip the Lean 4.9 workspace (long: builds Mathlib from source, ≈ 1.5 h)
#     --no-current        skip the recent-Lean workspace
#     --q5                also download the Q5_K_M prover
# Needs Internet once; afterwards everything runs offline.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export LAS_STATION_DIR="$ROOT"
LLAMA_COMMIT=436f6f89e1e581249900b37a5b8a12a36a6d0912
DEPS=0; BACKEND=auto; MODELS=1; P49=1; CUR=1; QUANTS=(Q4_K_M formalizer explainer)
drop() { local out=() q; for q in "${QUANTS[@]}"; do [ "$q" = "$1" ] || out+=("$q"); done; QUANTS=("${out[@]}"); }
while [ $# -gt 0 ]; do case "$1" in
  --install-deps) DEPS=1 ;;
  --backend) shift; BACKEND="${1:?--backend needs a value}" ;;
  --no-models) MODELS=0 ;;
  --no-translator) drop formalizer ;;
  --no-explainer) drop explainer ;;
  --no-prover49) P49=0 ;;
  --no-current) CUR=0 ;;
  --q5) QUANTS+=(Q5_K_M) ;;
  -h|--help) sed -n 2,14p "$0"; exit 0 ;;
  *) echo "Option inconnue : $1 (voir --help)"; exit 2 ;;
esac; shift; done
step() { printf '\n==> %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------------ distribution
FAMILY=unknown
if [ -r /etc/os-release ]; then
  . /etc/os-release
  case " ${ID:-} ${ID_LIKE:-} " in
    *" arch "*|*" archlinux "*) FAMILY=arch ;;
    *" fedora "*|*" rhel "*|*" centos "*) FAMILY=fedora ;;
    *" debian "*|*" ubuntu "*) FAMILY=debian ;;
    *" suse "*|*" opensuse "*) FAMILY=suse ;;
  esac
fi
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
case $FAMILY in
  arch)   BASE_CMD="$SUDO pacman -S --needed --noconfirm git cmake base-devel python zstd util-linux curl" ;;
  fedora) BASE_CMD="$SUDO dnf install -y git cmake gcc gcc-c++ make python3 python3-pip zstd util-linux curl libxkbcommon libglvnd-glx libglvnd-egl libglvnd-opengl fontconfig dbus-libs xcb-util-cursor" ;;
  debian) BASE_CMD="$SUDO apt-get update && $SUDO apt-get install -y git cmake build-essential python3 python3-venv python3-pip zstd util-linux curl libglib2.0-0 libxkbcommon0 libegl1 libgl1 libfontconfig1 libdbus-1-3 libxcb-cursor0" ;;
  suse)   BASE_CMD="$SUDO zypper --non-interactive install git cmake gcc gcc-c++ make python3 python3-pip zstd util-linux curl libxkbcommon0 Mesa-libEGL1 Mesa-libGL1 libfontconfig1 libdbus-1-3 libxcb-cursor0" ;;
  *)      BASE_CMD="" ;;
esac

step "1/8 Prérequis ($FAMILY)"
if [ $DEPS = 1 ]; then
  [ -n "$BASE_CMD" ] || { echo "Distribution non reconnue : installez git, cmake, gcc/g++, make, python3 (≥ 3.10, avec venv/pip), zstd, curl."; exit 1; }
  echo "+ $BASE_CMD"; eval "$BASE_CMD"
fi
missing=()
for c in git cmake gcc g++ make python3 curl zstd; do have "$c" || missing+=("$c"); done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null || missing+=("python3>=3.10")
python3 -c 'import venv, ensurepip' 2>/dev/null || missing+=("python3-venv")
if ((${#missing[@]})); then
  echo "Il manque : ${missing[*]}"
  [ -n "$BASE_CMD" ] && echo "Relancez avec --install-deps, ou installez-les :  $BASE_CMD"
  exit 1
fi

if [ "$BACKEND" = auto ]; then
  if have nvidia-smi && nvidia-smi -L >/dev/null 2>&1; then BACKEND=cuda; else BACKEND=cpu; fi
fi
echo "Moteur d'IA : $BACKEND"
for d in /opt/cuda /usr/local/cuda /usr/local/cuda-*; do [ -x "$d/bin/nvcc" ] && export PATH="$d/bin:$PATH"; done
if [ "$BACKEND" = cuda ]; then
  have nvidia-smi || { echo "Pas de pilote NVIDIA (nvidia-smi introuvable). Installez le pilote, ou utilisez --backend cpu."; exit 1; }
  if ! have nvcc; then
    echo "Le CUDA Toolkit (nvcc) est introuvable. À installer vous-même :"
    case $FAMILY in
      arch)   echo "  sudo pacman -S cuda" ;;
      fedora) echo "  Pilote : RPM Fusion (akmod-nvidia xorg-x11-drv-nvidia-cuda) ; Toolkit : dépôt NVIDIA"
              echo "  https://developer.nvidia.com/cuda-downloads  (Linux → x86_64 → Fedora → rpm (network))" ;;
      debian) echo "  sudo apt install nvidia-cuda-toolkit      (ou le dépôt NVIDIA : https://developer.nvidia.com/cuda-downloads)" ;;
      suse)   echo "  https://developer.nvidia.com/cuda-downloads  (Linux → x86_64 → openSUSE)" ;;
      *)      echo "  https://developer.nvidia.com/cuda-downloads" ;;
    esac
    echo "Puis relancez ./install.sh   (ou ./install.sh --backend cpu pour tout faire sans carte graphique)."
    exit 1
  fi
  vram=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)
  [ "${vram:-0}" -lt 7000 ] && echo "⚠️  Carte de ${vram} Mio : moins de 8 Go, le modèle sera en partie traité par le processeur (plus lent)."
fi
df -h "$HOME" | tail -1

step "2/8 Gestionnaire Lean (elan)"
if ! have elan && [ ! -x "$HOME/.elan/bin/elan" ]; then
  curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh -s -- -y --default-toolchain none
fi
export PATH="$HOME/.elan/bin:$PATH"

step "3/8 Moteur d'IA llama.cpp @ ${LLAMA_COMMIT:0:9} ($BACKEND)"
L="$ROOT/vendor/llama.cpp"
if [ ! -d "$L/.git" ]; then
  mkdir -p "$ROOT/vendor"
  git clone https://github.com/ggml-org/llama.cpp "$L"
fi
git -C "$L" fetch -q origin "$LLAMA_COMMIT" 2>/dev/null || true
git -C "$L" checkout -q "$LLAMA_COMMIT"
if [ ! -x "$L/build/bin/llama-server" ] || [ "$(cat "$L/build/.backend" 2>/dev/null)" != "$BACKEND" ]; then
  rm -rf "$L/build"
  build() { cmake -S "$L" -B "$L/build" -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF -DGGML_NATIVE=ON "$@" &&
            cmake --build "$L/build" -j"$(nproc)" --target llama-server llama-bench llama-cli llama-quantize; }
  if [ "$BACKEND" = cuda ]; then
    ARCH=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d '.')
    if ! build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$ARCH"; then
      # Fedora/Arch ship a GCC newer than the one this CUDA release supports: retry with an older host compiler.
      OLD=""; for v in 14 13 12 11; do have "g++-$v" && { OLD="g++-$v"; break; }; done
      rm -rf "$L/build"
      if [ -n "$OLD" ]; then echo "Nouvel essai avec le compilateur $OLD"; build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$ARCH" -DCMAKE_CUDA_HOST_COMPILER="$(command -v "$OLD")"
      else echo "Nouvel essai avec -allow-unsupported-compiler"; build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$ARCH" -DCMAKE_CUDA_FLAGS=-allow-unsupported-compiler; fi
    fi
  else
    build -DGGML_CUDA=OFF
  fi
  echo "$BACKEND" > "$L/build/.backend"
fi

step "4/8 Environnement Python"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  if python3 -c "import PySide6" 2>/dev/null; then python3 -m venv --system-site-packages "$ROOT/.venv"   # distribution's PySide6
  else python3 -m venv "$ROOT/.venv"; fi
fi
"$ROOT/.venv/bin/python" -m pip install -q --upgrade pip
"$ROOT/.venv/bin/python" -c "import PySide6" 2>/dev/null || "$ROOT/.venv/bin/python" -m pip install -q PySide6-Essentials
"$ROOT/.venv/bin/python" -m pip install -q psutil pytest pytest-qt
"$ROOT/.venv/bin/python" -c "import PySide6, psutil; print('PySide6', PySide6.__version__)"

step "5/8 Modèles d'IA (SHA-256 vérifié)"
if [ $MODELS = 1 ]; then
  mkdir -p "$HOME/models"
  "$ROOT/scripts/download_model.sh" "${QUANTS[@]}"
else
  echo "(ignoré : --no-models)"
fi

step "6/8 Espaces Lean"
[ $CUR = 1 ] && "$ROOT/scripts/setup_current.sh"
[ $P49 = 1 ] && "$ROOT/scripts/build_prover49.sh"   # long: builds Mathlib from source

step "7/8 Raccourci du menu des applications"
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
cp "$ROOT/app/lean_ai_station/assets/icon.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/lean-ai-station.svg"
sed "s|@ROOT@|$ROOT|" "$ROOT/packaging/lean-ai-station.desktop.in" > "$HOME/.local/share/applications/lean-ai-station.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

step "8/8 Contrôle"
LAS_CONFIG_DIR="$(mktemp -d)" LAS_STARTUP_PROBE=exit QT_QPA_PLATFORM=offscreen "$ROOT/bin/lean-ai-station" 2>/dev/null | grep STARTUP \
  && echo "L'application démarre correctement." || echo "⚠️  Le démarrage de contrôle a échoué : lancez $ROOT/bin/lean-ai-station pour voir l'erreur."

echo; echo "Installation terminée. Lancez « Lean AI Station » depuis le menu, ou : $ROOT/bin/lean-ai-station"
