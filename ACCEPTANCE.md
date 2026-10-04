# ACCEPTANCE — evidence for each item

Machine: RTX 4060 Laptop 8 GB (60 W), Ryzen 7 7735HS, EndeavourOS, kernel 7.2.7. Test log of the full run:
`logs/full_tests_prover49.log` (not versioned; reproduce with `LAS_TEST_WS=lean-prover49 ./run_tests.sh --all`).
Result of the last full run: **52 passed** + usability tasks 2/3 re-run after a test fix (**2 passed**) → 54/54.
After the memory fixes: fast suite 37/37 green, final 30-min soak flat (below).

## A. Functional
| Item | Evidence | Status |
|---|---|---|
| 5 tabs work (+ home, wizard) | `test_gui.py::test_all_pages_render`; 19 real-state screenshots `docs/screenshots/` | ✅ |
| Full prove cycle without network (`unshare -rn`), easy theorem | `bench/offline_unshare_lean-current.jsonl`: `pairs` proved, attempt 1, 28.5 s; `curl huggingface.co` fails inside the namespace | ✅ |
| … miniF2F statement | same file: `mathd_algebra_478` proved, attempt 1, 32.2 s | ✅ |
| Full prove cycle on lean-prover49 (Goedel's Lean 4.9 + Mathlib) | `test_integration.py::test_full_prove_cycle[easy]`, `[mathd_algebra_478]` with `LAS_TEST_WS=lean-prover49` (strict, no fallback) | ✅ |
| Wrong proof rejected | `test_wrong_proof_rejected`, `test_prover_fake.py::test_statement_cannot_be_weakened` | ✅ |
| `sorry` proof rejected (both Lean message styles) | `test_sorry_proof_rejected`, `test_judge_rejects_sorry_both_lean_versions`, `test_sorry_answer_rejected` | ✅ |
| Non-standard axioms rejected | `test_native_decide_axiom_rejected` (`Lean.ofReduceBool`) | ✅ |
| Goedel prompt/feedback format exact | `test_initial_prompt_matches_goedel_readme`, `test_correction_prompt_matches_goedel`, `test_error_string_identical_to_goedel` (byte-identical to Goedel's `get_error_str`) | ✅ |

## B. Robustness (fault injection)
| Item | Evidence | Status |
|---|---|---|
| kill llama-server mid-generation → clear message + restart, no hang | `test_kill_server_midgeneration_reports_error`; screenshots 18→19 (banner « Redémarrer le modèle », back to 37/37 layers in 4.0 s); usability task 4: 1 click | ✅ |
| kill `lean` mid-compile | `test_kill_lean_midcompile` | ✅ |
| compile timeout fires | `test_compile_timeout_fires` (5 s limit, returns < 20 s) | ✅ |
| no orphan processes after quit / kill -9 | `test_kill9_gui_kills_llama_server` (server gone after GUI SIGKILL, `setpriv --pdeathsig`); manual: gone ≤ 300 ms, VRAM back to 484 MiB; SIGTERM quit removes lock + pid file | ✅ |
| model missing / corrupt | `test_missing_and_corrupt_model`, `test_gguf_corrupt_and_truncated` | ✅ |
| GGUF too large for VRAM | `test_plan_launch_fallbacks` (ctx shrink, then partial offload, then CPU); real: Q5_K_M auto-loaded with ctx 12288, 37/37 layers (usability task 2) | ✅ |
| CUDA out-of-memory (real) | forced ctx 40960 f16 → real CUDA OOM → automatic retries 20480 → 10240 → ready in 5.6 s, 37/37 layers, plain-French notices (`bench/oom_fallback.txt`) | ✅ |
| disk nearly full warning | `test_disk_nearly_full_warns` | ✅ |
| GPU unavailable → CPU fallback message | `test_gpu_unavailable_falls_back_to_cpu` | ✅ (simulated) |
| double click / double launch | `test_double_click_prove_starts_once`, `test_double_launch_forwards_and_exits` | ✅ |
| power loss (kill -9 GUI) → clean recovery | `test_kill9_gui_recovers_cleanly` (atomic JSON, stale lock detected, « Session restaurée ») | ✅ |
| 30-minute soak | `bench/soak.json`: 55 prove cycles, **55/55 proved**, VRAM 7049–7053 MiB, GUI RSS 113.4 → 114.0 MB over the last 27 min (flat), QObject count constant (750), event-loop lag ≈ 20 ms (302 ms once, during model load) | ✅ |

## C. Efficiency (details in BENCH.md)
| Metric | Value |
|---|---|
| GUI cold start (window populated, workspaces + models detected) | 0.53–0.58 s wall |
| Model load (server ready, Q4_K_M, 37/37 layers) | 3.3–4.2 s (8.3 s with cold page cache under heavy CPU load) |
| First-token latency | 0.10–0.15 s |
| Generation speed | 41.5 tok/s (API), 44.8 tok/s llama-bench @0, 33.4 @8k, 26.6 @16k |
| GPU utilisation during generation | 92–97 % at the 60 W power cap |
| VRAM idle / under load | 7047 MiB (model + 24k KV pre-allocated; flat during soak) |
| RAM idle | GUI 106 MiB, server 882 MiB RSS |
| Idle CPU | GUI 0.10 %, server 0.20 % |
| Lean check (lean-prover49) | 9.6 s first (cold), 2.2 s warm; never runs `lake` → no rebuilds |
| GUI thread never blocked | all I/O via QProcess/QNetworkAccessManager/thread pool; soak event-loop lag ≈ 20 ms during proving |
| Best alternative compared | BENCH.md §1–3 (FA on/off, KV f16/q8/q4, ubatch, Q4 vs Q5, ctx sizes) |

## D. Intuitiveness
| Task (fresh profile, after the wizard, no terminal) | Clicks | Evidence |
|---|---|---|
| 1. first launch → first verified proof | **1** (« ▶ Prouver » on an example) | `test_usability.py::test_task1` |
| 2. load a different model | **3** (Modèles → row → Charger; or 2 with a double-click) | `test_task2` |
| 3. verify my own `.lean` file | **1** (drag the file on the window; or Ouvrir… → file, auto-verify) | `test_task3` |
| 4. recover from a stopped server | **1** (« Redémarrer le modèle » in the banner) | `test_task4` |

Screenshots of every screen/state were reviewed; fixes applied: truncated buttons/labels, wizard width, hidden spinbox
arrows, disabled-button contrast, dark blocks inside cards, horizontal scrolling, jargon (tooltips for tokens, KV,
batch, top-p…), mismatched « Réparer »/« Recompiler » wording. All visible strings reviewed (French).

## E. Delivery
| Item | Evidence | Status |
|---|---|---|
| README (French, screenshots) | `README.md` | ✅ |
| uninstall.sh lists exactly what was installed | dry run output (asks before deleting; keeps shared elan toolchains and `~/.cache/mathlib` unless `--toolchains`) | ✅ |
| export_offline.sh | real run `--no-models`: app 48 MB + elan 855 MB + workspaces 3.3 GB = **4.2 GB** (+ ~5 GB per GGUF), `zstd -t` OK on all archives; `import_offline.sh` restored into a fresh HOME, app started in 0.47 s (bug found and fixed: relative destination path) | ✅ |
| one-command test suite | `./run_tests.sh` (fast) / `./run_tests.sh --all` | ✅ |
| git repository, clean history, Apache-2.0 | `LICENSE`, `NOTICE` | ✅ |
| reproducible install | `install.sh` (pinned llama.cpp commit, pinned model revision + sha256, pinned Mathlib commits) | ✅ |

## F. Natural language → Lean, LaTeX, loop guard (added 2026-10-04)
| Item | Evidence | Status |
|---|---|---|
| Plain-language problem → Lean statement checked by Lean (real models, 3 problems incl. French + LaTeX + English) | `bench/translate_real.jsonl`: 3/3 compile at try 1, 9.5–21 s | ✅ |
| Two models on one 8 GB GPU: translate → switch → prove, real | `test_integration.py::test_translate_then_prove_with_model_switch` | ✅ |
| Formalizer prompt is the model-card prompt; retries on invalid Lean / loops; keeps last statement when giving up | `test_leancheck.py::test_formalize_prompt_is_the_model_card_prompt`, `test_prover_fake.py::test_formalizer_*` (real Lean 4.9) | ✅ |
| Mandatory human review step in the UI | `docs/screenshots/08_enonce_a_relire.png`; `test_gui.py::test_translate_needs_text_and_model` | ✅ |
| Loop guard (chat screenshot reported by the user) | `test_tex_and_loop.py::test_chat_stream_stops_a_loop` (stops after <1500 of 5000 chunks), `test_prover_restarts_after_a_loop`, `test_loop_detector` (no false positive on 60 × `· norm_num`) | ✅ |
| .tex import (theorems/lemmas/exercises, comments ignored, choice dialog) | `test_extract_statements_from_tex`, `test_import_tex_fills_problem_box` | ✅ |
| LaTeX export compiles (XeLaTeX), cannot be broken by proof content | `test_exported_tex_compiles_with_xelatex`, `test_export_escapes_plain_text_and_keeps_latex`; real export `docs/exemple_export.pdf` | ✅ |
| Beginner help (Lean in 2 minutes, glossary, why review) | `docs/screenshots/06b_aide.png`, `test_help_dialog_opens` | ✅ |
| Overleaf | exported file / copied code / configurable URL. **Not automated**: the local instance answers 404 on 127.0.0.1:80 and an API import would need the user's login | ⚠️ by design |
| Faithfulness of a *compiling* translation | cannot be measured automatically → forced human review | ⚠️ limit |

## Defects found by this acceptance loop and fixed (see DECISIONS D8–D11)
1. Restart after a server crash loaded the model on the CPU (stale VRAM snapshot) → free VRAM computed excluding our own process.
2. Race: model load before the first `nvidia-smi` reading → CPU → load now waits for a GPU snapshot.
3. llama-server host prompt cache up to 8 GiB → bounded to 1 GiB.
4. Lean 4.9 workspace reported "not built" (old Lake layout) and tests silently fell back to another workspace → both layouts + strict tests.
5. Segfault from a lambda capturing its emitting QProcess → bound slots.
6. GUI memory growth (+20 MB / 30 min): one ChatStream leaked per attempt and the hidden server-log widget kept ~6.6 KB per line → streams freed, log buffered as text and rendered only when visible. Re-soak: flat.

## Not verified / known limits
- GPU-unavailable path is tested by simulation only (the real GPU was not removed).
- Olympiad-level miniF2F problems often fail with the 8B model (3/8 failures in BENCH §4); long answers (up to ~16k tokens) can take ~8 min per attempt.
- Quality of Q4_K_M vs Q5_K_M was not compared statistically (choice made on speed + VRAM headroom).
