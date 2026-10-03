#!/usr/bin/env bash
# export_offline.sh — pack everything needed to run Lean AI Station on another OFFLINE machine
# (same OS family, NVIDIA GPU with a working driver + CUDA runtime, python + pyside6 installed).
#
# Usage: ./export_offline.sh DEST_DIR [--no-models] [--no-current]
# Produces DEST_DIR/*.tar.zst + SIZES.txt + import_offline.sh. Restore on the target with:
#   bash DEST_DIR/import_offline.sh
set -euo pipefail
DEST="${1:?usage: export_offline.sh DEST_DIR [--no-models] [--no-current]}"
shift || true
MODELS=1; CURRENT=1
for a in "$@"; do
  case "$a" in --no-models) MODELS=0 ;; --no-current) CURRENT=0 ;; esac
done
mkdir -p "$DEST"
S="$(cd "$(dirname "$0")" && pwd)"
TC49=leanprover--lean4---v4.9.0-rc1
TCCUR=leanprover--lean4---v4.34.1
Z="zstd -T0 -10"
pack() { # name, base dir, paths...
  local name=$1 base=$2; shift 2
  echo ">> $name"
  tar -C "$base" -I "$Z" -cf "$DEST/$name.tar.zst" "$@"
}
# 1. application + llama.cpp binaries (no sources, no venv: recreated by import)
N="$(basename "$S")"; B="$(dirname "$S")"
(cd "$B" && pack app "$B" "$N/app" "$N/bin" "$N/scripts" "$N/tests" "$N/docs" "$N/packaging" "$N/data" \
  "$N/vendor/llama.cpp/build/bin" $(cd "$B" && ls -d "$N"/*.md "$N"/*.sh "$N"/pytest.ini "$N"/LICENSE "$N"/NOTICE))
# 2. elan + toolchains (restored under $HOME)
TCS=(".elan/toolchains/$TC49")
[ $CURRENT = 1 ] && TCS+=(".elan/toolchains/$TCCUR")
pack elan "$HOME" .elan/bin .elan/settings.toml "${TCS[@]}"
# 3. Lean workspaces with their built .lake (no network needed to verify proofs)
WS=("$N/workspaces/lean-prover49")
[ $CURRENT = 1 ] && WS+=("$N/workspaces/lean-current")
pack workspaces "$B" "${WS[@]}"
# 4. models
if [ $MODELS = 1 ]; then
  pack models "$HOME" $(cd "$HOME" && ls models/Goedel-Prover-V2-8B.*.gguf models/Goedel-Prover-V2-8B.*.sha256 2>/dev/null)
fi
cat > "$DEST/import_offline.sh" <<'EOF'
#!/usr/bin/env bash
# Restore Lean AI Station from this folder (offline). Requires: python3, pyside6, NVIDIA driver + CUDA runtime.
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
# app + workspaces go to ~/lean-ai-station (or $1); elan and models go to $HOME
DST="${1:-$HOME}"
for f in app workspaces; do [ -f "$D/$f.tar.zst" ] && tar -C "$DST" -I zstd -xf "$D/$f.tar.zst"; done
for f in elan models; do [ -f "$D/$f.tar.zst" ] && tar -C "$HOME" -I zstd -xf "$D/$f.tar.zst"; done
R="$DST/lean-ai-station"
python3 -m venv --system-site-packages "$R/.venv"
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
cp "$R/app/lean_ai_station/assets/icon.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/lean-ai-station.svg"
sed "s|@ROOT@|$R|" "$R/packaging/lean-ai-station.desktop.in" > "$HOME/.local/share/applications/lean-ai-station.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "Terminé. Lancez « Lean AI Station » depuis le menu des applications."
EOF
chmod +x "$DEST/import_offline.sh"
{ echo "Export du $(date -Is)"; du -h "$DEST"/*.tar.zst; du -ch "$DEST"/*.tar.zst | tail -1; } | tee "$DEST/SIZES.txt"
