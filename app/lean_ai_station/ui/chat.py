"""Chat tab: streaming conversation with the loaded model."""
from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDoubleSpinBox, QFormLayout, QHBoxLayout, QPlainTextEdit, QSlider, QSpinBox,
                               QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from ..services import ChatStream
from . import theme
from .widgets import BusyBar, Card, button, label, shortcut


def _md_light(text: str) -> str:
    """Escape + minimal formatting: ```code``` blocks, <think> shown dimmed."""
    out = []
    parts = re.split(r"(```[a-z0-9]*\n.*?```)", text, flags=re.DOTALL)
    for p in parts:
        if p.startswith("```"):
            code = re.sub(r"^```[a-z0-9]*\n|```$", "", p)
            out.append(f"<pre style='background:{theme.PANEL2};padding:8px;border-radius:6px;"
                       f"font-family:monospace'>{html.escape(code)}</pre>")
        else:
            e = html.escape(p)
            e = e.replace("&lt;think&gt;", f"<span style='color:{theme.MUTED}'><i>Réflexion :</i> ")
            e = e.replace("&lt;/think&gt;", "</span><br>")
            out.append(e.replace("\n", "<br>"))
    return "".join(out)


class ChatPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.history: list[dict] = list(ctx.session.get("chat", []))
        self.stream: ChatStream | None = None
        self._live = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 12)
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(label("Discussion avec le modèle", "H2"))
        head.addStretch(1)
        self.new_btn = button("Nouvelle conversation", tip="Effacer la conversation (annulable)", slot=self.new_chat)
        head.addWidget(self.new_btn)
        ll.addLayout(head)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        ll.addWidget(self.view, 1)
        self.busy = BusyBar()
        self.busy.cancelled.connect(self.stop)
        ll.addWidget(self.busy)
        row = QHBoxLayout()
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Posez une question (ex. : « Explique la tactique linarith »)… Ctrl+Entrée pour envoyer")
        self.input.setMaximumHeight(110)
        row.addWidget(self.input, 1)
        col = QVBoxLayout()
        self.send_btn = button("Envoyer", "Primary", "Envoyer le message (Ctrl+Entrée)", slot=self.send)
        self.stop_btn = button("Arrêter", "Danger", "Arrêter la réponse (Échap)", slot=self.stop)
        self.stop_btn.setEnabled(False)
        col.addWidget(self.send_btn)
        col.addWidget(self.stop_btn)
        row.addLayout(col)
        ll.addLayout(row)
        split.addWidget(left)

        # settings panel
        side = Card()
        side.setMaximumWidth(330)
        side.lay.addWidget(label("Réglages de la réponse", "H2"))
        side.lay.addWidget(label("Consigne système (comportement de l'IA) :", "Muted"))
        self.system = QPlainTextEdit(ctx.settings.chat_system_prompt)
        self.system.setMaximumHeight(110)
        self.system.setToolTip("Texte envoyé au début de chaque conversation pour orienter l'IA.")
        self.system.textChanged.connect(self._save_settings)
        side.lay.addWidget(self.system)
        form = QFormLayout()
        s = ctx.settings.chat_sampling
        self.temp = QSlider(Qt.Horizontal)
        self.temp.setRange(0, 150)
        self.temp.setValue(int(s.temperature * 100))
        self.temp_lbl = label()
        self.temp.setToolTip("Température : basse = réponses sûres et répétables ; haute = plus variées.")
        self.temp.valueChanged.connect(self._save_settings)
        trow = QHBoxLayout()
        trow.addWidget(self.temp, 1)
        trow.addWidget(self.temp_lbl)
        form.addRow("Créativité", trow)
        self.top_p = QDoubleSpinBox()
        self.top_p.setRange(0.05, 1.0)
        self.top_p.setSingleStep(0.05)
        self.top_p.setValue(s.top_p)
        self.top_p.setToolTip("Top-p : ne garde que les mots les plus probables (0,95 conseillé).")
        self.top_p.valueChanged.connect(self._save_settings)
        form.addRow("Top-p", self.top_p)
        self.max_tok = QSpinBox()
        self.max_tok.setRange(64, 32768)
        self.max_tok.setSingleStep(256)
        self.max_tok.setValue(s.max_tokens)
        self.max_tok.setToolTip("Longueur maximale de la réponse, en tokens (≈ ¾ de mot).")
        self.max_tok.valueChanged.connect(self._save_settings)
        form.addRow("Longueur max.", self.max_tok)
        side.lay.addLayout(form)
        side.lay.addWidget(label("Note : Goedel-Prover est spécialisé dans les preuves Lean ; pour une discussion "
                                 "générale, chargez un autre modèle dans « Modèles ».", "Muted", wrap=True))
        side.lay.addStretch(1)
        split.addWidget(side)
        split.setStretchFactor(0, 4)

        shortcut(self, "Ctrl+Return", self.send)
        shortcut(self, "Escape", self.stop)
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(80)
        self._render_timer.timeout.connect(self.render)
        self._save_settings()
        self.render()

    def _save_settings(self):
        s = self.ctx.settings.chat_sampling
        s.temperature = self.temp.value() / 100
        s.top_p = self.top_p.value()
        s.max_tokens = self.max_tok.value()
        self.temp_lbl.setText(f"{s.temperature:.2f}")
        self.ctx.settings.chat_system_prompt = self.system.toPlainText()
        self.ctx.save_later()

    def render(self):
        if not self.history and not self._live:
            self.view.setHtml(f"<div style='color:{theme.MUTED};padding:30px;text-align:center'>"
                              "Aucun message pour l'instant.<br><br>Écrivez une question en bas puis cliquez sur « Envoyer ».<br>"
                              "Le modèle se chargera automatiquement si nécessaire.</div>")
            return
        parts = []
        for m in self.history + ([{"role": "assistant", "content": self._live + " ▌"}] if self.stream else []):
            who, bg = ("Vous", "#22304D") if m["role"] == "user" else ("IA", theme.PANEL)
            parts.append(f"<div style='background:{bg};padding:10px;margin:6px 0;border-radius:8px'>"
                         f"<b>{who}</b><br>{_md_light(m['content'])}</div>")
        self.view.setHtml("".join(parts))
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def send(self):
        text = self.input.toPlainText().strip()
        if not text or self.stream is not None or getattr(self, "_waiting", False):
            return
        self.history.append({"role": "user", "content": text})
        self.input.clear()
        self._persist()
        self.render()
        self._waiting = True
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        if self.ctx.server.state != "ready":
            self.busy.start("Chargement du modèle… (environ 10 à 30 s)")
        self.ctx.ensure_model(self._start)

    def _start(self):
        if not getattr(self, "_waiting", False):
            return
        self._waiting = False
        msgs = ([{"role": "system", "content": self.system.toPlainText()}] if self.system.toPlainText().strip() else [])
        msgs += self.history[-20:]
        s = self.ctx.settings.chat_sampling
        self._live = ""
        self.stream = ChatStream(self.ctx.server.url, self)
        self.stream.delta.connect(self._delta)
        self.stream.done.connect(self._done)
        self.stream.error.connect(self._error)
        self.stream.start(msgs, s.temperature, s.top_p, s.max_tokens)
        self.busy.start("L'IA écrit…")
        self._render_timer.start()

    def _delta(self, t: str):
        self._live += t

    def _finish(self):
        self._render_timer.stop()
        self.stream = None
        self.busy.stop()
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _done(self, d: dict):
        self.history.append({"role": "assistant", "content": d["text"]})
        self._live = ""
        self._finish()
        self._persist()
        self.render()
        self.ctx.toast.emit(f"Réponse : {d['tokens']} tokens à {d['tps']:.0f} tokens/s.", None, None)

    def _error(self, kind: str, details: str):
        partial = self._live
        self._live = ""
        self._finish()
        if partial:
            self.history.append({"role": "assistant", "content": partial + ("\n\n[arrêté]" if kind == "cancelled" else "\n\n[interrompu]")})
            self._persist()
        self.render()
        if kind != "cancelled":
            from ..errors import friendly
            self.ctx.banner.emit(friendly("server_crashed" if kind in ("unreachable", "stalled") else "generation"), details)

    def stop(self):
        if getattr(self, "_waiting", False):
            self._waiting = False
            self.ctx._after_ready.clear()
            self.busy.stop()
            self.send_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
        if self.stream is not None:
            self.stream.cancel()

    def new_chat(self):
        if self.stream is not None or not self.history:
            return
        old = self.history
        self.history = []
        self._persist()
        self.render()

        def undo():
            self.history = old
            self._persist()
            self.render()
        self.ctx.toast.emit("Conversation effacée.", undo, None)

    def _persist(self):
        self.ctx.session["chat"] = self.history[-200:]
        self.ctx.save_later()
