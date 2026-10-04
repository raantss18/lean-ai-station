# BENCH — Goedel-Prover-V2-8B on RTX 4060 Laptop (8 GB, 60 W Max-Q)

llama.cpp 436f6f8 (CUDA 13.4, sm_89), all 37/37 layers on GPU. Raw data in `bench/`.
Caveat: measured while Mathlib was compiling on the CPU (load avg ~30, user services also busy) — GPU-bound
numbers are barely affected; Lean compile times are inflated (see C in ACCEPTANCE.md for idle numbers).

## 1. Settings matrix (Q4_K_M, `llama-bench`, pp2048 / tg128, r=2) — `bench/q4_matrix.jsonl`

| FA | KV cache | ubatch | pp @0 | tg @0 | pp @8k | tg @8k |
|---|---|---|---:|---:|---:|---:|
| off | f16 | 512 | 1403 | 42.2 | 545 | 30.6 |
| on | f16 | 512 | **1843** | **43.8** | **1362** | **34.7** |
| on | q8_0 | 512 | 1795 | 43.1 | 1303 | 32.5 |
| on | q4_0 | 512 | 1764 | 42.6 | 1311 | 32.4 |
| on | q8_0 | 256 | 1765 | 42.8 | 1261 | 32.6 |
| on | q8_0 | 1024 | 1643 | 42.4 | 1228 | 32.4 |
| on | q8_0 | 2048 | 1512 | 42.2 | 1212 | 32.2 |

→ Flash attention is essential (+150 % prompt speed at 8k depth). ubatch 512 is best. f16 KV is ~6 % faster than q8_0
but needs twice the memory.

## 2. VRAM per configuration (llama-server, measured with nvidia-smi; desktop baseline 484 MiB of 8188)

| Model | ctx | KV | VRAM used | headroom | status |
|---|---:|---|---:|---:|---|
| Q4_K_M | 16384 | f16 | 7473 MiB | 0.7 GB | ok |
| Q4_K_M | 16384 | q8_0 | 6395 MiB | 1.8 GB | ok |
| **Q4_K_M** | **24576** | **q8_0** | **7047 MiB** | **1.1 GB** | **chosen** |
| Q4_K_M | 32768 | q8_0 | 7699 MiB | 0.5 GB | too tight for a desktop |
| Q4_K_M | 40960 | q8_0 | — | — | CUDA OOM |
| Q4_K_M | 32768 | f16 | — | — | CUDA OOM |
| Q5_K_M | 16384 | q8_0 | 7107 MiB | 1.1 GB | ok (auto-selected ctx if user loads Q5) |
| Q5_K_M | 24576 | q8_0 | 7759 MiB | 0.4 GB | too tight |

## 3. Q4_K_M vs Q5_K_M (FA on, q8_0, ub 512) — `bench/q4_vs_q5.jsonl`

| depth | Q4 pp | Q4 tg | Q5 pp | Q5 tg |
|---:|---:|---:|---:|---:|
| 0 | 1855 | 44.8 | 1724 | 39.2 |
| 8192 | 1350 | 33.4 | 1290 | 29.6 |
| 16384 | 989 | 26.6 | 952 | 24.0 |

→ Q5 is 10–12 % slower and cannot keep a 24k context with safe headroom. **Q4_K_M chosen** (see D7).

## 4. Real proofs (Goedel prompt, T=1.0, top-p 0.95; Q4_K_M, 24k ctx q8_0) — `bench/minif2f_lengths_q8_24k.jsonl`

Workspace: lean4web MathlibDemo (Lean 4.29) — run before lean-prover49 finished building.

| problem | result | attempts | first answer (tokens) | tok/s | total |
|---|---|---:|---:|---:|---:|
| et_logique (example) | ✔ | 1 | 479 | 41.9 | 54 s |
| somme_pairs (example) | ✔ | 1 | 1106 | 40.7 | 39 s |
| square_equation_solution (example) | ✔ | 1 | 1357 | 39.6 | 57 s |
| mathd_numbertheory_247 | ✔ | 1 | 1490 | 39.8 | 51 s |
| mathd_numbertheory_582 | ✔ | 1 | 2402 | 38.4 | 76 s |
| mathd_numbertheory_353 | ✘ | 3 | 5401 | 34.8 | 274 s |
| amc12b_2021_p3 | ✘ | 3 | **16091** | 30.1 | 903 s |
| algebra_amgm_sum1toneqn_prod1tonleq1 | ✘ | 3 | 9000 | 33.5 | 592 s |

→ answers range 0.5k–16k tokens (correction rounds ~0.9–3.8k): a 16k context would truncate the long tail, hence
**ctx 24576, max_tokens 16384**. First-token latency 0.10–0.15 s. Model load (server ready) 3.3–4.2 s.

## 5. Live server (chosen config)
- Generation through the API: **41.5 tok/s** (919 tokens), prompt 564 tok/s on a short prompt.
- GPU utilisation during generation: **92–97 %**, power at the 60 W cap (the laptop GPU is power-limited, so this is the ceiling).
- Idle loaded server: GPU 1 %, 17 W, VRAM 7047 MiB.

## Chosen defaults (Serveur → « Valeurs recommandées »)
`-ngl 99 -c 24576 -fa on -ctk q8_0 -ctv q8_0 -b 2048 -ub 512 -np 1`, sampling T=1.0, top-p 0.95 (Goedel), max 16384 tokens/answer.

## 6. Natural language → Lean (Goedel-Formalizer-V2-8B Q4_K_M, `bench/translate_real.jsonl`)
| Problem (as typed) | Result | Time |
|---|---|---|
| « Montrer que la somme de deux entiers pairs est paire. » | `∀ (a b : ℤ), Even a → Even b → Even (a + b)`, compiles, try 1 | 21.1 s (incl. model load) |
| « Soient $a$ et $b$ deux réels. Montrer que $2ab \le a^2 + b^2$. » | `(a b : ℝ) : 2 * a * b ≤ a^2 + b^2`, compiles, try 1 | 9.5 s |
| « Prove that for every natural number n, n(n+1)(n+2) is divisible by 6. » | `∀ n : ℕ, 6 ∣ n * (n + 1) * (n + 2)`, compiles, try 1 | 9.7 s |

Generation ≈ 44 tok/s, 310–340 tokens per translation. Switching prover ↔ formalizer on the 8 GB GPU: server restart ≈ 4–8 s.
Whether a *compiling* translation is *faithful* is not measured here (only a human can judge): that is why the UI
forces a « relisez » step.

## 7. CPU only (no GPU) — Ryzen 7 7735HS, 8 threads, Goedel-Prover-V2-8B Q4_K_M, `llama-bench -ngl 0`
| test | CPU | GPU (RTX 4060) | ratio |
|---|---:|---:|---:|
| prompt pp256 | 15.2 t/s | ≈ 1 850 t/s | ≈ 120 × |
| generation tg32 | 5.1 t/s | ≈ 44 t/s | ≈ 9 × |

A typical proof attempt (1–5 k tokens) therefore takes ≈ 4–16 min on the CPU instead of 0.5–2 min. The CPU path is what the
installer builds with `--backend cpu` and what the app falls back to when no NVIDIA GPU is detected.

## 8. Proof → French explanation (Qwen3-8B Q4_K_M, thinking off)
10.6 s (439 tokens) for a 14-line proof, 19.0 s (787 tokens) for a 20-line proof; prover model unusable for this task (D15).
