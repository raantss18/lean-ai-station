"""Models tab: local GGUF list, load/unload, import, delete (to trash, undoable), optional HF download."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtCore import QFile, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLineEdit, QListWidget,
                               QListWidgetItem, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import config
from ..errors import Friendly, friendly
from ..services import GuardedNAM, Net
from . import theme
from .widgets import BusyBar, Card, button, label


def human(n: float) -> str:
    return f"{n / 1e9:.2f} Go" if n >= 1e9 else f"{n / 1e6:.0f} Mo"


class ModelsPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 12)
        head = QHBoxLayout()
        head.addWidget(label("Modèles installés", "H2"))
        head.addStretch(1)
        head.addWidget(button("Importer un fichier .gguf…", tip="Copier un modèle GGUF dans le dossier des modèles",
                              slot=self.import_model))
        head.addWidget(button("Ouvrir le dossier", tip=str(config.MODELS_DIR),
                              slot=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.MODELS_DIR)))))
        head.addWidget(button("Actualiser", tip="Relire le dossier des modèles", slot=lambda: ctx.refresh_models()))
        lay.addLayout(head)
        lay.addWidget(label(f"Dossier : {config.tilde(config.MODELS_DIR)}", "Muted"))

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Nom", "Quantification", "Taille", "Carte graphique", "État"])
        self.table.horizontalHeaderItem(1).setToolTip("Précision du modèle compressé : Q4_K_M = bon compromis "
                                                      "vitesse/qualité ; Q5/Q6/Q8 = plus précis mais plus lourd.")
        self.table.horizontalHeaderItem(3).setToolTip("Le modèle tient-il entièrement dans la mémoire de la carte "
                                                      "graphique (VRAM) ? Sinon il sera plus lent.")
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._sel)
        self.table.doubleClicked.connect(lambda _i: self.load())
        lay.addWidget(self.table, 1)
        self.empty = label("Aucun modèle trouvé. Cliquez sur « Importer un fichier .gguf… » ou téléchargez-en un ci-dessous.",
                           "Muted", wrap=True)
        lay.addWidget(self.empty)

        row = QHBoxLayout()
        self.load_btn = button("▶ Charger", "Primary", "Charger ce modèle sur la carte graphique (double-clic)", self.load)
        self.unload_btn = button("⏏ Décharger", tip="Libérer la mémoire de la carte graphique", slot=self.unload)
        self.del_btn = button("Supprimer", "Danger", "Mettre le fichier à la corbeille (annulable)", self.delete)
        row.addWidget(self.load_btn)
        row.addWidget(self.unload_btn)
        row.addStretch(1)
        row.addWidget(self.del_btn)
        lay.addLayout(row)
        self.busy = BusyBar()
        lay.addWidget(self.busy)

        # Hugging Face download
        hf = Card()
        hf.lay.addWidget(label("Télécharger depuis Hugging Face (nécessite Internet)", "H2"))
        r = QHBoxLayout()
        self.repo = QLineEdit("mradermacher/Goedel-Prover-V2-8B-GGUF")
        self.repo.setToolTip("Nom du dépôt Hugging Face contenant des fichiers .gguf")
        self.search_btn = button("Lister les fichiers", tip="Afficher les fichiers .gguf du dépôt", slot=self.hf_list)
        r.addWidget(self.repo, 1)
        r.addWidget(self.search_btn)
        hf.lay.addLayout(r)
        self.hf_files = QListWidget()
        self.hf_files.setMaximumHeight(130)
        hf.lay.addWidget(self.hf_files)
        r2 = QHBoxLayout()
        self.offline_lbl = label("", "Muted", wrap=True)
        r2.addWidget(self.offline_lbl, 1)
        self.dl_btn = button("Télécharger", "Primary", "Télécharger le fichier sélectionné dans le dossier des modèles",
                             self.hf_download)
        r2.addWidget(self.dl_btn)
        hf.lay.addLayout(r2)
        hf.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        lay.addWidget(hf)

        self.nam = GuardedNAM(self)
        self.reply: QNetworkReply | None = None
        self.busy.cancelled.connect(self._cancel_dl)
        ctx.modelsChanged.connect(self.fill)
        ctx.server.stateChanged.connect(self.fill)
        ctx.settingsChanged.connect(self._offline_state)
        ctx.gpu.stats.connect(self._fit_column)
        self._offline_state()
        self.fill()

    # ------------------------------------------------------------ table
    def _fits(self, size: int, info=None) -> tuple[str, str]:
        tot = self.ctx.vram_total()
        if not tot:
            return "Inconnue", theme.MUTED
        kv = info.kv_bytes(self.ctx.settings.server.ctx_size, self.ctx.settings.server.kv_type) if info else 0
        need = (size + kv) / 2**20 + 800
        if need <= tot - 500:
            return "✔ Tient entièrement", theme.OK
        if size / 2**20 < tot:
            return "◐ Partiellement (plus lent)", theme.WARN
        return "✖ Trop gros (très lent)", theme.ERR

    def fill(self, *_):
        self.table.setRowCount(0)
        loaded = self.ctx.server.model_path if self.ctx.server.state in ("ready", "starting") else None
        for m in self.ctx.models:
            r = self.table.rowCount()
            self.table.insertRow(r)
            if isinstance(m, tuple):
                p, err = m
                vals = [p.name, "?", human(p.stat().st_size) if p.exists() else "?", "—", f"⚠️ Illisible : {err}"]
                color = theme.ERR
                path = p
                info = None
            else:
                path, info = m.path, m
                fit, color = self._fits(m.size, m)
                st = ""
                if loaded and Path(loaded) == m.path:
                    st = "🟢 Chargé" if self.ctx.server.state == "ready" else "🟡 Chargement…"
                role = "traduction texte → Lean" if self.ctx.is_formalizer(m.path) else "preuves"
                vals = [f"{m.path.name}  ·  {role}", m.quant, human(m.size), fit, st]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, str(path))
                if (c == 3 and info is not None) or (c == 4 and info is None):
                    it.setForeground(QColor(color))
                if c == 0:
                    it.setToolTip(str(path))
                self.table.setItem(r, c, it)
            if str(path) == self.ctx.settings.model_path:
                self.table.selectRow(r)
        self.empty.setVisible(not self.ctx.models)
        self._sel()

    def _fit_column(self, *_):
        if self.table.rowCount() and self.table.item(0, 3) and self.table.item(0, 3).text() == "Inconnue":
            self.fill()

    def _selected(self) -> Path | None:
        items = self.table.selectedItems()
        return Path(items[0].data(Qt.UserRole)) if items else None

    def _sel(self):
        p = self._selected()
        st = self.ctx.server.state
        self.load_btn.setEnabled(p is not None and st != "stopping")
        self.unload_btn.setEnabled(st in ("ready", "starting"))
        self.del_btn.setEnabled(p is not None)

    # ------------------------------------------------------------ actions
    def load(self):
        p = self._selected()
        if p:
            self.ctx.load_model(p)
            self.ctx.toast.emit(f"Chargement de {p.name}…", None, None)

    def unload(self):
        self.ctx.server.stop()

    def delete(self):
        p = self._selected()
        if not p:
            return
        if self.ctx.server.model_path and Path(self.ctx.server.model_path) == p:
            self.ctx.server.stop()
        ok, trashed = QFile.moveToTrash(str(p))
        if not ok:
            self.ctx.banner.emit(Friendly("Suppression impossible", "Le fichier n'a pas pu être mis à la corbeille.",
                                          [], "warn"), str(p))
            return
        sha = Path(str(p) + ".sha256")
        sha_tr = None
        if sha.exists():
            _ok, sha_tr = QFile.moveToTrash(str(sha))
        self.ctx.refresh_models()

        def undo():
            try:
                shutil.move(trashed, p)
                if sha_tr:
                    shutil.move(sha_tr, sha)
            except OSError as e:
                self.ctx.banner.emit(Friendly("Restauration impossible", "Restaurez le fichier depuis la corbeille.",
                                              [], "warn"), str(e))
            self.ctx.refresh_models()
        self.ctx.toast.emit(f"{p.name} mis à la corbeille.", undo, None)

    def import_model(self):
        src, _ = QFileDialog.getOpenFileName(self, "Importer un modèle", str(Path.home()), "Modèles GGUF (*.gguf)")
        if not src:
            return
        src = Path(src)
        dst = config.MODELS_DIR / src.name
        if dst.exists():
            self.ctx.toast.emit("Ce modèle est déjà installé.", None, None)
            return
        if self.ctx.disk_free_gb() < src.stat().st_size / 1e9 + 5:
            self.ctx.banner.emit(friendly("disk_low"), "")
            return
        from ..gguf import GGUFError, read_info
        try:
            read_info(src)
        except (OSError, GGUFError) as e:
            self.ctx.banner.emit(friendly("bad_model"), str(e))
            return
        self.busy.start(f"Copie de {src.name}…", cancellable=False)
        self._import_cancel = False

        def copy():
            tmp = dst.with_suffix(".gguf.part")
            shutil.copyfile(src, tmp)
            tmp.rename(dst)
            return dst

        def done(_r):
            self.busy.stop()
            self.ctx.refresh_models()
            self.ctx.toast.emit(f"Modèle importé : {src.name}", None, None)

        def err(e, tb):
            self.busy.stop()
            self.ctx.banner.emit(Friendly("Import impossible", "La copie a échoué (disque plein ?).", [], "warn"), tb)
        self.ctx.run_bg(copy, done, err)

    # ------------------------------------------------------------ Hugging Face
    def _offline_state(self):
        off = self.ctx.settings.offline
        for w in (self.search_btn, self.dl_btn, self.repo):
            w.setEnabled(not off)
        self.offline_lbl.setText("🔒 Mode hors-ligne actif : téléchargement désactivé (modifiable dans « Système »)."
                                 if off else "Le fichier sera vérifié puis ajouté à la liste.")

    def hf_list(self):
        if not Net.allowed("https://huggingface.co"):
            self.ctx.banner.emit(friendly("offline_blocked"), "")
            return
        repo = self.repo.text().strip()
        r = self.nam.get(QNetworkRequest(QUrl(f"https://huggingface.co/api/models/{repo}/tree/main")))
        self.busy.start("Recherche des fichiers…")
        r.finished.connect(self._listed)

    def _listed(self):
        r = self.sender()
        if not isinstance(r, QNetworkReply):
            return
        self.busy.stop()
        r.deleteLater()
        self.hf_files.clear()
        try:
            files = [f for f in json.loads(bytes(r.readAll())) if f.get("path", "").endswith(".gguf")]
        except (ValueError, TypeError):
            files = []
        if r.error() != QNetworkReply.NoError or not files:
            self.ctx.banner.emit(Friendly("Aucun fichier trouvé", "Vérifiez le nom du dépôt et la connexion Internet.",
                                          [], "warn"), r.errorString())
            return
        for f in files:
            it = QListWidgetItem(f"{f['path']}   ({human(f.get('size', 0))})")
            it.setData(Qt.UserRole, (f["path"], f.get("size", 0), (f.get("lfs") or {}).get("oid", "")))
            self.hf_files.addItem(it)

    def hf_download(self):
        it = self.hf_files.currentItem()
        if not it:
            self.ctx.toast.emit("Choisissez d'abord un fichier dans la liste.", None, None)
            return
        if not Net.allowed("https://huggingface.co"):
            self.ctx.banner.emit(friendly("offline_blocked"), "")
            return
        name, size, _oid = it.data(Qt.UserRole)
        if self.ctx.disk_free_gb() < size / 1e9 + 5:
            self.ctx.banner.emit(friendly("disk_low"), "")
            return
        repo = self.repo.text().strip()
        self._dl_path = config.MODELS_DIR / Path(name).name
        self._dl_tmp = self._dl_path.with_suffix(".gguf.part")
        self._dl_file = open(self._dl_tmp, "wb")
        req = QNetworkRequest(QUrl(f"https://huggingface.co/{repo}/resolve/main/{name}"))
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
        self.reply = self.nam.get(req)
        self.reply.readyRead.connect(lambda: self._dl_file.write(bytes(self.reply.readAll())) if self.reply else None)
        self.reply.downloadProgress.connect(lambda a, b: self.busy.progress(int(a / 2**20), max(1, int(b / 2**20)),
                                                                            f"Téléchargement : {human(a)} / {human(b)}"))
        self.reply.finished.connect(self._dl_done)
        self.busy.start(f"Téléchargement de {name}…", maximum=1)

    def _cancel_dl(self):
        if self.reply is not None:
            self.reply.abort()

    def _dl_done(self):
        r, self.reply = self.reply, None
        if r is None:
            return
        self._dl_file.write(bytes(r.readAll()))
        self._dl_file.close()
        self.busy.stop()
        ok = r.error() == QNetworkReply.NoError
        r.deleteLater()
        if not ok:
            self._dl_tmp.unlink(missing_ok=True)
            if r.error() != QNetworkReply.OperationCanceledError:
                self.ctx.banner.emit(Friendly("Téléchargement interrompu", "Vérifiez la connexion puis réessayez.",
                                              [], "warn"), r.errorString())
            return
        self._dl_tmp.rename(self._dl_path)
        self.ctx.refresh_models()
        self.ctx.toast.emit(f"Téléchargé : {self._dl_path.name}", None, None)
