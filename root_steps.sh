#!/usr/bin/env bash
# root_steps.sh — root actions computed after the Phase 0 audit (2026-10-03).
# Result of the audit: NO root action is required.
#   - NVIDIA driver nvidia-open 615.71.09 already runs CUDA 13.4 on the RTX 4060 (sm_89 test kernel OK).
#   - cuda, base-devel, cmake, git, ripgrep, python, pyside6, paru, timeshift: already installed.
#   - llama.cpp is built from source as the user (no package exists: pacman/AUR `llama.cpp-cuda` not found).
#   - pytest-qt is installed in a user venv, not system-wide.
# This script is kept idempotent and harmless so the audit trail is complete.
set -euo pipefail
LOG="$HOME/lean-ai-station/logs/root_steps.log"
mkdir -p "$(dirname "$LOG")"
echo "$(date -Is) root_steps.sh: nothing to do (see AUDIT.md)" | tee -a "$LOG"
