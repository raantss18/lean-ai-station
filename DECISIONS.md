# DECISIONS

## D1 — No root changes
- Choice: do not touch the system (no driver change, no `pacman -Syu`, no package install).
- Alternatives: install python-pytest-qt from repo; run nvidia-inst.
- Reason: audit shows the driver + CUDA already work; everything else can be user-level. Smallest blast radius.
- Evidence: AUDIT.md, nvcc sm_89 test kernel returned 0.

## D2 — Inference engine: llama.cpp built from source (CUDA, sm_89)
- Choice: `ggml-org/llama.cpp` built in `vendor/llama.cpp` with `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89 -DGGML_NATIVE=ON`.
- Alternatives: `ollama-cuda` (repo, 0.35.1) or the existing manual ollama 0.18.2; AUR `llama.cpp-cuda` (does not exist).
- Reason: the mission requires an OpenAI-compatible `llama-server` that the GUI starts/stops with explicit control of
  `-ngl`, ctx, batch, KV-cache type and flash attention; it also ships `llama-bench`, `llama-quantize` and
  `convert_hf_to_gguf.py`. Ollama hides those knobs and runs as a separate daemon. User's ollama is left untouched.

## D3 — Model source: convert official safetensors ourselves
- Choice: download `Goedel-LM/Goedel-Prover-V2-8B` @ `dfd02e6271a58375dfbf3ece0175277cf6b6a89a`, convert + quantize locally.
- Alternatives: `mradermacher/Goedel-Prover-V2-8B-GGUF` (exists, ~370 downloads), `NikolayKozloff/...Q8_0-GGUF`.
- Reason: provenance from the official repo, exact tokenizer/chat template, freedom to produce Q4_K_M and Q5_K_M with the
  same llama.cpp version used for inference.

## D4 — Prompt format: verbatim from Goedel-Prover-V2 `src/utils.py` (DeepSeekCoTHandler)
- Initial: "Complete the following Lean 4 code:\n\n```lean4\n{stmt := by sorry}```\n\nBefore producing ... proof plan ..."
- Correction: previous assistant output appended, then "The proof (Round {n-1}) is not correct. Following is the compilation
  error message, where we use <error></error> to signal the position of the error.\n\n{errors}\n\nBefore producing ... analysis of the error message."
- Error string built exactly like `get_error_str` (max 8 errors, 4 lines context). Reference copy: `docs/goedel_utils_reference.py`.
- Sampling as in their inference: temperature 1.0, top_p 0.95 (GUI default; adjustable).

## D5 — Lean workspaces; reuse existing installs
- `lean-prover49`: clone of `xinhjBrant/mathlib4` @ `2f65ba7f1a9144b20c8e7358513548e317d26de1` (the Goedel-Prover-V2
  submodule), toolchain `leanprover/lean4:v4.9.0-rc1`. Used as the workspace directly, like Goedel's `DEFAULT_LEAN_WORKSPACE`.
- `lean-current`: new Lake project requiring Mathlib tag `v4.34.1` (toolchain v4.34.1 = latest Lean stable release).
- Existing user projects (lean4web `MathlibDemo`, `<projet de cours>`) are auto-detected and offered **read-only**;
  the app never runs `lake build`/`update` in them. Rationale: user said Lean and lean4web are already installed; reuse
  without risking their working setups. elan + `~/.cache/mathlib` are shared.

## D3b — Revised: community GGUF (mradermacher) instead of local conversion
- Evidence: official safetensors download ran at ~1 MB/s on flaky WiFi (2.1 GB in ~35 min ⇒ >4 h for 16.4 GB);
  intermittent DNS failures.
- Choice: `mradermacher/Goedel-Prover-V2-8B-GGUF` @ `43fab68ec71738a75b0a84738e18b222d2d4e249`, files Q4_K_M (5.03 GB)
  and Q5_K_M (5.85 GB), SHA-256 verified against the HF LFS oid (stored as `~/models/*.sha256`).
- Integrity vs official: the embedded `tokenizer.chat_template` is compared to the official `tokenizer_config.json`
  (downloaded from the official repo at the pinned revision) — see STATE.md.
- `scripts/download_model.sh` stays re-runnable; local conversion remains possible (`.venv-convert`, llama.cpp
  `convert_hf_to_gguf.py`) if the official weights are fetched on a faster link.

## D6 — lean-prover49: build Mathlib from source
- Evidence: `lake exe cache get` at 2f65ba7 tries `lakecache.blob.core.windows.net` → HTTP 404 for every file
  (also 404 on `mathlib4.lean-cache.cloud`): 2024 artifacts are no longer served.
- Choice: `lake build Mathlib` locally (nice 10, ~4650 files, CPU only, no network). One-time cost; then fully offline.
- lean-current (v4.34.1) uses the live cache. Setup split into `scripts/build_prover49.sh` and `scripts/setup_current.sh`.

## D7 — Default inference settings (evidence: BENCH.md)
- Q4_K_M, ctx 24576, KV q8_0, flash attention on, -b 2048 -ub 512, all layers on GPU.
- Alternatives: 16k f16 (6 % faster tg but truncates the 16k-token answers observed on miniF2F); 32k q8_0 (0.5 GB VRAM
  headroom only); Q5_K_M (10–12 % slower, needs ctx ≤ 16k to keep headroom).
- Sampling = Goedel-Prover-V2 inference script: temperature 1.0, top-p 0.95. max_tokens 16384 bounds one attempt
  to ~8 min worst case at ~30 tok/s.

## D8 — Bound llama-server host prompt cache (`-cram 1024`)
- Evidence: llama-server logs "cache state ... limits: 8192.000 MiB" — by default it may keep up to 8 GiB of prompt
  states in RAM; the soak test must show flat memory.
- Choice: 1024 MiB. With one slot and sequential attempts, the slot KV cache already reuses the shared prefix
  (`cache_prompt`); the RAM cache only helps when switching chat ↔ prove.

## D9 — Never plan a model load without a GPU snapshot
- Evidence: soak run started the server before the first `nvidia-smi` poll returned → planner saw "no GPU" → CPU
  (VRAM stayed 484 MiB, server RSS 9 GB, GUI lag > 1 s). `AppContext.with_gpu_info` now defers the load until a
  snapshot (or a definitive "no GPU") arrives, 3 s max.

## D10 — Support both Lake build layouts; strict test workspace
- Evidence: lean-prover49 (Lean 4.9) was reported "Mathlib n'est pas compilé" although the build succeeded: old
  Lake writes oleans to `.lake/build/lib`, newer Lake to `.lake/build/lib/lean`. The integration tests had silently
  fallen back to lean-current.
- Fix: `Workspace.lean_path()` uses whichever layout exists per package; `LAS_TEST_WS` now fails loudly instead of
  falling back. Verified: good proof accepted (9.6 s cold), sorry and wrong proofs rejected (2.2 s warm) on Lean 4.9.

## D11 — Lifetime fixes found by the full suite
- A lambda capturing its emitter (`p.started → lambda: p.processId()`) caused a double delete (segfault) → bound slot.
- One `ChatStream` per attempt was never freed (GUI RSS +24 MB over a 30-min soak) → streams `deleteLater()` after reporting.

## D12 — Natural language → Lean with Goedel-Formalizer-V2-8B (second model, swapped on the same GPU)
- Evidence: HF search shows `Goedel-LM/Goedel-Formalizer-V2-8B` (Apache-2.0, same team as the prover, thinks before
  answering) and community GGUFs; model card gives the exact prompt and sampling (T 0.9, top-k 20, top-p 0.95).
  Real run (`bench/translate_real.jsonl`): 3/3 problems (French plain text, French+LaTeX, English) compile on the
  first try in 9.5–21 s.
- Choice: Q4_K_M GGUF (mradermacher @ 7e10076, sha256 14f0cf69…), prompt verbatim, up to 3 tries until Lean accepts
  the statement (compiled with `sorry`), standard header enforced. The statement is then shown for **human review**
  (a statement that compiles can still mean something else) before any proof search.
- Alternatives: one general chat model to translate (no formalization training, worse); 32B formalizer (does not fit
  8 GB); keeping both models loaded (2 × 5 GB > 8 GB VRAM) → swap per task (≈ 4 s, `AppContext.ensure_model(role)`).

## D13 — Loop guard
- Evidence: user screenshot of the chat repeating two comment lines forever; chat had no repetition control.
- Choice: `leancheck.detect_loop` (shortest period repeated ≥ 5× covering ≥ 600 chars; ≥ 1500 chars when the period is
  < 60 chars so legitimate `· norm_num` lists are not cut) checked every 40 streamed chunks in `ChatStream`; on a loop the
  request is aborted, the first occurrence is kept and the prover / formalizer starts a fresh attempt. Chat also sends
  `repeat_penalty 1.1` and DRY (0.8, length 4). Prover sampling is left exactly as in the Goedel scripts.

## D14 — LaTeX in/out without touching Overleaf's API
- Input: `texio.extract_statements` (theorem/lemma/proposition/exercise… environments, comments and `\label` removed;
  fallback = document body). Output: self-contained `.tex` (XeLaTeX, `fvextra` Verbatim — `listings` mis-placed `⟩`
  under XeLaTeX), verified to compile with the local XeLaTeX (`test_exported_tex_compiles_with_xelatex`).
- Overleaf: the instance on this machine answers 404 on 127.0.0.1:80 (OVERLEAF_SITE_URL is the Tailscale name) and
  an API import would need the user's login → no automation; the app exports a file / copies the code and opens the
  configurable URL (Système → adresse d'Overleaf). Passwords are never requested.

## D15 — Proof → French explanation: a third, general model (Qwen3-8B), not the prover
- Evidence (measured, same proof, same server): Goedel-Prover answered the English prompt in English with Markdown
  headings and re-wrote the Lean proof; with a French prompt it mixed French and English ("In Lean, this is expressed as…");
  with an assistant prefill it drifted back to "Step-by-Step Abstract Plan / Lean 4 Proof with have statements".
  Qwen3-8B Q4_K_M (the base architecture family of both Goedel models, official `Qwen/Qwen3-8B-GGUF` @ 7c41481,
  Apache-2.0) with the same prompt in French: fluent French, correct structure (statement / idea / numbered steps),
  10.6 s and 19.0 s for the two test proofs.
- Choice: `Explainer` service with `chat_template_kwargs.enable_thinking=false`, T 0.5, repeat_penalty 1.05, loop guard;
  Markdown + `$math$` rendered in the GUI (math → Unicode), converted to safe LaTeX for the export (compile-tested).
  The explanation is labelled « rédigée par une IA » everywhere; the Lean proof stays the authority.
- Cost: one more 5 GB model (3 × 5 GB on disk, swapped in 8 GB VRAM). `--no-explainer` skips it; the button then explains
  how to get the model.

## D16 — Chat tab removed
- The tab used the prover as a chat model, which it is not (it looped — user screenshot). Its value for the target user
  (learning Lean) is now covered by the guided flow, the Aide dialog and « Expliquer ». Settings keys are ignored on load.

## D17 — Portable installer
- Evidence: fresh `git clone` + `./install.sh --install-deps --backend cpu` in a **Fedora 44 container** found two real
  portability bugs (missing `libGL` for PySide6 wheels; a GUI test that depended on installed models) — both fixed.
- `install.sh`: distro family from /etc/os-release (arch/fedora/debian/suse), package lists per family, `--backend
  auto|cuda|cpu`, CUDA toolkit looked up in /opt/cuda, /usr/local/cuda*, retry with an older host compiler (g++-14…11) or
  `-allow-unsupported-compiler` when CUDA rejects a newer GCC (known Fedora/Arch issue), PySide6 from the distribution if
  importable else `PySide6-Essentials` in the venv, final start-up probe. Drivers and CUDA are never installed for the user.
- Not covered: AMD/Intel GPUs (Vulkan/ROCm builds) — the app then runs on the CPU.
