# STATE (memory across context resets / reboots)

## Snapshot
No root change was needed (D1) → no timeshift snapshot taken. Latest existing: 2026-10-03_13-48-44.

## Done (with evidence)
- 15:20 Phase 0 audit OK → AUDIT.md. root_steps.sh = documented no-op.
- 15:55 llama.cpp 436f6f8 built (CUDA sm_89, vendor/build.log "BUILD rc=0"). llama-bench qwen3-8B Q3_K_L:
  pp512 1844 t/s, tg128 36.85 t/s; "found 1 CUDA devices ... RTX 4060 Laptop GPU, VRAM 7888 MiB".
- 15:57 Direct `lean --json` (no lake) works; cold 28 s / warm 3.8 s on lean4web MathlibDemo.
- App written: app/lean_ai_station (config, gguf, leancheck, workspaces, services, errors, examples, ui/*).
- Tests: tests/test_leancheck.py + test_core.py (22, incl. byte-identical Goedel get_error_str),
  test_gui.py (7), test_integration.py (Lean 6/6 on MathlibDemo; server 4/4 with deepseek-r1 blob),
  test_process.py (double launch, kill -9 recovery: 2/2). Segfault on destroyed LeanCompiler fixed (bound slots).
- Cold start (offscreen, full window populated): ~0.55 s wall.
- Launcher bin/lean-ai-station + ~/.local/share/applications/lean-ai-station.desktop (validated) + icon.
- uninstall.sh, export_offline.sh written (syntax OK, not yet run).

- 17:00 Q4_K_M + Q5_K_M downloaded, sha256 OK; chat template identical to official (4168 chars). BENCH.md written; D7 defaults.
- 17:05 Real GUI run (scripts/screenshots.py, lean-current): wizard self-test 3/3, proof found in GUI, chat, crash → restart.
  Bug fixed: stale VRAM snapshot after crash made restart go CPU (0/36 layers) → free_excluding(own pid). Now 37/37, 4.0 s.
- 17:20 Idle: GUI 0.10 % CPU / 106 MiB, server 0.20 % CPU / 882 MiB RSS, VRAM 7047 MiB, GPU 1 %.
  kill -9 GUI → llama-server gone ≤300 ms, VRAM back to 484 MiB; relaunch OK; SIGTERM → clean (lock+pidfile removed).
- lean-current (Mathlib v4.34.1) built: "Build completed successfully (8925 jobs)".

- 17:25 Offline A-test OK in `unshare -rn` (lean-current): pairs + mathd_algebra_478 proved attempt 1 (28.5 s, 32.2 s),
  curl to huggingface fails inside ns. Fixed: GPU-snapshot race (D9), RAM prompt cache bounded (D8).
- uninstall.sh dry-run OK (pkill restricted to our binary).

## In progress (background, 17:26)
- Soak 30 min DONE: 47 cycles, 46 ok; VRAM flat 7049-7057 MiB; GUI RSS 108→132 MB (+24 MB, investigate: slow growth?); worst lag 447 ms (at model load), else 30-60 ms.
- lean-prover49 Mathlib build ~4157/4360 → logs/build_prover49.log.
- Model: scripts/download_model.sh Q4_K_M Q5_K_M (parallel, sha256) → logs/download.log.
- lean-prover49: Mathlib built from source (cache 404, D6) → logs/build_prover49.log.
- lean-current: v4.34.1 cache get → logs/setup_current.log.

## Status (2026-10-04): release 1.0.0
- Features: NL→Lean (Formalizer), prove (Prover), proof→French (Qwen3-8B), .tex in/out, loop guard, Chat tab removed,
  multi-distro installer (Fedora 44 / Ubuntu 24.04 / 22.04 container-tested, CPU backend).
- Release: tag v1.0.0 + GitHub release (see CHANGELOG.md). Local branch `dev-history` is NOT pushed (personal paths).
- Acceptance: ACCEPTANCE.md sections A–G.

## Gotchas
- NEVER `pkill -f`/`pgrep -f` with a pattern contained in the command itself (use "[x]yz" trick).
- The user's own GUI instance may hold the GPU (7 GB): ask before closing it; tests need the GPU free.
- New Lean prints "declaration uses `sorry`" with backticks; Lake 4.9 puts oleans in .lake/build/lib.
- llama-server needs `-lv 4` to log "offloaded N/M layers to GPU".
- Never connect a lambda that captures its own emitter (double delete).
