#!/usr/bin/env bash
# Download community GGUF quants of Goedel-Prover-V2-8B (mradermacher, pinned revision),
# parallel + resumable, SHA-256 verified against the Hugging Face LFS oid.
# Usage: download_model.sh Q4_K_M [Q5_K_M ...]
set -u
REPO=mradermacher/Goedel-Prover-V2-8B-GGUF; REV=43fab68ec71738a75b0a84738e18b222d2d4e249
declare -A SHA=([Q4_K_M]=919203ed088d6260fbbe124688ca04cb3e213cf63a9ed35346c69e4bec914c02
                [Q5_K_M]=eb00b45caa31a2b9cfa543c3075a02ef505c9d243439cce33ab7e8c203fb7a31)
for Q in "${@:-Q4_K_M}"; do
  F="Goedel-Prover-V2-8B.$Q.gguf"
  for i in 1 2 3; do
    python3 "$(dirname "$0")/pdownload.py" "https://huggingface.co/$REPO/resolve/$REV/$F" "$HOME/models/$F" "${SHA[$Q]}" \
      --workers 12 && break
    echo "attempt $i failed"; sleep 30
  done
done
echo "DL DONE"
