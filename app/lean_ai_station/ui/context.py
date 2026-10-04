"""Application context shared by all pages: settings, session, services, background jobs."""
from __future__ import annotations

import shutil
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from .. import config
from ..i18n import _
from ..errors import Friendly, friendly
from ..gguf import GGUFError, find_models, read_info
from ..services import Explainer, Formalizer, GpuMonitor, LeanCompiler, LlamaServer, Net, Prover
from ..workspaces import Workspace, all_workspaces


class _Relay(QObject):
    done = Signal(object, object)   # result, error


class _Job(QRunnable):
    def __init__(self, fn, relay):
        super().__init__()
        self.fn, self.relay = fn, relay

    def run(self):
        try:
            res = self.fn()
            self.relay.done.emit(res, None)
        except Exception as e:  # reported on the GUI thread
            self.relay.done.emit(None, (e, traceback.format_exc()))


class AppContext(QObject):
    banner = Signal(object, str)          # Friendly, details
    toast = Signal(str, object, object)   # text, undo callable | None, on_expire | None
    navigate = Signal(str)                # page key
    modelsChanged = Signal()
    workspacesChanged = Signal()
    settingsChanged = Signal()
    languageChanged = Signal(str)        # new interface language: the main window is rebuilt
    proveRequest = Signal(str, str)       # Lean statement to prove immediately, optional informal text
    texRequest = Signal(str)              # path of a .tex file to import
    translateRequest = Signal(str)        # problem in natural language: translate to Lean (user then reviews)

    def __init__(self, settings: config.Settings, session: dict):
        super().__init__()
        self.settings = settings
        self.session = session
        Net.offline = settings.offline
        self.pool = QThreadPool.globalInstance()
        self._relays: set[_Relay] = set()
        self.server = LlamaServer(self)
        self.compiler = LeanCompiler(self)       # used by the prove loop
        self.verifier = LeanCompiler(self)       # used by « Vérifier »
        self.prover = Prover(self.server, self.compiler, self)
        self.translator_compiler = LeanCompiler(self)
        self.formalizer = Formalizer(self.server, self.translator_compiler, self)
        self.explainer = Explainer(self.server, self)
        from .pipeline import Pipeline
        self.pipeline = Pipeline(self)
        from .updates_ui import UpdateManager
        self.updates = UpdateManager(self)
        self.gpu = GpuMonitor(self)
        self.models: list = []                   # GGUFInfo or (Path, error)
        self.workspaces: list[Workspace] = []
        self.gpu_ok: bool | None = None
        self.gpu.stats.connect(lambda d: setattr(self, "gpu_ok", True))
        self.gpu.unavailable.connect(lambda _m: setattr(self, "gpu_ok", False))
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self.save_now)
        self._after_ready: list = []
        self.server.stateChanged.connect(self._server_state)
        self.server.failed.connect(self._server_failed)
        self.server.notice.connect(lambda m: self.banner.emit(Friendly(_("Réglage automatique"), m, [], "info"), ""))

    # ---------------------------------------------------------------- background jobs
    def run_bg(self, fn, on_done=None, on_error=None):
        relay = _Relay()
        self._relays.add(relay)

        def finish(res, err):
            self._relays.discard(relay)
            if err is None:
                if on_done:
                    on_done(res)
            elif on_error:
                on_error(*err)
            else:
                self.banner.emit(friendly("unknown"), err[1])
        relay.done.connect(finish)
        self.pool.start(_Job(fn, relay))

    # ---------------------------------------------------------------- persistence
    def save_later(self):
        self._save_timer.start()

    def save_now(self):
        try:
            config.save_settings(self.settings)
            config.save_session(self.session)
        except OSError as e:
            self.banner.emit(Friendly(_("Impossible d'enregistrer les réglages"),
                                      _("Le disque est peut-être plein ou protégé en écriture."), [], "warn"), str(e))

    def request_language(self, code: str):
        from ..i18n import LANGS, set_language
        if code not in LANGS or code == self.settings.language:
            return
        if self.pipeline.busy or self.verifier.busy:
            self.toast.emit(_("Une opération est en cours : arrêtez-la d'abord (Échap)."), None, None)
            return
        self.settings.language = code
        set_language(code)
        self.save_now()
        self.languageChanged.emit(code)

    def set_offline(self, on: bool):
        self.settings.offline = on
        Net.offline = on
        self.save_later()
        self.settingsChanged.emit()

    # ---------------------------------------------------------------- models / workspaces
    def refresh_models(self, then=None):
        def scan():
            out = []
            for p in find_models(config.MODELS_DIR):
                try:
                    out.append(read_info(p))
                except (OSError, GGUFError) as e:
                    out.append((p, str(e)))
            return out

        def done(res):
            self.models = res
            self.modelsChanged.emit()
            if then:
                then()
        self.run_bg(scan, done)

    def refresh_workspaces(self, then=None):
        def done(res):
            self.workspaces = res
            if not self.settings.workspace or not self.workspace():
                ready = [w for w in res if not w.problems]
                pref = [w for w in ready if w.key == "lean-prover49"] or ready
                if pref:
                    self.settings.workspace = pref[0].key
            self.workspacesChanged.emit()
            if then:
                then()
        self.run_bg(all_workspaces, done)

    def workspace(self, key: str | None = None) -> Workspace | None:
        key = key or self.settings.workspace
        for w in self.workspaces:
            if w.key == key:
                return w
        return None

    @staticmethod
    def is_formalizer(path: Path | str) -> bool:
        return "formalizer" in Path(path).name.lower()

    def formalizer_model(self) -> Path | None:
        """The natural-language → Lean translation model, if installed."""
        c = [m for m in self.models if not isinstance(m, tuple) and self.is_formalizer(m.path)]
        return max(c, key=lambda m: ("Q4_K_M" in m.path.name, -m.size)).path if c else None

    @staticmethod
    def is_explainer(path: Path | str) -> bool:
        n = Path(path).name.lower()
        return "qwen3" in n and "goedel" not in n

    def explainer_model(self) -> Path | None:
        """General instruction-following model used to explain proofs in French."""
        c = [m for m in self.models if not isinstance(m, tuple) and self.is_explainer(m.path)]
        return max(c, key=lambda m: ("Q4_K_M" in m.path.name, -m.size)).path if c else None

    def role_model(self, role: str) -> Path | None:
        if role == "formalizer":
            return self.formalizer_model()
        if role == "explainer":
            return self.explainer_model()
        return self.default_model()

    def default_model(self) -> Path | None:
        good = [m for m in self.models if not isinstance(m, tuple) and not self.is_formalizer(m.path)
                and not self.is_explainer(m.path)]
        if (self.settings.model_path and Path(self.settings.model_path).exists()
                and not self.is_formalizer(self.settings.model_path) and not self.is_explainer(self.settings.model_path)):
            return Path(self.settings.model_path)
        vram = self.vram_total()
        goedel = [m for m in good if "goedel" in m.path.name.lower()]
        cands = goedel or good
        if not cands:
            return None
        # prefer Q4_K_M, then the largest that fits the GPU
        q4 = [m for m in cands if m.quant == "Q4_K_M"]
        if q4:
            return q4[0].path
        fit = [m for m in cands if vram and m.size < vram * 0.75 * 2**20]
        return max(fit or cands, key=lambda m: m.size).path

    def vram_total(self) -> int | None:
        return self.gpu.last["total"] if self.gpu.last else None

    def vram_free_for_model(self) -> int | None:
        """Free VRAM (MiB) as if our own llama-server were not loaded (it is restarted on every load)."""
        return self.gpu.free_excluding(self.server.last_pid)

    def check_disk(self, min_gb: float = 10.0) -> bool:
        """Warn (banner) when the disk is nearly full. Returns True when space is fine."""
        free = self.disk_free_gb()
        if free < min_gb:
            self.banner.emit(friendly("disk_low"), _("{n:.1f} Go libres").format(n=free))
            return False
        return True

    def disk_free_gb(self, path: Path = config.MODELS_DIR) -> float:
        p = path if path.exists() else Path.home()
        return shutil.disk_usage(p).free / 1e9

    # ---------------------------------------------------------------- model loading
    def load_model(self, path: Path | None = None, then=None):
        path = path or self.default_model()
        if path is None:
            self.banner.emit(Friendly(_("Aucun modèle installé"),
                                      _("Aucun fichier de modèle (.gguf) n'a été trouvé. Ouvrez « Modèles » pour en importer un."),
                                      [(_("Ouvrir Modèles"), "goto_models")], "warn"), "")
            return
        if then:
            self._after_ready.append(then)
        if self.server.state == LlamaServer.READY and self.server.model_path == path:
            self._flush_ready()
            return
        if not self.is_formalizer(path) and not self.is_explainer(path):   # the prover stays the default across sessions
            self.settings.model_path = str(path)
            self.save_later()
        self.with_gpu_info(lambda: self._start_server(path))

    def with_gpu_info(self, then, timeout_ms: int = 3000):
        """Run `then` once a GPU snapshot (or a definitive 'no GPU') is known, never on missing data."""
        if self.gpu.last is not None or self.gpu_ok is False:
            then()
            return
        done = {"x": False}

        def fire(*_a):
            if done["x"]:
                return
            done["x"] = True
            for sig, slot in ((self.gpu.stats, fire), (self.gpu.unavailable, fire)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            then()
        self.gpu.stats.connect(fire)
        self.gpu.unavailable.connect(fire)
        self.gpu.poll()
        QTimer.singleShot(timeout_ms, self, fire)

    def _start_server(self, path: Path):
        if self.gpu_ok is False or self.gpu.last is None:
            self.banner.emit(friendly("no_gpu"), "")
            self.server.start(path, self.settings.server, None)
        else:
            self.server.start(path, self.settings.server, self.vram_free_for_model())

    def ensure_model(self, then, role: str = "prover"):
        """Run `then` once the model for `role` ('prover' | 'formalizer') is loaded, switching models if needed."""
        target = self.role_model(role)
        if target is None:
            if role in ("formalizer", "explainer"):
                self.banner.emit(friendly("no_" + role), "")
            else:
                self.load_model(then=then)       # reports « Aucun modèle installé »
            return
        if self.server.state == LlamaServer.READY and self.server.model_path == target:
            then()
        else:
            self.load_model(target, then=then)

    def _flush_ready(self):
        cbs, self._after_ready = self._after_ready, []
        for cb in cbs:
            cb()

    def _server_state(self, st: str):
        if st == LlamaServer.READY:
            self._flush_ready()

    def _server_failed(self, kind: str, details: str):
        self._after_ready = []
        key = {"crashed": "server_crashed"}.get(kind, kind)
        self.banner.emit(friendly(key), details)
