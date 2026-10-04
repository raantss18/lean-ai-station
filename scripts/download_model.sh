#!/usr/bin/env bash
# Download community GGUF quants (mradermacher, pinned revisions), parallel + resumable, SHA-256 verified
# against the Hugging Face LFS oid.
# Usage: download_model.sh [Q4_K_M] [Q5_K_M] [formalizer] [explainer]     (default: prover Q4_K_M)
set -u
P_REPO=mradermacher/Goedel-Prover-V2-8B-GGUF;     P_REV=43fab68ec71738a75b0a84738e18b222d2d4e249
F_REPO=mradermacher/Goedel-Formalizer-V2-8B-GGUF; F_REV=7e1007687ab9d8da2bf1fbf1fca03649f5e6f2cc
E_REPO=Qwen/Qwen3-8B-GGUF;                         E_REV=7c41481f57cb95916b40956ab2f0b139b296d974
declare -A SHA=([Q4_K_M]=919203ed088d6260fbbe124688ca04cb3e213cf63a9ed35346c69e4bec914c02
                [Q5_K_M]=eb00b45caa31a2b9cfa543c3075a02ef505c9d243439cce33ab7e8c203fb7a31
                [formalizer]=14f0cf69c3c3b8906136f6d98420262e70a66be671f6295e312adab5649564fe
                [explainer]=d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785)
get() { # repo rev file sha
  for i in 1 2 3; do
    python3 "$(dirname "$0")/pdownload.py" "https://huggingface.co/$1/resolve/$2/$3" "$HOME/models/$3" "$4" --workers 12 && return 0
    echo "attempt $i failed"; sleep 30
  done
  return 1
}
mkdir -p "$HOME/models"
for Q in "${@:-Q4_K_M}"; do
  if [ "$Q" = formalizer ]; then get "$F_REPO" "$F_REV" "Goedel-Formalizer-V2-8B.Q4_K_M.gguf" "${SHA[formalizer]}"
  elif [ "$Q" = explainer ]; then get "$E_REPO" "$E_REV" "Qwen3-8B-Q4_K_M.gguf" "${SHA[explainer]}"
  else get "$P_REPO" "$P_REV" "Goedel-Prover-V2-8B.$Q.gguf" "${SHA[$Q]}"; fi
done
echo "DL DONE"
