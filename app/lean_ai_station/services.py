"""Qt services: llama-server lifecycle, streaming chat client, Lean compiler, prove loop, GPU monitor.

Everything is event-driven on the GUI thread (QProcess / QNetworkAccessManager), so the UI never
blocks. Child processes are started through `setpriv --pdeathsig KILL` so they die with the app,
even after `kill -9` of the GUI."""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import signal
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QProcess, QProcessEnvironment, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest

from . import config, leancheck
from .gguf import GGUFError, GGUFInfo, read_info
from .workspaces import Workspace

SETPRIV = shutil.which("setpriv")


def guarded(program: str, args: list[str]) -> tuple[str, list[str]]:
    if SETPRIV:
        return SETPRIV, ["--pdeathsig", "KILL", "--", program, *args]
    return program, args


# ---------------------------------------------------------------- network guard
class Net:
    offline = True

    @staticmethod
    def is_local(url: QUrl | str) -> bool:
        host = (url.host() if isinstance(url, QUrl) else QUrl(url).host()).lower()
        return host in ("127.0.0.1", "localhost", "::1", "")

    @classmethod
    def allowed(cls, url: QUrl | str) -> bool:
        return cls.is_local(url) or not cls.offline


class GuardedNAM(QNetworkAccessManager):
    """Refuses every non-loopback request while offline mode is on."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))

    def createRequest(self, op, request, data=None):  # noqa: N802 (Qt API)
        if not Net.allowed(request.url()):
            request = QNetworkRequest(QUrl("blocked-offline:"))
        return super().createRequest(op, request, data)


# ---------------------------------------------------------------- helpers
def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _proc_cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def kill_orphan_server() -> bool:
    """Kill a llama-server left by a previous crashed session (pid file). Returns True if one was killed."""
    try:
        pid = int(config.SERVER_PID_FILE.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    killed = False
    if "llama-server" in _proc_cmdline(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(30):
                time.sleep(0.1)
                if not _proc_cmdline(pid):
                    break
            else:
                os.kill(pid, signal.SIGKILL)
            killed = True
        except ProcessLookupError:
            pass
    try:
        config.SERVER_PID_FILE.unlink()
    except OSError:
        pass
    return killed


def llama_bin(name: str) -> Path:
    return config.LLAMA_BIN_DIR / name


# ---------------------------------------------------------------- GPU monitor
class GpuMonitor(QObject):
    stats = Signal(dict)        # {name, total, used, free, temp, util, apps: {pid: MiB}} (MiB, °C, %)
    unavailable = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last: dict | None = None
        self._proc: QProcess | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)

    def start(self, interval_ms: int = 3000):
        self.poll()
        self._timer.start(interval_ms)

    def set_interval(self, ms: int):
        self._timer.setInterval(ms)

    def stop(self):
        self._timer.stop()

    def poll(self):
        if self._proc is not None:
            return
        exe = shutil.which("nvidia-smi")
        if not exe:
            self.last = None
            self.unavailable.emit("nvidia-smi introuvable")
            return
        p = QProcess(self)
        self._proc = p
        p.finished.connect(self._done)
        p.errorOccurred.connect(self._done)
        p.start(exe, ["--query-gpu=name,memory.total,memory.used,temperature.gpu,utilization.gpu",
                      "--format=csv,noheader,nounits"])

    def _done(self, *_):
        p = self._proc
        if p is None or self.sender() is not p:
            return
        out = bytes(p.readAllStandardOutput()).decode(errors="replace").strip()
        p.deleteLater()
        try:
            name, total, used, temp, util = [x.strip() for x in out.splitlines()[0].split(",")]
            self._pending_stats = {"name": name, "total": int(total), "used": int(used),
                                   "free": int(total) - int(used), "temp": int(temp), "util": int(util), "apps": {}}
        except (ValueError, IndexError):
            self._proc = None
            self.last = None
            self.unavailable.emit(out or "nvidia-smi n'a rien renvoyé")
            return
        # second query in the same poll: per-process VRAM (lets us ignore our own server's share)
        q = QProcess(self)
        self._proc = q
        q.finished.connect(self._apps_done)
        q.errorOccurred.connect(self._apps_done)
        q.start(shutil.which("nvidia-smi"), ["--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"])

    def _apps_done(self, *_):
        q = self._proc
        if q is None or self.sender() is not q:
            return
        self._proc = None
        out = bytes(q.readAllStandardOutput()).decode(errors="replace")
        q.deleteLater()
        d = self._pending_stats
        for line in out.splitlines():
            try:
                pid, mem = [int(x.strip()) for x in line.split(",")[:2]]
                d["apps"][pid] = d["apps"].get(pid, 0) + mem
            except ValueError:
                continue
        self.last = d
        self.stats.emit(d)

    def free_excluding(self, pid: int | None) -> int | None:
        """Free VRAM (MiB) if process `pid` released its memory; consistent even when the snapshot is stale."""
        d = self.last
        if not d:
            return None
        return d["free"] + (d["apps"].get(pid, 0) if pid else 0)


# ---------------------------------------------------------------- llama-server
@dataclass
class LaunchPlan:
    model: Path
    info: GGUFInfo | None
    gpu_layers: int
    ctx: int
    kv_type: str
    note: str = ""        # plain-French explanation of any automatic downgrade


def plan_launch(model: Path, s: config.ServerSettings, vram_free_mib: int | None) -> LaunchPlan:
    """Choose GPU layers / context so the model fits; never refuses, downgrades with an explanation."""
    try:
        info = read_info(model)
    except (OSError, GGUFError):
        info = None
    ctx, ngl, kv = s.ctx_size, s.gpu_layers, s.kv_type if s.flash_attn else "f16"
    if info is not None and info.ctx_train:
        ctx = min(ctx, info.ctx_train)
    if vram_free_mib is None:
        return LaunchPlan(model, info, 0, min(ctx, 8192), kv,
                          "Aucune carte graphique NVIDIA utilisable : le modèle tourne sur le processeur (beaucoup plus lent).")
    if info is None:
        return LaunchPlan(model, None, ngl, ctx, kv)
    budget = vram_free_mib * 1024 * 1024 - 450 * 1024 * 1024    # CUDA context + compute buffers
    need = info.size + info.kv_bytes(ctx, kv) + 300 * 1024 * 1024
    if need <= budget or ngl < 99:
        return LaunchPlan(model, info, ngl, ctx, kv)
    # 1) shrink context down to 8192
    c = ctx
    while c > 8192 and info.size + info.kv_bytes(c, kv) + 300 * 1024 * 1024 > budget:
        c //= 2
    if info.size + info.kv_bytes(c, kv) + 300 * 1024 * 1024 <= budget:
        return LaunchPlan(model, info, ngl, c, kv,
                          f"Mémoire graphique limitée : contexte réduit à {c} tokens pour que tout tienne sur la carte.")
    # 2) partial offload
    per_layer = info.size / max(info.n_layers + 1, 1)
    layers = int((budget - info.kv_bytes(c, kv) - 300 * 1024 * 1024) // per_layer)
    layers = max(0, min(info.n_layers, layers))
    return LaunchPlan(model, info, layers, c, kv,
                      f"Le modèle est trop gros pour la carte graphique : {layers}/{info.n_layers} couches sur la carte, "
                      "le reste sur le processeur (plus lent). Un modèle plus petit (Q4_K_M) serait plus rapide.")


class LlamaServer(QObject):
    STOPPED, STARTING, READY, STOPPING = "stopped", "starting", "ready", "stopping"
    stateChanged = Signal(str)
    log = Signal(str)
    failed = Signal(str, str)     # kind, details   kinds: crashed, oom, bad_model, no_binary, port, timeout
    notice = Signal(str)          # plain-French info (automatic downgrades)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = self.STOPPED
        self.proc: QProcess | None = None
        self.plan: LaunchPlan | None = None
        self.port = 8765
        self.offload = ""            # "37/37"
        self.load_seconds: float | None = None
        self._t0 = 0.0
        self._tail: list[str] = []
        self._oom = False
        self._bad = False
        self._retries = 0
        self._settings: config.ServerSettings | None = None
        self._pending = None
        self.last_pid: int | None = None
        self._nam = GuardedNAM(self)
        self._health = QTimer(self)
        self._health.setInterval(400)
        self._health.timeout.connect(self._poll_health)
        self._health_reply: QNetworkReply | None = None
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._force_kill)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def model_path(self) -> Path | None:
        return self.plan.model if self.plan else None

    def _set(self, st: str):
        if st != self.state:
            self.state = st
            self.stateChanged.emit(st)

    # -- start / stop
    def start(self, model: Path, s: config.ServerSettings, vram_free_mib: int | None):
        if self.state in (self.STARTING, self.READY):
            self.stop(restart_with=(model, s, vram_free_mib))
            return
        if self.state == self.STOPPING:
            self._pending = (model, s, vram_free_mib)
            return
        exe = llama_bin("llama-server")
        if not exe.exists():
            self.failed.emit("no_binary", f"{exe} introuvable")
            return
        if not model.exists():
            self.failed.emit("bad_model", f"Fichier absent : {model}")
            return
        self._settings = s
        self._retries = 0
        self._launch(plan_launch(model, s, vram_free_mib))

    def _launch(self, plan: LaunchPlan):
        s = self._settings or config.ServerSettings()
        kill_orphan_server()
        port = s.port
        if not port_free(port):
            for p in range(port + 1, port + 50):
                if port_free(p):
                    self.notice.emit(f"Le port {port} est déjà utilisé : le serveur utilise le port {p}.")
                    port = p
                    break
            else:
                self.failed.emit("port", f"Aucun port libre à partir de {port}")
                return
        self.port, self.plan = port, plan
        if plan.note:
            self.notice.emit(plan.note)
        fa = s.flash_attn and plan.gpu_layers > 0
        kv = plan.kv_type if fa else "f16"
        args = ["-m", str(plan.model), "--host", "127.0.0.1", "--port", str(port),
                "-ngl", str(plan.gpu_layers), "-c", str(plan.ctx), "-b", str(s.batch_size), "-ub", str(s.ubatch_size),
                "-fa", "on" if fa else "off", "-ctk", kv, "-ctv", kv, "-t", str(s.threads),
                "-np", "1", "--jinja", "--reasoning-format", "none", "--no-webui", "--fit", "off", "-lv", "4", "-cram", "1024"]
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("LD_LIBRARY_PATH", str(config.LLAMA_BIN_DIR))
        if plan.gpu_layers == 0:
            env.insert("CUDA_VISIBLE_DEVICES", "")
        p.setProcessEnvironment(env)
        p.setProcessChannelMode(QProcess.MergedChannels)
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._finished)
        p.errorOccurred.connect(self._error)
        self.proc, self._tail, self._oom, self._bad, self.offload = p, [], False, False, ""
        p.started.connect(self._started)
        self._t0 = time.monotonic()
        self.load_seconds = None
        self._set(self.STARTING)
        prog, a = guarded(str(llama_bin("llama-server")), args)
        self.log.emit("$ llama-server " + " ".join(args))
        p.start(prog, a)
        self._health.start()

    def stop(self, restart_with=None):
        self._pending = restart_with
        self._health.stop()
        if not self.proc or self.state == self.STOPPED:
            self._set(self.STOPPED)
            self._run_pending()
            return
        self._set(self.STOPPING)
        self.proc.terminate()
        self._kill_timer.start(5000)

    def _force_kill(self):
        if self.proc:
            self.proc.kill()

    def shutdown_blocking(self, timeout_ms: int = 4000):
        """Used on application exit only."""
        self._pending = None
        self._health.stop()
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.state = self.STOPPING
            self.proc.terminate()
            if not self.proc.waitForFinished(timeout_ms):
                self.proc.kill()
                self.proc.waitForFinished(2000)
        try:
            config.SERVER_PID_FILE.unlink()
        except OSError:
            pass

    def _run_pending(self):
        pending, self._pending = getattr(self, "_pending", None), None
        if pending:
            self.start(*pending)

    # -- process events
    def _read(self):
        p = self.sender()
        if not isinstance(p, QProcess):
            return
        text = bytes(p.readAllStandardOutput()).decode("utf-8", "replace")
        for line in text.splitlines():
            if not line.strip():
                continue
            self._tail = (self._tail + [line])[-60:]
            low = line.lower()
            m = re.search(r"offloaded (\d+)/(\d+) layers to gpu", low)
            if m:
                self.offload = f"{m.group(1)}/{m.group(2)}"
            if "out of memory" in low or "cudamalloc failed" in low or "failed to allocate" in low:
                self._oom = True
            if ("failed to load model" in low or "error loading model" in low or "invalid magic" in low
                    or "gguf_init_from_file" in low and "failed" in low):
                self._bad = True
            self.log.emit(line)

    def _started(self):
        p = self.sender()
        if isinstance(p, QProcess):
            self.last_pid = p.processId()

    def _poll_health(self):
        if self._health_reply is not None:
            return
        if time.monotonic() - self._t0 > 300:
            self._health.stop()
            self.failed.emit("timeout", "Le serveur ne répond pas après 5 minutes.\n" + "\n".join(self._tail[-15:]))
            self.stop()
            return
        r = self._nam.get(QNetworkRequest(QUrl(self.url + "/health")))
        self._health_reply = r
        r.finished.connect(self._health_done)

    def _health_done(self):
        r = self._health_reply
        if r is None:
            return
        self._health_reply = None
        code = r.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        r.deleteLater()
        if code == 200 and self.state == self.STARTING:
            self._health.stop()
            self.load_seconds = time.monotonic() - self._t0
            try:
                pid = self.proc.processId() if self.proc else 0
                config.atomic_write_text(config.SERVER_PID_FILE, f"{pid} {self.port} {self.plan.model if self.plan else ''}\n")
            except OSError:
                pass
            self._set(self.READY)

    def _error(self, err):
        p = self.sender()
        if err == QProcess.FailedToStart and p is self.proc:
            self._health.stop()
            self.proc = None
            self._set(self.STOPPED)
            self.failed.emit("no_binary", p.errorString())

    def _finished(self, code: int, _status=None):
        p = self.sender()
        if not isinstance(p, QProcess):
            return
        if p is not self.proc:
            p.deleteLater()
            return
        self._kill_timer.stop()
        self._health.stop()
        self.proc = None
        p.deleteLater()
        was = self.state
        try:
            config.SERVER_PID_FILE.unlink()
        except OSError:
            pass
        details = "\n".join(self._tail[-25:])
        if was == self.STOPPING:
            self._set(self.STOPPED)
            self._run_pending()
            return
        self._set(self.STOPPED)
        if was == self.STARTING and self._oom and self.plan and self._retries < 3:
            # automatic fallback: smaller context first, then fewer GPU layers
            self._retries += 1
            pl = self.plan
            if pl.ctx > 8192:
                new = LaunchPlan(pl.model, pl.info, pl.gpu_layers, max(8192, pl.ctx // 2), pl.kv_type,
                                 f"Mémoire graphique insuffisante : nouvel essai avec un contexte de {max(8192, pl.ctx // 2)} tokens.")
            else:
                n = pl.info.n_layers if pl.info else 36
                cur = min(pl.gpu_layers, n)
                new = LaunchPlan(pl.model, pl.info, max(0, int(cur * 0.7)), pl.ctx, pl.kv_type,
                                 f"Mémoire graphique insuffisante : nouvel essai avec {max(0, int(cur * 0.7))} couches sur la carte.")
            self._launch(new)
            return
        if self._oom:
            self.failed.emit("oom", details)
        elif self._bad:
            self.failed.emit("bad_model", details)
        else:
            self.failed.emit("crashed", details)


# ---------------------------------------------------------------- streaming chat client
class ChatStream(QObject):
    delta = Signal(str)
    done = Signal(dict)            # {text, finish_reason, tokens, ttft, tps, seconds}
    error = Signal(str, str)       # kind, details   kinds: unreachable, http, stalled, cancelled

    def __init__(self, base_url: str, parent=None):
        super().__init__(parent)
        self.base_url = base_url
        self._nam = GuardedNAM(self)
        self.reply: QNetworkReply | None = None
        self._buf = b""
        self._text: list[str] = []
        self._finish = None
        self._timings: dict = {}
        self._t0 = 0.0
        self._ttft: float | None = None
        self._chunks = 0
        self._cancelled = False
        self._tail = ""                 # last characters of the answer, for loop detection
        self._loop: tuple[int, int] | None = None
        self._watch = QTimer(self)
        self._watch.setInterval(5000)
        self._watch.timeout.connect(self._check_stall)
        self._last = 0.0
        self.stall_s = 180

    def start(self, messages: list[dict], temperature: float, top_p: float, max_tokens: int,
              extra: dict | None = None):
        body = {"messages": messages, "stream": True, "temperature": temperature, "top_p": top_p,
                "max_tokens": max_tokens, "cache_prompt": True, "stream_options": {"include_usage": True},
                "seed": random.randint(0, 2**31 - 1)}   # explicit per-request seed (server already varies; kept explicit)
        body.update(extra or {})
        req = QNetworkRequest(QUrl(self.base_url + "/v1/chat/completions"))
        req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        req.setTransferTimeout(0)
        self._t0 = self._last = time.monotonic()
        self.reply = self._nam.post(req, QByteArray(json.dumps(body).encode()))
        self.reply.readyRead.connect(self._read)
        self.reply.finished.connect(self._finished)
        self._watch.start()

    def cancel(self):
        self._cancelled = True
        if self.reply is not None:
            self.reply.abort()

    @property
    def text(self) -> str:
        return "".join(self._text)

    def _check_stall(self):
        if self.reply is not None and time.monotonic() - self._last > self.stall_s:
            self._stalled = True
            self.reply.abort()

    def _read(self):
        if self.reply is None:
            return
        self._buf += bytes(self.reply.readAll())
        self._last = time.monotonic()
        status = self.reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if status and int(status) >= 400:
            return  # body handled in _finished
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                continue
            try:
                d = json.loads(payload)
            except ValueError:
                continue
            if "timings" in d:
                self._timings = d["timings"]
            for ch in d.get("choices") or []:
                if self._loop is not None:
                    break                       # loop already detected: ignore what is still in flight
                delta = ch.get("delta") or {}
                piece = (delta.get("reasoning_content") or "") + (delta.get("content") or "")
                if piece:
                    if self._ttft is None:
                        self._ttft = time.monotonic() - self._t0
                    self._chunks += 1
                    self._text.append(piece)
                    self._tail = (self._tail + piece)[-12000:]
                    self.delta.emit(piece)
                    if self._chunks % 40 == 0 and self._loop is None:
                        self._loop = leancheck.detect_loop(self._tail)
                        if self._loop and self.reply is not None:
                            self.reply.abort()      # the model is going round in circles: stop wasting minutes
                if ch.get("finish_reason"):
                    self._finish = ch["finish_reason"]

    def _finished(self):
        try:
            self._finish_reply()
        finally:
            if self.reply is None:
                self.deleteLater()   # one stream per request: free it once it has reported (no leak per attempt)

    def _finish_reply(self):
        r = self.reply
        if r is None:
            return
        self._watch.stop()
        self._read()
        err = r.error()
        status = r.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        rest = self._buf.decode("utf-8", "replace")
        self.reply = None
        r.deleteLater()
        if self._loop is not None and not self._cancelled:
            unit, reps = self._loop
            text = self.text
            text = text[: len(text) - unit * (reps - 1)]       # keep the first occurrence of the repeated block
            secs = time.monotonic() - self._t0
            self.done.emit({"text": text, "finish_reason": "loop", "loop": True, "tokens": self._chunks,
                            "ttft": self._ttft, "tps": self._chunks / max(secs - (self._ttft or 0), 1e-6),
                            "seconds": secs, "prompt_tokens": 0})
            return
        if self._cancelled:
            self.error.emit("cancelled", "")
            return
        if getattr(self, "_stalled", False):
            self.error.emit("stalled", f"Aucune donnée reçue depuis {self.stall_s} s.")
            return
        if status and int(status) >= 400:
            try:
                msg = json.loads(rest).get("error", {}).get("message", rest)
            except ValueError:
                msg = rest
            self.error.emit("http", f"HTTP {status}: {msg}")
            return
        if err != QNetworkReply.NoError:
            self.error.emit("unreachable", r.errorString())
            return
        secs = time.monotonic() - self._t0
        t = self._timings
        tokens = int(t.get("predicted_n") or self._chunks)
        tps = float(t.get("predicted_per_second") or (tokens / max(secs - (self._ttft or 0), 1e-6)))
        self.done.emit({"text": self.text, "finish_reason": self._finish or "stop", "tokens": tokens,
                        "ttft": self._ttft, "tps": tps, "seconds": secs, "prompt_tokens": int(t.get("prompt_n") or 0)})


# ---------------------------------------------------------------- Lean compiler
@dataclass
class CompileResult:
    verdict: leancheck.Verdict
    code: str
    seconds: float
    stdout: str
    timed_out: bool = False
    cancelled: bool = False
    infra_error: str = ""      # non-empty: compile could not run (workspace broken...)


class LeanCompiler(QObject):
    finished = Signal(object)    # CompileResult

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: QProcess | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)
        self._state: dict = {}

    @property
    def busy(self) -> bool:
        return self.proc is not None

    def compile(self, code: str, ws: Workspace, timeout_s: int, strict_name: str | None):
        if self.proc is not None:
            raise RuntimeError("compile already running")
        problems = ws.check()
        if problems:
            v = leancheck.Verdict(False, problems[0])
            QTimer.singleShot(0, self, lambda: self.finished.emit(CompileResult(v, code, 0.0, "", infra_error=problems[0])))
            return
        tmpdir = config.CACHE_DIR / "tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        f = tmpdir / f"check_{os.getpid()}_{int(time.time() * 1000)}.lean"
        f.write_text(code, encoding="utf-8")
        p = QProcess(self)
        env = QProcessEnvironment()
        for k, v in ws.env().items():
            env.insert(k, v)
        p.setProcessEnvironment(env)
        p.setWorkingDirectory(str(ws.path))
        p.finished.connect(self._finished)
        p.errorOccurred.connect(self._err)
        self.proc = p
        self._state = {"code": code, "file": f, "name": strict_name, "t0": time.monotonic(),
                       "timed_out": False, "cancelled": False}
        prog, args = guarded(str(ws.lean_bin), ["--json", str(f)])
        p.start(prog, args)
        self._timer.start(max(5, timeout_s) * 1000)

    def cancel(self):
        if self.proc is not None:
            self._state["cancelled"] = True
            self.proc.kill()

    def _timeout(self):
        if self.proc is not None:
            self._state["timed_out"] = True
            self.proc.kill()

    def _err(self, e):
        p = self.sender()
        if e == QProcess.FailedToStart and p is self.proc:
            self._done(p, -1, failed_start=p.errorString())

    def _finished(self, code: int, _status=None):
        self._done(self.sender(), code)

    def _done(self, p: QProcess, code: int, failed_start: str = ""):
        if p is not self.proc:
            return
        self._timer.stop()
        self.proc = None
        out = bytes(p.readAllStandardOutput()).decode("utf-8", "replace")
        err = bytes(p.readAllStandardError()).decode("utf-8", "replace")
        p.deleteLater()
        st = self._state
        try:
            st["file"].unlink()
        except OSError:
            pass
        secs = time.monotonic() - st["t0"]
        if failed_start:
            v = leancheck.Verdict(False, "Lean n'a pas pu démarrer.")
            self.finished.emit(CompileResult(v, st["code"], secs, err, infra_error=failed_start))
            return
        if st["cancelled"]:
            self.finished.emit(CompileResult(leancheck.Verdict(False, "Vérification annulée."), st["code"], secs, out, cancelled=True))
            return
        if st["timed_out"]:
            v = leancheck.Verdict(False, f"Lean a dépassé le délai ({int(secs)} s).",
                                  feedback_en="Compilation timed out. Use simpler, faster tactics (avoid heavy nlinarith/simp searches).")
            self.finished.emit(CompileResult(v, st["code"], secs, out, timed_out=True))
            return
        msgs = leancheck.parse_lean_json(out)
        infra = ""
        if any("object file" in m.text and "does not exist" in m.text for m in msgs) or (
                "unknown package" in out and "Mathlib" in out):
            infra = "Mathlib introuvable ou non compilé dans cet espace."
        if not msgs and code != 0:
            infra = (err or out).strip()[-2000:] or f"code de sortie {code}"
        v = leancheck.judge(st["code"], msgs, st["name"], code)
        self.finished.emit(CompileResult(v, st["code"], secs, out + err, infra_error=infra))


# ---------------------------------------------------------------- prove loop
@dataclass
class Attempt:
    index: int
    kind: str                      # initial | correction
    raw: str = ""
    code: str = ""                 # assembled code that was compiled (without the axiom probe)
    status: str = "génération"     # génération | compilation | accepté | refusé | erreur | annulé
    summary: str = ""
    errors_text: str = ""
    gen_seconds: float = 0.0
    tokens: int = 0
    tps: float = 0.0
    ttft: float | None = None
    compile_seconds: float = 0.0
    finish_reason: str = ""


class Prover(QObject):
    attemptStarted = Signal(int)
    token = Signal(int, str)
    attemptUpdated = Signal(int)
    finished = Signal(bool, str)          # success, French summary
    infraError = Signal(str, str)          # kind, details  (server_down, workspace, ...)

    def __init__(self, server: LlamaServer, compiler: LeanCompiler, parent=None):
        super().__init__(parent)
        self.server, self.compiler = server, compiler
        self.attempts: list[Attempt] = []
        self.running = False
        self.final_code = ""
        self._stream: ChatStream | None = None
        self.compiler.finished.connect(self._compiled)
        self._mine = False

    def start(self, statement: str, ws: Workspace, n_attempts: int, sampling: config.SamplingSettings,
              timeout_s: int, ctx: int):
        self.statement = leancheck.prepare_statement(statement)
        self.name = leancheck.theorem_name(self.statement)
        self.ws, self.n, self.sampling, self.timeout_s, self.ctx = ws, max(1, n_attempts), sampling, timeout_s, ctx
        self.attempts, self.final_code, self.running = [], "", True
        self.messages = [{"role": "user", "content": leancheck.initial_prompt(self.statement)}]
        self._next()

    def cancel(self):
        if not self.running:
            return
        self.running = False
        if self._stream:
            self._stream.cancel()
        if self._mine and self.compiler.busy:
            self.compiler.cancel()
        if self.attempts and self.attempts[-1].status in ("génération", "compilation"):
            self.attempts[-1].status = "annulé"
            self.attemptUpdated.emit(len(self.attempts) - 1)
        self.finished.emit(False, "Recherche de preuve arrêtée.")

    def _fit_messages(self) -> int:
        budget = self.ctx - 256
        def est(ms):
            return int(sum(len(m["content"]) for m in ms) / 3.0) + 50
        while est(self.messages) > budget - 2048 and len(self.messages) > 3:
            del self.messages[1:3]   # drop oldest assistant/user pair, keep the original task
        if est(self.messages) > budget - 2048:
            self.messages = self.messages[:1]
        return max(512, min(self.sampling.max_tokens, budget - est(self.messages)))

    def _next(self):
        if not self.running:
            return
        if len(self.attempts) >= self.n:
            self.running = False
            self.finished.emit(False, f"Aucune preuve trouvée après {self.n} essais.")
            return
        if self.server.state != LlamaServer.READY:
            self.running = False
            self.infraError.emit("server_down", "")
            return
        kind = "initial" if len(self.messages) == 1 else "correction"
        a = Attempt(len(self.attempts), kind)
        self.attempts.append(a)
        if getattr(self, "_retry_same", False):
            self._retry_same = False
            self.attemptUpdated.emit(a.index)
        else:
            self.attemptStarted.emit(a.index)
        max_tokens = self._fit_messages()
        s = ChatStream(self.server.url, self)
        self._stream = s
        s.delta.connect(lambda t, i=a.index: self._delta(i, t))
        s.done.connect(lambda d, i=a.index: self._generated(i, d))
        s.error.connect(lambda k, det, i=a.index: self._gen_error(i, k, det))
        s.start(list(self.messages), self.sampling.temperature, self.sampling.top_p, max_tokens)

    def _delta(self, i: int, t: str):
        self.attempts[i].raw += t
        self.token.emit(i, t)

    def _gen_error(self, i: int, kind: str, det: str):
        self._stream = None
        if kind == "cancelled" or not self.running:
            return
        a = self.attempts[i]
        a.errors_text = det
        if kind == "http" and ("context" in det.lower() or "exceed" in det.lower()) and len(self.messages) > 1:
            # prompt too long for the context window: shorten the history and retry this same attempt
            self.messages = self.messages[:1] if len(self.messages) <= 3 else self.messages[:1] + self.messages[3:]
            self.attempts.pop()
            self._retry_same = True
            QTimer.singleShot(0, self._next)
            return
        a.status, a.summary = "erreur", "La génération a échoué."
        self.attemptUpdated.emit(i)
        self.running = False
        self.infraError.emit("server_down" if kind in ("unreachable", "stalled") else "generation", det)

    def _generated(self, i: int, d: dict):
        self._stream = None
        if not self.running:
            return
        a = self.attempts[i]
        a.raw = d["text"]
        a.gen_seconds, a.tokens, a.tps, a.ttft, a.finish_reason = d["seconds"], d["tokens"], d["tps"], d["ttft"], d["finish_reason"]
        code = leancheck.extract_code(a.raw)
        if d.get("loop"):
            a.status, a.summary = "refusé", "L'IA tournait en rond : essai interrompu, nouvel essai."
            self.attemptUpdated.emit(i)
            self.messages = self.messages[:1]
            QTimer.singleShot(0, self._next)
            return
        if code is None:
            a.status = "refusé"
            a.summary = ("Réponse trop longue, coupée avant le code Lean." if a.finish_reason == "length"
                         else "Le modèle n'a pas produit de code Lean.")
            self.attemptUpdated.emit(i)
            self.messages = self.messages[:1]   # start over
            QTimer.singleShot(0, self._next)
            return
        try:
            a.code = leancheck.assemble_proof(self.statement, code)
        except leancheck.StatementError as e:
            a.status, a.summary = "refusé", str(e)
            self.attemptUpdated.emit(i)
            self.messages = self.messages[:1]
            QTimer.singleShot(0, self._next)
            return
        a.status = "compilation"
        self.attemptUpdated.emit(i)
        self._mine = True
        self.compiler.compile(leancheck.with_axiom_probe(a.code, self.name), self.ws, self.timeout_s, self.name)

    def _compiled(self, res: CompileResult):
        if not self._mine:
            return
        self._mine = False
        if not self.running or not self.attempts:
            return
        i = len(self.attempts) - 1
        a = self.attempts[i]
        a.compile_seconds = res.seconds
        if res.infra_error and not res.verdict.errors:
            a.status, a.summary = "erreur", res.verdict.summary
            self.attemptUpdated.emit(i)
            self.running = False
            self.infraError.emit("workspace", res.infra_error)
            return
        a.summary = res.verdict.summary
        if res.verdict.ok:
            a.status = "accepté"
            self.final_code = a.code
            self.attemptUpdated.emit(i)
            self.running = False
            self.finished.emit(True, f"Preuve trouvée et vérifiée par Lean (essai {i + 1}).")
            return
        a.status = "refusé"
        feedback = leancheck.errors_for_feedback(res.code, res.verdict)
        a.errors_text = feedback
        self.attemptUpdated.emit(i)
        self.messages = self.messages + [
            {"role": "assistant", "content": a.raw},
            {"role": "user", "content": leancheck.correction_prompt(i, feedback)},
        ]
        QTimer.singleShot(0, self._next)


# ---------------------------------------------------------------- natural language -> Lean statement
class Formalizer(QObject):
    """Translate a problem written in natural language (LaTeX allowed) into a Lean `theorem … := by sorry`.

    Each try = one Goedel-Formalizer generation + a Lean compile of the statement (with `sorry`); stops at the
    first statement that Lean accepts (up to `tries`). Same signals as `Prover` so the UI can share its widgets."""
    attemptStarted = Signal(int)
    token = Signal(int, str)
    attemptUpdated = Signal(int)
    finished = Signal(bool, str)          # all statements compiled, French summary
    infraError = Signal(str, str)

    def __init__(self, server: LlamaServer, compiler: LeanCompiler, parent=None):
        super().__init__(parent)
        self.server, self.compiler = server, compiler
        self.attempts: list[Attempt] = []
        self.running = False
        self.n = 3
        self.statement = ""          # best statement so far (full Lean file with `:= by sorry`)
        self.errors: list[leancheck.LeanMessage] = []
        self._stream: ChatStream | None = None
        self._mine = False
        compiler.finished.connect(self._compiled)

    def start(self, text: str, ws: Workspace, tries: int, timeout_s: int):
        self.text, self.ws, self.n, self.timeout_s = text.strip(), ws, max(1, tries), timeout_s
        self.attempts, self.statement, self.errors, self.running = [], "", [], True
        self._next()

    def cancel(self):
        if not self.running:
            return
        self.running = False
        if self._stream:
            self._stream.cancel()
        if self._mine and self.compiler.busy:
            self.compiler.cancel()
        if self.attempts and self.attempts[-1].status in ("génération", "compilation"):
            self.attempts[-1].status = "annulé"
            self.attemptUpdated.emit(len(self.attempts) - 1)
        self.finished.emit(False, "Traduction arrêtée.")

    def _next(self):
        if not self.running:
            return
        if len(self.attempts) >= self.n:
            self.running = False
            self.finished.emit(False, "La traduction ne compile pas encore : corrigez-la dans l'éditeur "
                                      "ou reformulez le problème.")
            return
        if self.server.state != LlamaServer.READY:
            self.running = False
            self.infraError.emit("server_down", "")
            return
        a = Attempt(len(self.attempts), "translation")
        self.attempts.append(a)
        self.attemptStarted.emit(a.index)
        s = ChatStream(self.server.url, self)
        self._stream = s
        s.delta.connect(self._delta)
        s.done.connect(self._generated)
        s.error.connect(self._gen_error)
        s.start([{"role": "user", "content": leancheck.formalize_prompt(self.text)}], 0.9, 0.95, 12000,
                extra={"top_k": 20})        # sampling from the Goedel-Formalizer-V2 model card

    def _delta(self, t: str):
        if self.attempts:
            self.attempts[-1].raw += t
            self.token.emit(len(self.attempts) - 1, t)

    def _gen_error(self, kind: str, det: str):
        self._stream = None
        if kind == "cancelled" or not self.running:
            return
        a = self.attempts[-1]
        a.status, a.summary = "erreur", "La traduction a échoué."
        self.attemptUpdated.emit(a.index)
        self.running = False
        self.infraError.emit("server_down" if kind in ("unreachable", "stalled") else "generation", det)

    def _generated(self, d: dict):
        self._stream = None
        if not self.running:
            return
        i = len(self.attempts) - 1
        a = self.attempts[i]
        a.raw = d["text"]
        a.gen_seconds, a.tokens, a.tps, a.ttft, a.finish_reason = d["seconds"], d["tokens"], d["tps"], d["ttft"], d["finish_reason"]
        code = None if d.get("loop") else leancheck.extract_code(a.raw)
        if code is None:
            a.status = "refusé"
            a.summary = ("L'IA tournait en rond : nouvel essai." if d.get("loop") else
                         "Pas d'énoncé Lean dans la réponse : nouvel essai.")
            self.attemptUpdated.emit(i)
            QTimer.singleShot(0, self._next)
            return
        try:
            a.code = leancheck.normalize_formal_statement(code)
        except leancheck.StatementError as e:
            a.status, a.summary = "refusé", f"{e} Nouvel essai."
            self.attemptUpdated.emit(i)
            QTimer.singleShot(0, self._next)
            return
        self.statement = a.code
        a.status = "compilation"
        self.attemptUpdated.emit(i)
        self._mine = True
        self.compiler.compile(a.code, self.ws, self.timeout_s, None)

    def _compiled(self, res: CompileResult):
        if not self._mine:
            return
        self._mine = False
        if not self.running or not self.attempts:
            return
        i = len(self.attempts) - 1
        a = self.attempts[i]
        a.compile_seconds = res.seconds
        if res.infra_error and not res.verdict.errors:
            a.status, a.summary = "erreur", res.verdict.summary
            self.attemptUpdated.emit(i)
            self.running = False
            self.infraError.emit("workspace", res.infra_error)
            return
        self.errors = res.verdict.errors
        if not res.verdict.errors and not res.timed_out:
            a.status, a.summary = "accepté", "Énoncé valide en Lean (à relire)"
            self.attemptUpdated.emit(i)
            self.running = False
            self.finished.emit(True, "Énoncé traduit : Lean le comprend. Relisez-le avant de prouver.")
            return
        a.status = "refusé"
        a.summary = "Lean refuse cette traduction : nouvel essai." if i + 1 < self.n else "Lean refuse cette traduction."
        a.errors_text = "\n".join(f"ligne {m.line} : {m.text}" for m in res.verdict.errors)
        self.attemptUpdated.emit(i)
        QTimer.singleShot(0, self._next)
