# ACCEPTANCE — evidence for each item

Machine: RTX 4060 Laptop 8 GB (60 W), Ryzen 7 7735HS, EndeavourOS, kernel 7.2.7. Test log of the full run:
`logs/full_tests_prover49.log` (not versioned; reproduce with `LAS_TEST_WS=lean-prover49 ./run_tests.sh --all`).
Result of the last full run: **52 passed** + usability tasks 2/3 re-run after a test fix (**2 passed**) → 54/54.

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
| CUDA out-of-memory | automatic retry with smaller ctx / fewer layers (`LlamaServer._finished`); real OOM run: see below | see C-OOM |
| disk nearly full warning | `test_disk_nearly_full_warns` | ✅ |
| GPU unavailable → CPU fallback message | `test_gpu_unavailable_falls_back_to_cpu` | ✅ (simulated) |
| double click / double launch | `test_double_click_prove_starts_once`, `test_double_launch_forwards_and_exits` | ✅ |
| power loss (kill -9 GUI) → clean recovery | `test_kill9_gui_recovers_cleanly` (atomic JSON, stale lock detected, « Session restaurée ») | ✅ |
| 30-minute soak | `bench/soak.json` — see section below | see below |

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
| GUI thread never blocked | all I/O via QProcess/QNetworkAccessManager/thread pool; soak worst event-loop lag (see below) |
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
| export_offline.sh | see section below | see below |
| one-command test suite | `./run_tests.sh` (fast) / `./run_tests.sh --all` | ✅ |
| git repository, clean history, Apache-2.0 | `LICENSE`, `NOTICE` | ✅ |
| reproducible install | `install.sh` (pinned llama.cpp commit, pinned model revision + sha256, pinned Mathlib commits) | ✅ |
