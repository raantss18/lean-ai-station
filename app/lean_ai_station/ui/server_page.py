"""Server tab: start/stop llama-server, port, URL, advanced parameters, live logs."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QPlainTextEdit, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import config
from . import theme
from .widgets import Card, button, label

STATE_TXT = {"stopped": ("⚪ Arrêté", theme.MUTED), "starting": ("🟡 Démarrage…", theme.WARN),
             "ready": ("🟢 En marche", theme.OK), "stopping": ("🟠 Arrêt…", theme.WARN)}


class ServerPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        srv = ctx.server
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 12)
        top = Card()
        r = QHBoxLayout()
        col = QVBoxLayout()
        self.state = label("", "H2")
        self.info = label("", "Muted", wrap=True)
        col.addWidget(self.state)
        col.addWidget(self.info)
        r.addLayout(col, 1)
        self.start_btn = button("▶ Démarrer", "Primary", "Démarrer le serveur avec le modèle choisi", self.start)
        self.restart_btn = button("↻ Redémarrer", tip="Redémarrer avec les réglages ci-dessous", slot=self.start)
        self.stop_btn = button("⏹ Arrêter", "Danger", "Arrêter le serveur et libérer la carte graphique", srv.stop)
        for b in (self.start_btn, self.restart_btn, self.stop_btn):
            r.addWidget(b)
        top.lay.addLayout(r)
        u = QHBoxLayout()
        self.url = label("", wrap=False)
        self.url.setToolTip("Adresse compatible OpenAI, utilisable par d'autres logiciels de cet ordinateur.")
        u.addWidget(self.url)
        u.addWidget(button("Copier l'adresse", tip="Copier l'URL de l'API", slot=lambda: (
            QGuiApplication.clipboard().setText(srv.url + "/v1"), ctx.toast.emit("Adresse copiée.", None, None))))
        u.addStretch(1)
        top.lay.addLayout(u)
        lay.addWidget(top)

        adv = QGroupBox("Réglages avancés (les valeurs recommandées conviennent dans la plupart des cas)")
        form = QFormLayout(adv)
        s = ctx.settings.server
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(s.port)
        self.port.setToolTip("Port local du serveur (127.0.0.1 uniquement, jamais exposé au réseau).")
        self.ctx_size = QComboBox()
        for v in (4096, 8192, 12288, 16384, 24576, 32768, 40960):
            self.ctx_size.addItem(f"{v} tokens", v)
        self.ctx_size.setCurrentIndex(max(0, self.ctx_size.findData(s.ctx_size)))
        self.ctx_size.setToolTip("Mémoire de travail du modèle. Plus grand = preuves longues possibles, "
                                 "mais plus de mémoire graphique.")
        self.ngl = QSpinBox()
        self.ngl.setRange(0, 99)
        self.ngl.setValue(s.gpu_layers)
        self.ngl.setToolTip("Nombre de couches du modèle sur la carte graphique (99 = toutes ; 0 = processeur seul).")
        self.batch = QComboBox()
        for v in (256, 512, 1024, 2048, 4096):
            self.batch.addItem(str(v), v)
        self.batch.setCurrentIndex(max(0, self.batch.findData(s.batch_size)))
        self.batch.setToolTip("Taille de lot pour lire la question (plus grand = lecture plus rapide).")
        self.ubatch = QComboBox()
        for v in (128, 256, 512, 1024, 2048):
            self.ubatch.addItem(str(v), v)
        self.ubatch.setCurrentIndex(max(0, self.ubatch.findData(s.ubatch_size)))
        self.ubatch.setToolTip("Taille de micro-lot physique sur la carte graphique.")
        self.kv = QComboBox()
        for v, t in (("f16", "f16 (précis, plus de mémoire)"), ("q8_0", "q8_0 (recommandé)"), ("q4_0", "q4_0 (économe)")):
            self.kv.addItem(t, v)
        self.kv.setCurrentIndex(max(0, self.kv.findData(s.kv_type)))
        self.kv.setToolTip("Format du cache de contexte (KV). q8_0 divise la mémoire par deux sans perte notable.")
        self.fa = QCheckBox("Activée")
        self.fa.setChecked(s.flash_attn)
        self.fa.setToolTip("Flash Attention : plus rapide et plus économe en mémoire sur les cartes RTX.")
        self.threads = QSpinBox()
        self.threads.setRange(1, 64)
        self.threads.setValue(s.threads)
        self.threads.setToolTip("Nombre de cœurs du processeur utilisés.")
        for w in (self.port, self.ctx_size, self.ngl, self.batch, self.ubatch, self.kv, self.threads):
            w.setMaximumWidth(280)
        form.addRow("Port", self.port)
        form.addRow("Contexte", self.ctx_size)
        form.addRow("Couches sur la carte graphique", self.ngl)
        form.addRow("Lot (batch)", self.batch)
        form.addRow("Micro-lot (ubatch)", self.ubatch)
        form.addRow("Cache KV", self.kv)
        form.addRow("Flash Attention", self.fa)
        form.addRow("Threads processeur", self.threads)
        rr = QHBoxLayout()
        rr.addWidget(button("Valeurs recommandées", tip="Revenir aux réglages mesurés comme les meilleurs (BENCH.md)",
                            slot=self.reset))
        rr.addStretch(1)
        form.addRow(rr)
        for w in (self.port, self.ngl, self.threads):
            w.valueChanged.connect(self._save)
        for w in (self.ctx_size, self.batch, self.ubatch, self.kv):
            w.currentIndexChanged.connect(self._save)
        self.fa.toggled.connect(self._save)
        lay.addWidget(adv)

        lay.addWidget(label("Journal du serveur (en direct)", "H2"))
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setFont(theme.mono_font(9))
        self.logs.setMaximumBlockCount(1000)
        self._lines: deque[str] = deque(maxlen=1000)   # cheap buffer; the widget is only filled while visible
        self.logs.setPlaceholderText("Le journal apparaîtra ici quand le serveur démarrera.")
        lay.addWidget(self.logs, 1)
        srv.log.connect(self._log)
        srv.stateChanged.connect(self.refresh)
        ctx.modelsChanged.connect(self.refresh)
        self.refresh()

    def _log(self, line: str):
        self._lines.append(line)
        if self.isVisible():
            self.logs.appendPlainText(line)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self.logs.setPlainText("\n".join(self._lines))
        self.logs.verticalScrollBar().setValue(self.logs.verticalScrollBar().maximum())

    def hideEvent(self, e):  # noqa: N802
        super().hideEvent(e)
        self.logs.clear()            # frees the text layout (≈ 6.6 KB per line)

    def _save(self, *_):
        s = self.ctx.settings.server
        s.port, s.ctx_size, s.gpu_layers = self.port.value(), self.ctx_size.currentData(), self.ngl.value()
        s.batch_size, s.ubatch_size, s.kv_type = self.batch.currentData(), self.ubatch.currentData(), self.kv.currentData()
        s.flash_attn, s.threads = self.fa.isChecked(), self.threads.value()
        self.ctx.save_later()

    def reset(self):
        d = config.ServerSettings()
        self.port.setValue(d.port)
        self.ctx_size.setCurrentIndex(self.ctx_size.findData(d.ctx_size))
        self.ngl.setValue(d.gpu_layers)
        self.batch.setCurrentIndex(self.batch.findData(d.batch_size))
        self.ubatch.setCurrentIndex(self.ubatch.findData(d.ubatch_size))
        self.kv.setCurrentIndex(self.kv.findData(d.kv_type))
        self.fa.setChecked(d.flash_attn)
        self.threads.setValue(d.threads)
        self._save()
        self.ctx.toast.emit("Réglages recommandés rétablis (redémarrez le serveur pour les appliquer).", None, None)

    def start(self):
        self._save()
        self.ctx.load_model(self.ctx.default_model())
        if self.ctx.server.state == "ready":
            # same model: explicit restart to apply new parameters
            self.ctx.server.start(self.ctx.server.model_path, self.ctx.settings.server, self.ctx.vram_free_for_model())

    def refresh(self, *_):
        srv = self.ctx.server
        txt, col = STATE_TXT.get(srv.state, (srv.state, theme.MUTED))
        self.state.setText(txt)
        self.state.setStyleSheet(f"color: {col};")
        if srv.state == "ready" and srv.plan:
            p = srv.plan
            self.info.setText(f"Modèle : {p.model.name} — couches sur la carte graphique : {srv.offload or p.gpu_layers} — "
                              f"contexte : {p.ctx} tokens — chargé en {srv.load_seconds or 0:.1f} s")
        elif srv.state == "stopped":
            m = self.ctx.default_model()
            self.info.setText(f"Modèle qui sera chargé : {m.name}" if m else "Aucun modèle installé.")
        else:
            self.info.setText("")
        self.url.setText(f"API : {srv.url}/v1" if srv.state == "ready" else "API : (serveur arrêté)")
        has_model = self.ctx.default_model() is not None
        self.start_btn.setEnabled(srv.state == "stopped" and has_model)
        self.start_btn.setToolTip("Démarrer le serveur avec le modèle choisi" if has_model else
                                  "Aucun modèle installé : ouvrez l'onglet « Modèles » pour en ajouter un.")
        self.restart_btn.setEnabled(srv.state == "ready")
        self.stop_btn.setEnabled(srv.state in ("ready", "starting"))
