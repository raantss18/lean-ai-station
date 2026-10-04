"""Lean tab: editor, « Vérifier » (compile) and « Prouver » (generate → compile → feedback loop)."""
from __future__ import annotations

import socket
import urllib.parse
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QInputDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
                               QPlainTextEdit, QSpinBox, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from .. import leancheck, texio
from ..errors import Friendly, friendly
from ..examples import EXAMPLES, VERIFY_SAMPLE
from ..services import CompileResult
from . import theme
from .widgets import BusyBar, Card, LeanEditor, button, label, shortcut

STATUS_ICON = {"génération": "✍️", "compilation": "⚙️", "accepté": "✅", "refusé": "❌", "erreur": "⚠️", "annulé": "⏹"}
DEFAULT_TEXT = EXAMPLES[1].statement + "\n"


def lean4web_url() -> str | None:
    try:
        with socket.create_connection(("127.0.0.1", 8890), timeout=0.2):
            return "http://127.0.0.1:8890"
    except OSError:
        return None


class _NoAttempts:
    attempts: list = []
    n = 0


class LeanPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.file_path: str = ctx.session.get("lean_file", "")
        self._pending_prove = False
        self._pending_translate = False
        self._active = ctx.prover          # whose attempts the list shows: ctx.prover or ctx.formalizer
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 12)
        lay.setSpacing(8)

        self.busy = BusyBar()
        self.busy.cancelled.connect(self.stop)
        lay.addWidget(self.busy)

        # --- left column: ① problem → ② Lean statement → ③ actions
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)

        h1 = QHBoxLayout()
        h1.addWidget(label("① Décrivez votre problème", "H2"))
        h1.addStretch(1)
        self.ex_btn = button("Exemples ▾", tip="Charger un exercice d'exemple (problème + énoncé Lean)")
        self.menu = QMenu(self)
        for ex in EXAMPLES:
            self.menu.addAction(f"{ex.title}  ({ex.level})", lambda e=ex: self.load_example(e))
        self.menu.addSeparator()
        self.menu.addAction("Fichier Lean à vérifier (exemple)", lambda: self.editor.set_text_undoable(VERIFY_SAMPLE))
        self.ex_btn.setMenu(self.menu)
        self.tex_btn = button("📄 Importer un .tex…", tip="Lire un fichier LaTeX et en extraire un théorème, un lemme "
                              "ou un exercice (vous pouvez aussi glisser le .tex sur la fenêtre)", slot=self.import_tex)
        h1.addWidget(self.ex_btn)
        h1.addWidget(self.tex_btn)
        ll.addLayout(h1)
        self.nl_edit = QPlainTextEdit()
        self.nl_edit.setMaximumHeight(120)
        self.nl_edit.setMinimumHeight(70)
        self.nl_edit.setPlaceholderText("Écrivez le problème avec vos mots, en français ou en anglais.\n"
                                        "Exemple : « Montrer que la somme de deux entiers pairs est paire. »\n"
                                        "Les formules LaTeX sont acceptées : $a^2 + b^2 \\ge 2ab$.")
        self.nl_edit.setPlainText(ctx.session.get("lean_nl", ""))
        self.nl_edit.textChanged.connect(self._nl_changed)
        ll.addWidget(self.nl_edit)
        h1b = QHBoxLayout()
        self.translate_btn = button("✨ Traduire en Lean", "Primary",
                                    "L'IA traduit votre texte en un énoncé Lean (Ctrl+T). Vous pourrez le relire et le corriger.",
                                    self.translate)
        h1b.addWidget(self.translate_btn)
        h1b.addWidget(label("Lean est le langage dans lequel la preuve sera vérifiée.", "Muted"), 1)
        ll.addLayout(h1b)

        h2 = QHBoxLayout()
        h2.addWidget(label("② Énoncé Lean (à relire)", "H2"))
        self.file_label = label(obj="Muted")
        self.file_label.setMaximumWidth(150)
        h2.addWidget(self.file_label, 1)
        self.open_btn = button("Ouvrir…", tip="Ouvrir un fichier .lean et le faire vérifier par Lean (Ctrl+O).\n"
                               "Vous pouvez aussi glisser un fichier .lean sur la fenêtre.", slot=self.open_file)
        self.save_btn = button("Enregistrer", tip="Enregistrer l'éditeur dans un fichier .lean (Ctrl+S)", slot=self.save_file)
        h2.addWidget(self.open_btn)
        h2.addWidget(self.save_btn)
        ll.addLayout(h2)
        self.editor = LeanEditor()
        self.editor.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.editor.setPlaceholderText("L'énoncé Lean apparaîtra ici après la traduction. Vous pouvez aussi l'écrire vous-même :\n\n"
                                       "theorem exemple (a b : ℝ) : a + b = b + a := by sorry"
                                       "\n\nAstuce : tapez \\R puis espace pour obtenir ℝ, \\le pour ≤, \\to pour →.")
        self.editor.setPlainText(ctx.session.get("lean_editor", DEFAULT_TEXT))
        self.editor.textChanged.connect(self._editor_changed)
        ll.addWidget(self.editor, 1)
        self._review_text = ""
        self.review_card = Card(margins=12)
        self.review_card.lay.addWidget(label(
            "<b>⚠ Relisez cet énoncé.</b> Lean vérifiera la preuve de <i>exactement</i> ce texte, mais il ne peut pas "
            "savoir si c'est bien <i>votre</i> problème. Hypothèses oubliées ? Mauvais nombres ? Corrigez ici, "
            "ou cliquez sur « Retraduire ».", wrap=True))
        rr = QHBoxLayout()
        self.ok_prove_btn = button("✅ C'est bon : prouver", "Primary", "L'IA cherche maintenant une preuve de cet énoncé",
                                   self.prove)
        self.retranslate_btn = button("↻ Retraduire", tip="Demander une nouvelle traduction", slot=self.translate)
        rr.addWidget(self.ok_prove_btn)
        rr.addWidget(self.retranslate_btn)
        rr.addStretch(1)
        self.review_card.lay.addLayout(rr)
        self.review_card.hide()
        ll.addWidget(self.review_card)

        h3 = QHBoxLayout()
        h3.addWidget(label("③ Faites prouver", "H2"))
        h3.addStretch(1)
        self.options_btn = button("⚙ Options", tip="Version de Lean utilisée, nombre d'essais")
        self.options_btn.setCheckable(True)
        self.options_btn.toggled.connect(lambda on: self.options_box.setVisible(on))
        self.verify_btn = button("✔ Vérifier", tip="Faire vérifier l'énoncé ou le fichier par Lean (Ctrl+Entrée)", slot=self.verify)
        self.prove_btn = button("✨ Prouver", "Primary",
                                "L'IA cherche une preuve, Lean la vérifie (Ctrl+Maj+Entrée)", slot=self.prove)
        self.stop_btn = button("⏹ Arrêter", "Danger", "Arrêter l'opération en cours (Échap)", slot=self.stop)
        self.stop_btn.setEnabled(False)
        for b in (self.options_btn, self.verify_btn, self.prove_btn, self.stop_btn):
            h3.addWidget(b)
        ll.addLayout(h3)
        self.options_box = QWidget()
        ob = QHBoxLayout(self.options_box)
        ob.setContentsMargins(0, 0, 0, 0)
        ob.addWidget(label("Espace Lean :"))
        self.ws_combo = QComboBox()
        self.ws_combo.setMinimumWidth(300)
        self.ws_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.ws_combo.setToolTip("Version de Lean et de Mathlib utilisée pour vérifier.\n"
                                 "« Prouveur (Lean 4.9) » donne les meilleurs résultats avec Goedel-Prover.")
        self.ws_combo.currentIndexChanged.connect(self._ws_changed)
        ob.addWidget(self.ws_combo, 1)
        ob.addWidget(label("Essais :"))
        self.tries = QSpinBox()
        self.tries.setRange(1, 32)
        self.tries.setValue(ctx.settings.prove_attempts)
        self.tries.setToolTip("Nombre maximal d'essais : après chaque échec, les erreurs de Lean sont renvoyées à l'IA "
                              "pour qu'elle corrige sa preuve.")
        self.tries.valueChanged.connect(self._tries_changed)
        ob.addWidget(self.tries)
        self.options_box.hide()
        ll.addWidget(self.options_box)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        self.status_card = Card()
        self.status_title = label("Prêt", "H2", wrap=True)
        self.status_detail = label("Écrivez un énoncé (ou choisissez « Exemples »), puis cliquez sur « Prouver ».",
                                   "Muted", wrap=True)
        self.status_card.lay.addWidget(self.status_title)
        self.status_card.lay.addWidget(self.status_detail)
        self.status_card.setToolTip("Un « token » est un morceau de mot (environ ¾ de mot) : tokens/s mesure la vitesse "
                                    "d'écriture de l'IA.")
        rl.addWidget(self.status_card)

        self.stack = QStackedWidget()
        rl.addWidget(self.stack, 1)
        # empty state
        empty = QLabel("Comment ça marche\n\n"
                       "①  Écrivez votre problème en français (ou importez un fichier .tex)\n"
                       "②  L'IA le traduit en Lean : relisez l'énoncé\n"
                       "③  L'IA cherche la preuve, Lean la vérifie ligne par ligne\n\n"
                       "Une preuve n'est jamais acceptée sans le feu vert de Lean.\n"
                       "Nouveau avec Lean ? Cliquez sur « ❓ Aide » dans la barre de gauche.")
        empty.setObjectName("Muted")
        empty.setAlignment(Qt.AlignCenter)
        self.stack.addWidget(empty)
        # results
        res = QWidget()
        rs = QVBoxLayout(res)
        rs.setContentsMargins(0, 0, 0, 0)
        self.attempts = QListWidget()
        self.attempts.setMaximumHeight(130)
        self.attempts.setWordWrap(True)
        self.attempts.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.attempts.setToolTip("Chaque essai de l'IA. Cliquez pour voir son raisonnement, son code et les erreurs.")
        self.attempts.currentRowChanged.connect(self._show_attempt)
        self.attempts.itemActivated.connect(lambda it: self._goto_line(it))
        rs.addWidget(self.attempts)
        self.tabs = QTabWidget()
        self.reasoning = QPlainTextEdit()
        self.reasoning.setReadOnly(True)
        self.reasoning.setFont(theme.mono_font(10))
        self.reasoning.setMaximumBlockCount(20000)
        self.code_view = LeanEditor(read_only=True)
        self.code_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.errors_view = QPlainTextEdit()
        self.errors_view.setReadOnly(True)
        self.errors_view.setFont(theme.mono_font(10))
        self.reasoning.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.tabs.addTab(self.reasoning, "Réflexion de l'IA (en anglais)")
        self.tabs.addTab(self.code_view, "Code testé")
        self.tabs.addTab(self.errors_view, "Messages de Lean")
        rs.addWidget(self.tabs, 1)
        # final proof
        self.final_card = Card()
        self.final_card.lay.addWidget(label("Preuve vérifiée", "H2"))
        self.final_view = LeanEditor(read_only=True)
        self.final_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.final_view.setMinimumHeight(140)
        self.final_card.lay.addWidget(self.final_view)
        fr = QHBoxLayout()
        fr2 = QHBoxLayout()
        fr.addWidget(button("📋 Copier", "Primary", "Copier la preuve dans le presse-papiers", self.copy_final))
        fr.addWidget(button("💾 Enregistrer…", tip="Enregistrer la preuve dans un fichier .lean", slot=self.save_final))
        fr2.addWidget(button("↩ Vers l'éditeur", tip="Remplacer le contenu de l'éditeur par la preuve (annulable avec Ctrl+Z)",
                             slot=lambda: self.editor.set_text_undoable(self.final_view.toPlainText())))
        self.l4w_btn = button("Lean4Web ↗", tip="Ouvrir la preuve dans votre Lean4Web local (navigateur).\n"
                              "Attention : Lean4Web utilise une autre version de Lean.", slot=self.open_lean4web)
        fr2.addWidget(self.l4w_btn)
        self.tex_out_btn = button("📄 LaTeX ▾", tip="Exporter l'énoncé et la preuve en LaTeX (compatible Overleaf)")
        tm = QMenu(self)
        tm.addAction("Enregistrer un fichier .tex…", self.export_tex)
        tm.addAction("Copier le code LaTeX", self.copy_tex)
        tm.addAction("Ouvrir Overleaf (navigateur)", self.open_overleaf)
        self.tex_out_btn.setMenu(tm)
        fr.addWidget(self.tex_out_btn)
        fr.addStretch(1)
        fr2.addStretch(1)
        self.final_card.lay.addLayout(fr)
        self.final_card.lay.addLayout(fr2)
        self.final_card.hide()
        rs.addWidget(self.final_card)
        self.stack.addWidget(res)
        split.addWidget(right)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 6)
        lay.addWidget(split, 1)

        # shortcuts
        shortcut(self, "Ctrl+T", self.translate)
        shortcut(self, "Ctrl+Return", self.verify)
        shortcut(self, "Ctrl+Shift+Return", self.prove)
        shortcut(self, "Escape", self.stop)
        shortcut(self, "Ctrl+S", self.save_file)
        shortcut(self, "Ctrl+O", self.open_file)

        # streaming buffer (flushed every 60 ms to keep the UI smooth)
        self._buf: list[str] = []
        self._flush = QTimer(self)
        self._flush.setInterval(60)
        self._flush.timeout.connect(self._flush_tokens)
        self._live_index = -1

        p = ctx.prover
        p.attemptStarted.connect(self._attempt_started)
        p.token.connect(self._token)
        p.attemptUpdated.connect(self._attempt_updated)
        p.finished.connect(self._prove_finished)
        p.infraError.connect(self._infra_error)
        f = ctx.formalizer
        f.attemptStarted.connect(self._attempt_started)
        f.token.connect(self._token)
        f.attemptUpdated.connect(self._attempt_updated)
        f.finished.connect(self._translate_finished)
        f.infraError.connect(self._infra_error)
        ctx.translateRequest.connect(self.translate_text)
        ctx.texRequest.connect(self.load_tex)
        ctx.verifier.finished.connect(self._verified)
        ctx.workspacesChanged.connect(self._fill_ws)
        ctx.server.stateChanged.connect(self._server_state)
        ctx.server.failed.connect(self._loading_failed)
        ctx.proveRequest.connect(self.prove_statement)
        self._fill_ws()
        self._update_file_label()

    # ------------------------------------------------------------ state helpers
    def _running(self) -> bool:
        return (self.ctx.prover.running or self.ctx.formalizer.running or self.ctx.verifier.busy
                or self._pending_prove or self._pending_translate)

    def _set_running(self, on: bool):
        self.prove_btn.setEnabled(not on)
        self.verify_btn.setEnabled(not on)
        self.translate_btn.setEnabled(not on)
        self.tex_btn.setEnabled(not on)
        self.ok_prove_btn.setEnabled(not on)
        self.retranslate_btn.setEnabled(not on)
        self.ws_combo.setEnabled(not on)
        self.stop_btn.setEnabled(on)
        if not on:
            self.busy.stop()

    def _fill_ws(self):
        self.ws_combo.blockSignals(True)
        self.ws_combo.clear()
        for w in self.ctx.workspaces:
            text = w.label + ("" if not w.problems else "  — indisponible")
            self.ws_combo.addItem(text, w.key)
            i = self.ws_combo.count() - 1
            tip = w.description + ("\n\n⚠️ " + " ".join(w.problems) if w.problems else "")
            self.ws_combo.setItemData(i, tip, Qt.ToolTipRole)
            if w.problems:
                self.ws_combo.model().item(i).setEnabled(False)
            if w.key == self.ctx.settings.workspace:
                self.ws_combo.setCurrentIndex(i)
        if not self.ctx.workspaces:
            self.ws_combo.addItem("Recherche des espaces Lean…")
        self.ws_combo.blockSignals(False)

    def _ws_changed(self, i: int):
        key = self.ws_combo.itemData(i)
        if key:
            self.ctx.settings.workspace = key
            self.ctx.save_later()

    def _tries_changed(self, v: int):
        self.ctx.settings.prove_attempts = v
        self.ctx.save_later()

    def _nl_changed(self):
        self.ctx.session["lean_nl"] = self.nl_edit.toPlainText()
        self.ctx.save_later()

    def _editor_changed(self):
        self.ctx.session["lean_editor"] = self.editor.toPlainText()
        self.ctx.save_later()
        if self.review_card.isVisible() and self.editor.toPlainText() != self._review_text:
            self.review_card.hide()          # the user is editing the statement: the warning has been read
        if self.editor.extraSelections():
            self.editor.set_error_lines({})

    def _update_file_label(self):
        self.file_label.setText(f"📄 {Path(self.file_path).name}" if self.file_path else "(non enregistré)")
        self.file_label.setToolTip(self.file_path or "Le texte de l'éditeur est conservé automatiquement entre deux sessions.")

    def _status(self, title: str, detail: str = "", color: str | None = None):
        self.status_title.setText(title)
        self.status_title.setStyleSheet(f"color: {color};" if color else "")
        self.status_detail.setText(detail)

    def _need_ws(self):
        ws = self.ctx.workspace()
        if ws is None or ws.check():
            self.ctx.banner.emit(friendly("workspace"), "\n".join(ws.problems) if ws else "Aucun espace sélectionné.")
            return None
        return ws

    # ------------------------------------------------------------ verify
    def verify(self):
        if self._running():
            return
        ws = self._need_ws()
        if not ws:
            return
        code = self.editor.toPlainText()
        if not code.strip():
            self._status("L'éditeur est vide", "Écrivez du code Lean ou choisissez un exemple (bouton « Exemples »).", theme.WARN)
            return
        self._active = _NoAttempts()          # the list below shows Lean messages, not AI attempts
        self._set_running(True)
        self.busy.start("Lean vérifie le fichier… (quelques secondes ; jusqu'à 1 minute la première fois)")
        self._status("Vérification en cours…", f"Espace : {ws.label}")
        self.editor.set_error_lines({})
        self.ctx.verifier.compile(code, ws, self.ctx.settings.compile_timeout_s, None)

    def _verified(self, res: CompileResult):
        self._set_running(False)
        if res.cancelled:
            self._status("Vérification annulée", "", theme.MUTED)
            return
        if res.infra_error and not res.verdict.errors:
            self._status("Lean n'a pas pu vérifier", res.verdict.summary, theme.ERR)
            self.ctx.banner.emit(friendly("workspace"), res.infra_error)
            return
        v = res.verdict
        self.stack.setCurrentIndex(1)
        self.attempts.clear()
        self.final_card.hide()
        lines = {}
        out = []
        for m in leancheck.parse_lean_json(res.stdout):
            sev = {"error": "❌ Erreur", "warning": "⚠️ Avertissement", "information": "ℹ️ Info"}.get(m.severity, m.severity)
            out.append(f"{sev} — ligne {m.line}, colonne {m.col + 1} :\n{m.text}\n")
            if m.severity == "error":
                lines[m.line] = m.text
            it = QListWidgetItem(f"{sev}, ligne {m.line} : {m.text.splitlines()[0][:110] if m.text else ''}")
            it.setData(Qt.UserRole, m.line)
            it.setToolTip(m.text)
            self.attempts.addItem(it)
        self.errors_view.setPlainText("\n".join(out) or "Aucun message : tout est correct.")
        self.code_view.setPlainText(res.code)
        self.reasoning.setPlainText("(Vérification simple : pas d'IA utilisée.)")
        self.tabs.setCurrentIndex(2)
        self.editor.set_error_lines(lines)
        if v.ok:
            self._status("✅ " + v.summary, f"Vérifié en {res.seconds:.1f} s.", theme.OK)
        elif v.has_sorry and not v.errors:
            self._status("⚠️ Accepté, mais incomplet", "Le fichier contient « sorry » : certaines preuves manquent. "
                         "Cliquez sur « Prouver » pour que l'IA les complète.", theme.WARN)
        elif res.timed_out:
            self._status("⏱ " + v.summary, "Augmentez le délai dans « Système » ou simplifiez le fichier.", theme.WARN)
        else:
            self._status("❌ " + v.summary, "Les lignes en rouge contiennent des erreurs. Double-cliquez sur un message "
                         "pour aller à la ligne.", theme.ERR)

    def _goto_line(self, item: QListWidgetItem):
        ln = item.data(Qt.UserRole)
        if isinstance(ln, int):
            block = self.editor.document().findBlockByNumber(ln - 1)
            c = self.editor.textCursor()
            c.setPosition(block.position())
            self.editor.setTextCursor(c)
            self.editor.setFocus()

    # ------------------------------------------------------------ prove
    def prove_statement(self, statement: str, nl: str = ""):
        """Called from Home examples: load the statement (and its plain-language text) and start immediately."""
        self.ctx.navigate.emit("lean")
        if self._running():
            self.ctx.toast.emit("Une recherche est déjà en cours : arrêtez-la d'abord (Échap).", None, None)
            return
        if nl:
            self.nl_edit.setPlainText(nl)
        self.review_card.hide()
        self.editor.set_text_undoable(statement + "\n")
        self.prove()

    def load_example(self, ex):
        self.nl_edit.setPlainText(ex.blurb)
        self.review_card.hide()
        self.editor.set_text_undoable(ex.statement + "\n")

    # ------------------------------------------------------------ translate (natural language → Lean)
    def translate_text(self, text: str):
        """Called from Home: put the problem in the box and translate it."""
        self.ctx.navigate.emit("lean")
        if self._running():
            self.ctx.toast.emit("Une opération est en cours : arrêtez-la d'abord (Échap).", None, None)
            return
        self.nl_edit.setPlainText(text)
        self.translate()

    def translate(self):
        if self._running():
            return
        text = self.nl_edit.toPlainText().strip()
        if not text:
            self._status("Décrivez d'abord votre problème", "Écrivez-le dans la zone ① (en français ou en anglais), "
                         "ou choisissez « Exemples ».", theme.WARN)
            self.nl_edit.setFocus()
            return
        if self.ctx.formalizer_model() is None:
            self.ctx.banner.emit(friendly("no_formalizer"), "")
            return
        ws = self._need_ws()
        if not ws:
            return
        self._pending_translate = True
        self._active = self.ctx.formalizer
        self._set_running(True)
        self.stack.setCurrentIndex(1)
        self.attempts.clear()
        self.reasoning.clear()
        self.code_view.clear()
        self.errors_view.clear()
        self.final_card.hide()
        self.review_card.hide()
        target = self.ctx.formalizer_model()
        if not (self.ctx.server.state == "ready" and self.ctx.server.model_path == target):
            self.busy.start("Chargement du traducteur en mémoire graphique… (environ 10 à 30 s)")
            self._status("Préparation…", "Le modèle de traduction se charge ; la traduction démarrera automatiquement.")
        self.ctx.ensure_model(self._start_translation, role="formalizer")

    def _start_translation(self):
        if not self._pending_translate:
            return
        self._pending_translate = False
        ws = self.ctx.workspace()
        if ws is None:
            self._set_running(False)
            return
        self._active = self.ctx.formalizer
        self.busy.start("L'IA traduit votre problème…")
        self.ctx.formalizer.start(self.nl_edit.toPlainText(), ws, self.ctx.settings.translate_attempts,
                                  self.ctx.settings.compile_timeout_s)
        self._status("Traduction en cours…", "L'IA réfléchit, écrit l'énoncé en Lean, puis Lean contrôle qu'il est valide.")
        self._flush.start()

    def _translate_finished(self, ok: bool, summary: str):
        self._flush_tokens()
        self._flush.stop()
        self._set_running(False)
        f = self.ctx.formalizer
        if "arrêtée" in summary:
            self._status("⏹ " + summary, "Vous pouvez relancer avec « Traduire en Lean ».", theme.MUTED)
            return
        if not f.statement:
            self._status("❌ L'IA n'a pas produit d'énoncé", "Reformulez le problème (plus précis, avec les hypothèses) "
                         "et réessayez, ou écrivez l'énoncé vous-même dans la zone ②.", theme.ERR)
            return
        self.editor.set_text_undoable(f.statement)
        self._review_text = self.editor.toPlainText()
        self.review_card.show()
        if ok:
            self._status("✅ Énoncé traduit — à relire", "Lean comprend cet énoncé. Vérifiez dans la zone ② qu'il dit bien "
                         "ce que vous voulez, puis cliquez sur « C'est bon : prouver ».", theme.OK)
        else:
            self.editor.set_error_lines({m.line: m.text for m in f.errors if m.severity == "error"})
            self._status("⚠️ Traduction à corriger", summary + " Les lignes en rouge sont refusées par Lean.", theme.WARN)

    # ------------------------------------------------------------ LaTeX in / out
    def import_tex(self):
        start = self.ctx.session.get("last_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Importer un fichier LaTeX", start, "Fichiers LaTeX (*.tex);;Tous (*)")
        if path:
            self.load_tex(path)

    def load_tex(self, path: str):
        """Read a .tex file, let the user pick a theorem/lemma/exercise, and put it in the problem box."""
        self.ctx.navigate.emit("lean")
        if self._running():
            self.ctx.toast.emit("Une opération est en cours : arrêtez-la d'abord (Échap).", None, None)
            return
        try:
            tex = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self.ctx.banner.emit(Friendly("Impossible d'ouvrir le fichier", "Le fichier n'est pas lisible (droits).",
                                          [], "warn"), str(e))
            return
        items = texio.extract_statements(tex)
        if not items:
            self.ctx.banner.emit(Friendly("Aucun énoncé trouvé", "Ce fichier .tex ne contient ni théorème, ni lemme, "
                                          "ni exercice, ni texte exploitable.", [], "warn"), "")
            return
        chosen = items[0]
        if len(items) > 1:
            labels = [f"{it.title} : {it.body.replace(chr(10), ' ')[:70]}…" for it in items]
            pick, ok = QInputDialog.getItem(self, "Choisir l'énoncé", "Ce fichier contient plusieurs énoncés.\n"
                                            "Lequel voulez-vous traduire en Lean ?", labels, 0, False)
            if not ok:
                return
            chosen = items[labels.index(pick)]
        self.ctx.session["last_dir"] = str(Path(path).parent)
        self.nl_edit.setPlainText(chosen.body)
        self.review_card.hide()
        self.ctx.toast.emit(f"Importé : {chosen.title}. Cliquez sur « Traduire en Lean ».", None, None)

    def latex_source(self) -> str:
        code = self.final_view.toPlainText()
        try:
            name = leancheck.theorem_name(code)
        except leancheck.StatementError:
            name = "theoreme"
        stmt = ""
        try:
            full = leancheck.prepare_statement(self.editor.toPlainText())
            k = full.index("theorem")
            stmt = full[k:]
        except (leancheck.StatementError, ValueError):
            pass
        ws = self.ctx.workspace()
        meta = f"{ws.label.split(' (')[0]}, Lean {ws.toolchain.split(':')[-1].lstrip('v')}" if ws else ""
        return texio.export_document(self.nl_edit.toPlainText(), stmt, code, name, meta)

    def export_tex(self):
        try:
            name = leancheck.theorem_name(self.final_view.toPlainText()) + ".tex"
        except leancheck.StatementError:
            name = "preuve.tex"
        start = self.ctx.session.get("last_dir", str(Path.home() / "Documents"))
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer en LaTeX", str(Path(start) / name), "Fichiers LaTeX (*.tex)")
        if not path:
            return
        if not path.endswith(".tex"):
            path += ".tex"
        try:
            Path(path).write_text(self.latex_source(), encoding="utf-8")
        except OSError as e:
            self.ctx.banner.emit(Friendly("Enregistrement impossible", "Choisissez un autre dossier.", [], "warn"), str(e))
            return
        self.ctx.session["last_dir"] = str(Path(path).parent)
        self.ctx.toast.emit(f"Enregistré : {Path(path).name}. Dans Overleaf : « Nouveau projet → Téléverser », "
                            "compilateur XeLaTeX.", None, None)

    def copy_tex(self):
        QGuiApplication.clipboard().setText(self.latex_source())
        self.ctx.toast.emit("Code LaTeX copié : collez-le dans un fichier .tex d'Overleaf (compilateur XeLaTeX).", None, None)

    def open_overleaf(self):
        QDesktopServices.openUrl(QUrl(self.ctx.settings.overleaf_url))

    def prove(self):
        if self._running():
            return
        try:
            leancheck.theorem_name(leancheck.prepare_statement(self.editor.toPlainText()))
        except leancheck.StatementError as e:
            self.ctx.banner.emit(Friendly("Énoncé incomplet", str(e) + "\nExemple : theorem t (a : ℕ) : a + 0 = a := by sorry",
                                          [], "warn"), "")
            return
        ws = self._need_ws()
        if not ws:
            return
        self._pending_prove = True
        self._active = self.ctx.prover
        self.review_card.hide()
        self._set_running(True)
        self.stack.setCurrentIndex(1)
        self.attempts.clear()
        self.reasoning.clear()
        self.code_view.clear()
        self.errors_view.clear()
        self.final_card.hide()
        if not (self.ctx.server.state == "ready" and self.ctx.server.model_path == self.ctx.default_model()):
            self.busy.start("Chargement du modèle en mémoire graphique… (environ 10 à 30 s)")
            self._status("Préparation…", "Le modèle d'IA se charge, la recherche démarrera automatiquement.")
        self.ctx.ensure_model(self._start_prover)

    def _start_prover(self):
        if not self._pending_prove:
            return
        self._pending_prove = False
        ws = self.ctx.workspace()
        if ws is None:
            self._set_running(False)
            return
        self._active = self.ctx.prover
        self.busy.start("L'IA réfléchit…")
        try:
            self.ctx.prover.start(self.editor.toPlainText(), ws, self.tries.value(), self.ctx.settings.sampling,
                                  self.ctx.settings.compile_timeout_s,
                                  self.ctx.server.plan.ctx if self.ctx.server.plan else self.ctx.settings.server.ctx_size)
        except leancheck.StatementError as e:
            self._set_running(False)
            self.ctx.banner.emit(Friendly("Énoncé incomplet", str(e), [], "warn"), "")
            return
        self._status("Recherche de preuve…", f"Jusqu'à {self.tries.value()} essais. Espace : {ws.label}")
        self._flush.start()

    def _loading_failed(self, *_):
        if self._pending_prove or self._pending_translate:
            self._pending_prove = self._pending_translate = False
            self._set_running(False)
            self._status("Le modèle n'a pas pu être chargé", "Voir le message en haut de la fenêtre.", theme.ERR)

    def _server_state(self, st: str):
        if st == "stopped" and self._pending_prove and not self.ctx.server.proc:
            pass  # failure path handled by _loading_failed

    def stop(self):
        if self._pending_prove or self._pending_translate:
            self._pending_prove = self._pending_translate = False
            self.ctx._after_ready.clear()
            self._set_running(False)
            self._status("Arrêté", "Le chargement continue en arrière-plan ; vous pourrez relancer.", theme.MUTED)
            return
        if self.ctx.prover.running:
            self.ctx.prover.cancel()
        elif self.ctx.formalizer.running:
            self.ctx.formalizer.cancel()
        elif self.ctx.verifier.busy:
            self.ctx.verifier.cancel()

    def _attempt_started(self, i: int):
        a = self._active.attempts[i]
        it = QListWidgetItem()
        self.attempts.addItem(it)
        self._render_item(i)
        self._live_index = i
        self.attempts.setCurrentRow(i)
        self.reasoning.clear()
        self.tabs.setCurrentIndex(0)
        n = self._active.n
        what = ("traduit votre problème" if a.kind == "translation" else
                "écrit une preuve" if a.kind == "initial" else "écrit une correction")
        self.busy.start(f"Essai {i + 1}/{n} : l'IA {what}…", maximum=n)
        self.busy.progress(i, n)

    def _render_item(self, i: int):
        a = self._active.attempts[i]
        it = self.attempts.item(i)
        if it is None:
            return
        extra = []
        if a.tokens:
            extra.append(f"{a.tokens} tokens, {a.tps:.0f} tok/s")
        if a.compile_seconds:
            extra.append(f"Lean {a.compile_seconds:.0f} s")
        kind = {"initial": "1re tentative", "translation": "traduction"}.get(a.kind, "correction")
        it.setText(f"{STATUS_ICON.get(a.status, '•')} Essai {i + 1} ({kind}) — {a.summary or a.status}"
                   + (f"   [{', '.join(extra)}]" if extra else ""))

    def _token(self, i: int, t: str):
        if i == self._live_index and self.attempts.currentRow() == i:
            self._buf.append(t)

    def _flush_tokens(self):
        if not self._buf:
            return
        text, self._buf = "".join(self._buf), []
        sb = self.reasoning.verticalScrollBar()
        at_end = sb.value() >= sb.maximum() - 4
        c = self.reasoning.textCursor()
        c.movePosition(QTextCursor.End)
        c.insertText(text)
        if at_end:
            sb.setValue(sb.maximum())

    def _attempt_updated(self, i: int):
        self._render_item(i)
        a = self._active.attempts[i]
        if a.status == "compilation":
            what = "l'énoncé" if a.kind == "translation" else "la preuve"
            self.busy.start(f"Essai {i + 1}/{self._active.n} : Lean vérifie {what}…", maximum=self._active.n)
            self.busy.progress(i, self._active.n)
        if self.attempts.currentRow() == i:
            self._show_attempt(i)

    def _show_attempt(self, i: int):
        attempts = self._active.attempts
        if not (0 <= i < len(attempts)) or not attempts:
            return
        a = attempts[i]
        self._buf = []
        self.reasoning.setPlainText(a.raw)
        self.reasoning.verticalScrollBar().setValue(self.reasoning.verticalScrollBar().maximum())
        self.code_view.setPlainText(a.code or "(pas de code extrait)")
        self.errors_view.setPlainText(a.errors_text or a.summary or "")

    def _prove_finished(self, ok: bool, summary: str):
        self._flush_tokens()
        self._flush.stop()
        self._set_running(False)
        p = self.ctx.prover
        if ok:
            self.final_view.setPlainText(p.final_code)
            self.final_card.show()
            self.l4w_btn.setVisible(lean4web_url() is not None)
            a = p.attempts[-1]
            self._status("✅ " + summary, f"Lean a accepté la preuve (sans « sorry », axiomes standard uniquement). "
                         f"Génération {a.gen_seconds:.0f} s à {a.tps:.0f} tokens/s, vérification {a.compile_seconds:.0f} s.",
                         theme.OK)
            self.ctx.session["last_proof"] = p.final_code
            self.ctx.save_later()
        elif "arrêtée" in summary:
            self._status("⏹ " + summary, "Vous pouvez relancer avec « Prouver ».", theme.MUTED)
        else:
            self._status("❌ " + summary, "Essayez d'augmenter le nombre d'essais, de simplifier l'énoncé, "
                         "ou de changer d'espace Lean.", theme.ERR)

    def _infra_error(self, kind: str, details: str):
        self._flush.stop()
        self._set_running(False)
        if kind == "server_down":
            f = friendly("server_crashed") if self.ctx.server.state != "ready" else friendly("generation")
            self._status("⚠️ Le moteur d'IA ne répond plus", "Cliquez sur « Redémarrer le modèle » dans le message en haut.",
                         theme.ERR)
        elif kind == "workspace":
            f = friendly("workspace")
            self._status("⚠️ Lean n'a pas pu vérifier", details[:300], theme.ERR)
        else:
            f = friendly(kind)
            self._status("⚠️ Problème pendant la génération", "", theme.ERR)
        self.ctx.banner.emit(f, details)

    # ------------------------------------------------------------ files
    def open_file(self):
        start = self.file_path or self.ctx.session.get("last_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Ouvrir un fichier Lean", start, "Fichiers Lean (*.lean);;Tous (*)")
        if path:
            self.load_file(path)

    def load_file(self, path: str, verify: bool = True):
        """Open a .lean file in the editor and (by default) verify it immediately."""
        self.ctx.navigate.emit("lean")
        if self._running():
            self.ctx.toast.emit("Une opération est en cours : arrêtez-la d'abord (Échap).", None, None)
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            self.ctx.banner.emit(Friendly("Impossible d'ouvrir le fichier", "Le fichier n'est pas lisible (droits ou "
                                          "encodage). Choisissez un autre fichier.", [], "warn"), str(e))
            return
        self.editor.set_text_undoable(text)
        self.file_path = path
        self.ctx.session["lean_file"] = path
        self.ctx.session["last_dir"] = str(Path(path).parent)
        self._update_file_label()
        self.ctx.toast.emit(f"Fichier ouvert : {Path(path).name}", None, None)
        if verify:
            self.verify()

    def _save_text(self, text: str, suggested: str) -> str | None:
        start = self.ctx.session.get("last_dir", str(Path.home() / "Documents"))
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer", str(Path(start) / suggested), "Fichiers Lean (*.lean)")
        if not path:
            return None
        if not path.endswith(".lean"):
            path += ".lean"
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as e:
            self.ctx.banner.emit(Friendly("Enregistrement impossible", "Choisissez un autre dossier.", [], "warn"), str(e))
            return None
        self.ctx.session["last_dir"] = str(Path(path).parent)
        self.ctx.toast.emit(f"Enregistré : {Path(path).name}", None, None)
        return path

    def save_file(self):
        if self.file_path:
            try:
                Path(self.file_path).write_text(self.editor.toPlainText(), encoding="utf-8")
                self.ctx.toast.emit(f"Enregistré : {Path(self.file_path).name}", None, None)
                return
            except OSError:
                pass
        p = self._save_text(self.editor.toPlainText(), "exercice.lean")
        if p:
            self.file_path = p
            self.ctx.session["lean_file"] = p
            self._update_file_label()

    def save_final(self):
        name = "preuve.lean"
        try:
            name = leancheck.theorem_name(self.final_view.toPlainText()) + ".lean"
        except leancheck.StatementError:
            pass
        self._save_text(self.final_view.toPlainText(), name)

    def copy_final(self):
        QGuiApplication.clipboard().setText(self.final_view.toPlainText())
        self.ctx.toast.emit("Preuve copiée dans le presse-papiers.", None, None)

    def open_lean4web(self):
        base = lean4web_url()
        if base:
            QDesktopServices.openUrl(QUrl(base + "/#code=" + urllib.parse.quote(self.final_view.toPlainText())))
