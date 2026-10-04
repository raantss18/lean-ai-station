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


## D18 — v1.1 design choices (asked to the user first, 2026-10-04)
- Memory = profile + dossier history + library (user selected all three). Chaining = fully automatic (user choice; the
  statement stays visible in the thread, a « Pause pour relire l'énoncé » option exists, off by default).
  Refinement = discussion thread per dossier. Language switch = interface + model answers.
- The profile is given to the formalizer (as « conventions » appended to the natural-language text; the model-card
  template itself is untouched) and to the explainer. **Not** to the prover: its prompt must stay Goedel's (D4).
- Follow-up routing: keyword router FR/EN (`dossiers.route`) with a visible override combo, instead of an extra LLM call
  that would cost a model switch (≈ 5 s) for every message.
- Proof refinement: prover conversation = [Goedel initial prompt, previous verified proof, « The proof above is correct
  … The user now asks: … »]; then the usual Lean-error correction rounds. Statement refinement: formalizer input =
  problem + previous statement + requested change. Explanation refinement: explainer conversation continued.
- Library: every accepted proof is stored (header stripped, dependencies recorded, same-workspace only); for a new
  statement, up to 3 entries with Jaccard vocabulary similarity ≥ 0.25 are pasted above the target theorem (with their
  proofs, so Lean checks everything). The target theorem is therefore always the LAST declaration of a file (D19).

## D19 — Target = last theorem; per-dossier theorem names
- `prepare_statement`, `theorem_name`, `initial_prompt` (rsplit, identical output for a single theorem — tested) and
  `assemble_proof` now target the last declaration; helper lemmas the model copies back are de-duplicated by name.
- Each dossier gets a unique Lean name (`slug(title)_xxxx`) so library entries never clash.

## D20 — i18n
- French is the source language; `_()` + `i18n_en.EN` (629 entries). An AST-based key extractor
  (`scripts/dev/i18n_keys.py`) feeds `tests/test_i18n.py` (no missing key, same placeholders, no French in English).
- Changing language rebuilds the main window in-process (model and dossiers untouched; refused while a task runs).
- Pitfall found: parameters/locals named `_` (`*_`, `lambda _=False`, `path, _ = …`, `for _ in`) shadow the function;
  all renamed, and a check confirmed none remain.

## D21 — Updates of Lean/Mathlib and of the Goedel models (asked to the user first, 2026-10-04)
- User choices: weekly check at start-up (on by default, can be turned off), one-click install, obsolete versions
  deleted automatically, shipped in 1.1.0. The check is a deliberate, visible exception to offline mode: only
  `git ls-remote` on mathlib4 (GitHub) and the Hugging Face model API are contacted; no identifier is sent.
- What is followed: the stable Mathlib tags (`v4.N.M`, no release candidates) for the `lean-current` workspace; new
  `Goedel-LM/Goedel-{Prover,Formalizer}-V*-8B` models (8B only: 8 GB card) installable once a Q4_K_M GGUF exists at
  mradermacher (same source as the installer), and re-published GGUF files (checksum changed).
- Not followed: `lean-prover49` (Lean 4.9 + Goedel's Mathlib fork) is the model's training environment and stays fixed;
  the Qwen3 explainer and llama.cpp stay pinned (reproducibility).
- Install = build next to the old version → verify (Mathlib: a test theorem compiled with the new workspace; model:
  SHA-256 + loaded by the bundled llama-server on the CPU and asked for a few tokens) → switch → delete the obsolete
  version (old workspace; old Lean toolchain only if no `lean-toolchain` in the home folder still pins it and it is not
  the elan default; older model files of the same role). Any failure before the switch leaves everything unchanged.
- The shared Mathlib download cache (`~/.cache/mathlib`) is not pruned: other Lean projects of the user use it.
- Limit: a future Goedel model could expect a different prompt format; the smoke test checks that it loads and
  answers, not its proof quality.

## D22 — Circular reasoning detector (bug report with a screen recording, 2026-10-04)
- Symptom: Goedel-Prover recycled the same paragraphs with small variations until the 16 384-token limit (≈ 6 min per
  attempt, 8 attempts planned). `detect_loop` only sees exact repetitions, so it never fired.
- `detect_rambling`: code blocks removed (proofs legitimately repeat tactic lines); in the last 8 000 characters of
  prose, the share of sentences (≥ 30 chars, normalised) already written earlier; fires at ≥ 60 % and ≥ 12 sentences,
  never before 20 000 characters of prose. Checked every 200 tokens in `ChatStream`; the attempt then ends as « loop ».
- Calibration on 11 real answers collected with the app's server settings (bench corpus, not committed except one
  excerpt in `tests/data/`): with a first setting (5 000/12 000) the detector fired on 4 answers that had a circling
  phase, one of which later escaped and **was accepted by Lean** (mathd_numbertheory_353). With the final setting it
  fires on none of the 19 naturally finished answers (8 more runs of the failing statement were added) and still
  catches the recorded failure pattern.
- The same corpus exposed a false positive of the **exact** detector (since 1.0): a correct answer
  (amc12b_2021_p3, accepted by Lean) repeats a 121-character tactic block 5 times and was stopped at 7 600 characters.
  Inside a Lean code block (odd number of fences in the whole answer, now passed instead of the last 12 000
  characters) an exact loop now needs 12 repetitions and 3 000 characters; the real runaway in code
  (imo_1959_p1, nested `Nat.gcd_eq_left/right` until the token limit) is still stopped, at 6 000 characters.

## D23 — « Comprendre » step before translation
- Same report: « donne une preuve du théorème de la base incomplète… » was formalised as « more than dim V vectors are
  dependent » (a different theorem); the formalizer only receives the user's words, and a theorem name gives it little.
- The general model (Qwen3-8B, already installed for explanations) now rewrites the request as one precise,
  self-contained statement in English (objects, types, hypotheses; named results stated in textbook form; plain
  statements only translated) before Goedel-Formalizer. Measured on the CPU: the incomplete basis theorem becomes
  « …let S be a linearly independent subset of V. Then there exists a basis of V that contains S ». Unknown names
  (« lemme des bergers ») or non-mathematical text give `UNKNOWN`: the chain stops and asks for the statement instead
  of inventing one (first prompt version hallucinated a theorem there).
- The rewritten statement is shown in the thread before the Lean statement, so the user sees the interpretation.
  Cost: one more model switch (≈ 5 s) and ≈ 5–20 s of generation. Skipped when the explainer is not installed, and for
  « Corriger l'énoncé » requests (they go to the formalizer with the previous statement, as before).
- Limit: ambiguous names are resolved by the model (« théorème de Bézout » → curves, not the arithmetic identity); the
  user corrects it in the thread.

## D24 — Prove loop: bounded correction chains, token floor, user hints (bug report, 2026-10-04)
- Symptom: « les 8 essais reproduisent la même erreur ». Replayed on the user's dossier (cubic polynomial has a real
  root, Lean 4.9): attempt 1 takes a bad line of attack, attempt 2 (correction) repeats it with the same errors, attempt
  3 gets only 1 556 tokens because the conversation filled the 24k context (`_fit_messages` allowed down to ≈ 2k), so
  it is cut and returns a `sorry` skeleton; every later attempt does the same.
- Goedel-Prover-V2 is evaluated with a few self-correction rounds on top of independent samples, not with one ever
  growing conversation. Now: at most 2 corrections per line of attack (`Prover.MAX_CORRECTIONS`), then a fresh sample;
  a fresh sample immediately when Lean returns exactly the same feedback twice; and the conversation restarts instead of
  requesting fewer than 8 192 tokens (`MIN_GEN_TOKENS`). Round numbers restart at 0 for each line of attack (Goedel's
  « Round {n-1} » convention).
- Router: before a proof exists, follow-ups were always sent to the formalizer (the statement was re-translated, the
  prover never saw the request). Now only statement-related requests are; others start a new proof search with the
  request appended to the initial prompt (`HINT_TEMPLATE`, the only addition to Goedel's prompt, used only when the
  user wrote something).

## D25 — Real names for invented lemmas
- Seen in the 1.1.2 live run: most refused attempts cite lemmas that do not exist in Mathlib 4.9
  (`Polynomial.exists_root_of_odd_degree`, `Polynomial.exists_root`, …); Lean's message gives no alternative, so the
  next attempt guesses another name.
- `names.py` indexes the declarations of the sources that are compiled in the workspace (module roots found in the
  `.lake/**/build/lib` directories of `lean_path()`, plus the toolchain's `Init`), with a light parser (namespaces,
  sections, `_root_`, declaration keywords, signature head up to `:=`). No Lean process; 5.4 s for lean-prover49
  (164 474 names), 9.8 s for lean-current (274 895), cached in `~/.cache/lean-ai-station/names/` (key: workspace,
  toolchain, mtimes of `lake-manifest.json` / `lean-toolchain`, so an update rebuilds it). Built in a background thread
  when a proof search starts; if not ready yet, the feedback is simply unchanged.
- Suggestions: same last component in another namespace first, then token overlap + string similarity (+ bonus for
  the same namespace). Added after Goedel's error string as « Note on unknown names » (up to 3 names × 5 suggestions,
  with signatures). Names generated by attributes (`@[to_additive]`, `@[simps]`) are not indexed: they can be missing
  from suggestions, never wrongly reported, because only names Lean itself rejected are looked up.
- Lean 4.9 reports `T.foo x` with `T` a type (`Real`, `Nat`, `Eq`) as « invalid field notation » without the name:
  the name is then read from the `<error>` span of that error.

## D26 — Follow-up messages are read by the general model (bug report, 2026-10-04)
- Symptom: « Montre qu'un carré pair est issu d'un entier pair » was rewritten as the converse by the « comprendre »
  step, then « et la réciproque », « et si n^2 est pair montre que n est pair », « J'ai dis montre que si n^2 est pair
  alors n est aussi pair » were all routed to `proof` by the keyword router (no statement keyword; a proof existed), so
  the same theorem was proved three more times.
- Understanding: the prompt now asks to identify what is assumed and what must be concluded (with this very example)
  and to answer `EN:` (for the formalizer) + `FR:` (shown in the thread, in the interface language). Measured on Qwen3
  (CPU): the three phrasings give « If n² is even, then n is even »; real-model test `test_task7_direction_and_converse`
  proves `Even (n^2) → Even n`.
- Routing: in « Auto » mode, a follow-up goes first to the general model with the current theorem, its Lean statement
  and whether it is proven; it answers ACTION: STATEMENT | PROOF | EXPLANATION and, for STATEMENT, the complete new
  theorem (EN + user language). A new statement is then translated afresh (no « previous formalization » anchoring),
  proved and explained; the explanation now uses the understood statement rather than the original problem. Measured:
  10/11 messages classified as intended; the miss (« pourquoi utilises-tu ring ? » → PROOF) is overridden by the
  keyword router when it has explicit explanation/statement keywords and no proof keyword. Cost: one model switch per
  follow-up (≈ 5–15 s). The manual stage selector and quick-action buttons bypass it, as before.
- Rejected names (D25 addendum): the 1.1.3 note reached the model only inside a correction; every fresh sample
  (after 2 corrections or a repeated error) started without it, so `Polynomial.exists_root` came back. Names rejected by
  Lean are now kept for the whole search and appended to the first message of each fresh sample. Lean 4.34 writes
  « Unknown identifier `x` » (capital, backticks): both wordings are recognised.
- Live run on the user's cubic: the model also kept the rejected name inside its corrections, despite the note. Reusing
  a name Lean already rejected in this search now ends the line of attack at once: fresh sample, with the warning at
  the top of the task.
