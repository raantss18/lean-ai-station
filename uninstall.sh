#!/usr/bin/env bash
# uninstall.sh — removes exactly what the Lean AI Station setup installed. Nothing else.
# Usage: ./uninstall.sh          (shows the list, asks for confirmation)
#        ./uninstall.sh --yes    (no question)
# Never touched / never removed: system packages (no root change was made), your own Lean projects
# (lean4web, <projet de cours>, ...), other elan toolchains, Ollama and its models.
set -u
H="$HOME"
ITEMS=(
  "$(cd "$(dirname "$0")" && pwd)"                       # this folder: app, venvs, llama.cpp build, Lean workspaces
  "$H/.config/lean-ai-station"                           # settings + session
  "$H/.cache/lean-ai-station"                            # temporary check files
  "$H/.local/share/applications/lean-ai-station.desktop" # menu entry
  "$H/.local/share/icons/hicolor/scalable/apps/lean-ai-station.svg"
  "$H/models/Goedel-Prover-V2-8B.Q4_K_M.gguf"            # model files (+ checksums)
  "$H/models/Goedel-Prover-V2-8B.Q4_K_M.gguf.sha256"
  "$H/models/Goedel-Prover-V2-8B.Q5_K_M.gguf"
  "$H/models/Goedel-Prover-V2-8B.Q5_K_M.gguf.sha256"
  "$H/models/src/Goedel-Prover-V2-8B"                    # partial official safetensors (abandoned download)
)
# Optional (shared with your other Lean projects): removed only with --toolchains
TOOLCHAINS=(
  "$H/.elan/toolchains/leanprover--lean4---v4.9.0-rc1"
  "$H/.elan/toolchains/leanprover--lean4---v4.34.1"
)
echo "Les éléments suivants seront supprimés :"
for p in "${ITEMS[@]}"; do [ -e "$p" ] && printf "  %-75s %s\n" "$p" "$(du -sh "$p" 2>/dev/null | cut -f1)"; done
WITH_TC=0
[[ " $* " == *" --toolchains "* ]] && WITH_TC=1
if [ $WITH_TC = 1 ]; then
  for p in "${TOOLCHAINS[@]}"; do [ -e "$p" ] && printf "  %-75s %s\n" "$p" "$(du -sh "$p" | cut -f1)"; done
else
  echo "(Les versions de Lean ajoutées à elan sont conservées ; ajoutez --toolchains pour les retirer.)"
  echo "(~/.cache/mathlib est partagé avec vos autres projets Lean : il est conservé.)"
fi
if [[ " $* " != *" --yes "* ]]; then
  read -r -p "Confirmer la suppression ? [o/N] " a
  [[ "$a" =~ ^[oOyY]$ ]] || { echo "Annulé."; exit 0; }
fi
pkill -f "^$(cd "$(dirname "$0")" && pwd)/vendor/llama.cpp/build/bin/llama-server" 2>/dev/null || true
for p in "${ITEMS[@]}"; do rm -rf -- "$p"; done
[ $WITH_TC = 1 ] && for p in "${TOOLCHAINS[@]}"; do rm -rf -- "$p"; done
rmdir "$H/models/src" 2>/dev/null || true
update-desktop-database "$H/.local/share/applications" 2>/dev/null || true
echo "Lean AI Station a été désinstallé."
