# AUDIT — Phase 0 (2026-10-03 15:20, read-only)

Machine: Acer laptop, AMD Ryzen 7 7735HS (16 threads), 30 GiB RAM + 31 GiB swap, on AC (battery full).
OS: EndeavourOS (Arch), kernel 7.2.7-arch1-1. `/` btrfs (445G, 300G free), `/home` ext4 (1.8T, **502G free**).
GPU: NVIDIA GeForce RTX 4060 Max-Q / Mobile (AD107M, Ada, sm_89), **8188 MiB VRAM** (nvidia-smi), ~480 MiB used at idle
(Xorg, kwin_wayland, sunshine). Hybrid with AMD iGPU (amdgpu loaded). Kernel driver in use: `nvidia` (nvidia-open 615.71.09).
Network: reachable (huggingface.co HTTP 200). (sudo not needed: no root change was made).
Snapshots: timeshift 26.09 + timeshift-autosnap present (btrfs root); last snapshot 2026-10-03 13:48.

| Component | Present | Version | Action |
|---|---|---|---|
| NVIDIA driver | yes | nvidia-open 615.71.09, CUDA UMD 13.4 | keep (works; Ada → open driver is correct) |
| nvidia-inst | yes | 26.1.1 | not needed (driver already correct) |
| CUDA toolkit | yes | cuda 13.4.2 (nvcc V13.4.92) | keep; test kernel sm_89 compiled+ran with gcc 16 |
| gcc | yes | 16.2.1 (+gcc15 15.3) | keep (gcc16 works as nvcc host) |
| base-devel / cmake / git / ripgrep | yes | 1-2 / 4.4.4 / 2.56 / 15.2 | keep |
| python | yes | 3.14.7 | keep |
| uv | yes | 0.10.12 (~/.local/bin) | keep |
| pyside6 (Qt6 binding) | yes | 6.11.2 (repo) | keep |
| pytest / pytest-qt | pytest yes / pytest-qt no | 9.1.1 / – | install pytest-qt in user venv |
| AUR helper | paru | 2.1.0 | keep (yay absent) |
| llama.cpp / llama-server | **no** (no AUR `llama.cpp-cuda` either) | – | **build from source** (CUDA, arch 89) |
| ollama | yes (manual /usr/local/bin) | 0.18.2, service inactive, 5 models | keep untouched; not used (see DECISIONS) |
| elan | yes | 4.2.1 | keep |
| Lean toolchains | yes | 4.22.0, 4.23.0-rc2, 4.29.0-rc6, 4.29.1, 4.30.0-rc2, 4.32.1 | add v4.9.0-rc1 and v4.34.1 |
| ~/.cache/mathlib | yes | 401 MB | reuse |
| lean4web | yes | user service `lean4web.service` on 127.0.0.1:8890 | keep, read-only; GUI links to it |
| Existing Lean projects | yes | lean4web/Projects/MathlibDemo (v4.29.0-rc6, Mathlib built, 7.3G); <projet de cours> (v4.23.0-rc2, 5.6G) | detected as read-only extra workspaces, never modified |
| ~/models | no | – | create |
| Pending updates | 9 (ffmpeg, vulkan, spirv…) | no kernel/driver | not required; not applied |

## Root actions needed
None. All requirements are satisfied by installed packages or user-level installs (see `root_steps.sh`, which is a documented no-op).
