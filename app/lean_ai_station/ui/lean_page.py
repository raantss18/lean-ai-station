"""Lean tab (v1.1): one « dossier » per proof — a conversation thread (problem, follow-up requests, results) on the
left, the current artefacts (Lean statement, proof, explanation, attempts, Lean messages) on the right.
The pipeline (ui/pipeline.py) chains translate → prove → explain and switches models automatically."""
from __future__ import annotations

import datetime as _dt
import html
import re
import socket
import urllib.parse
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QListWidget,
                               QListWidgetItem, QMenu, QPlainTextEdit, QSpinBox, QSplitter, QTabWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from .. import leancheck, texio
from ..errors import Friendly, friendly
from ..examples import EXAMPLES, VERIFY_SAMPLE, example_text
from ..i18n import _
from ..services import CompileResult
from . import theme
from .widgets import BusyBar, LeanEditor, button, label, shortcut

STATUS_ICON = {"génération": "✍️", "compilation": "⚙️", "accepté": "✅", "refusé": "❌", "erreur": "⚠️", "annulé": "⏹"}
STAGE_KEYS = ["auto", "statement", "proof", "explanation"]


def stage_labels() -> list[str]:
    return [_("Auto"), _("Énoncé"), _("Preuve"), _("Explication")]


def lean4web_url() -> str | None:
    try:
        with socket.create_connection(("127.0.0.1", 8890), timeout=0.2):
            return "http://127.0.0.1:8890"
    except OSError:
        return None


def _md_to_html(md: str) -> str:
    doc = QTextDocument()
    doc.setMarkdown(texio.display_markdown(md))
    body = doc.toHtml()
    i, j = body.find("<body"), body.rfind("</body>")
    return body[body.find(">", i) + 1: j] if i >= 0 and j > i else html.escape(md)


class _NoAttempts:
    attempts: list = []
    n = 0


class LeanPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.pipe = ctx.pipeline
        self.file_path = ""
        self._active = _NoAttempts()          # service whose attempts are shown in « Essais »
        self._live_index = -1
        self._buf: list[str] = []
        self._expl_live = ""
        self._editor_from_dossier = ""        # last statement text written by the dossier (detects manual edits)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 10)
        lay.setSpacing(8)

        # ---------------- top bar: dossiers
        top = QHBoxLayout()
        top.addWidget(label(_("Dossier :"), "H2"))
        self.dossier_combo = QComboBox()
        self.dossier_combo.setMinimumWidth(320)
        self.dossier_combo.setToolTip(_("Chaque preuve est un dossier : son fil de discussion et toutes ses versions "
                                        "sont conservés. Choisissez-en un pour le rouvrir."))
        self.dossier_combo.activated.connect(self._combo_open)
        top.addWidget(self.dossier_combo, 1)
        self.new_btn = button(_("＋ Nouveau"), "Primary", _("Commencer un nouveau problème (Ctrl+N)"), self.new_dossier)
        self.rename_btn = button("✎", tip=_("Renommer ce dossier"), slot=self.rename_dossier)
        self.delete_btn = button("🗑", "Danger", _("Supprimer ce dossier (annulable)"), self.delete_dossier)
        for b in (self.new_btn, self.rename_btn, self.delete_btn):
            top.addWidget(b)
        top.addSpacing(12)
        self.options_btn = button(_("⚙ Options"), tip=_("Version de Lean, nombre d'essais, pause après la traduction"))
        self.options_btn.setCheckable(True)
        self.options_btn.toggled.connect(lambda on: self.options_box.setVisible(on))
        self.stop_btn = button(_("⏹ Arrêter"), "Danger", _("Arrêter l'opération en cours (Échap)"), self.stop)
        self.stop_btn.setEnabled(False)
        top.addWidget(self.options_btn)
        top.addWidget(self.stop_btn)
        lay.addLayout(top)

        self.options_box = QWidget()
        ob = QHBoxLayout(self.options_box)
        ob.setContentsMargins(0, 0, 0, 0)
        ob.addWidget(label(_("Espace Lean :")))
        self.ws_combo = QComboBox()
        self.ws_combo.setMinimumWidth(280)
        self.ws_combo.setToolTip(_("Version de Lean et de Mathlib utilisée pour vérifier.\n"
                                   "« Prouveur (Lean 4.9) » donne les meilleurs résultats avec Goedel-Prover."))
        self.ws_combo.currentIndexChanged.connect(self._ws_changed)
        ob.addWidget(self.ws_combo, 1)
        ob.addWidget(label(_("Essais :")))
        self.tries = QSpinBox()
        self.tries.setRange(1, 32)
        self.tries.setValue(ctx.settings.prove_attempts)
        self.tries.setToolTip(_("Nombre maximal d'essais : après chaque échec, les erreurs de Lean sont renvoyées à "
                                "l'IA pour qu'elle corrige sa preuve."))
        self.tries.valueChanged.connect(self._tries_changed)
        ob.addWidget(self.tries)
        self.pause_box = QCheckBox(_("Pause pour relire l'énoncé"))
        self.pause_box.setChecked(ctx.settings.pause_after_translation)
        self.pause_box.setToolTip(_("Désactivé : traduction, preuve et explication s'enchaînent sans arrêt.\n"
                                    "Activé : l'outil s'arrête après la traduction pour que vous relisiez l'énoncé."))
        self.pause_box.toggled.connect(self._pause_changed)
        ob.addWidget(self.pause_box)
        self.options_box.hide()
        lay.addWidget(self.options_box)

        self.busy = BusyBar()
        self.busy.cancelled.connect(self.stop)
        lay.addWidget(self.busy)

        split = QSplitter(Qt.Horizontal)
        # ---------------- left: thread
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.status_title = label("", "H2", wrap=True)
        self.status_detail = label("", "Muted", wrap=True)
        ll.addWidget(self.status_title)
        ll.addWidget(self.status_detail)
        self.thread = QTextBrowser()
        self.thread.setOpenLinks(False)
        self.thread.anchorClicked.connect(self._anchor)
        ll.addWidget(self.thread, 1)
        self.quick = QWidget()
        qk = QHBoxLayout(self.quick)
        qk.setContentsMargins(0, 0, 0, 0)
        self.quick_btns = []
        for text, stage, tip in [
            (_("Corriger l'énoncé…"), "statement", _("Décrivez ce qui ne va pas dans l'énoncé : l'IA le retraduit puis refait la preuve")),
            (_("Preuve plus simple"), "proof", _("Demander une preuve plus courte et plus lisible")),
            (_("Autre méthode"), "proof", _("Demander une preuve par une autre méthode")),
            (_("Expliquer plus en détail"), "explanation", _("Demander une explication plus détaillée")),
        ]:
            b = button(text, tip=tip, slot=lambda _c=False, t=text, s=stage: self._quick(t, s))
            self.quick_btns.append(b)
            qk.addWidget(b)
        qk.addStretch(1)
        ll.addWidget(self.quick)
        self.input = QPlainTextEdit()
        self.input.setMaximumHeight(92)
        self.input.setMinimumHeight(60)
        ll.addWidget(self.input)
        row = QHBoxLayout()
        self.ex_btn = button(_("Exemples ▾"), tip=_("Lancer un exercice d'exemple"))
        menu = QMenu(self)
        for ex in EXAMPLES:
            menu.addAction(f"{_(ex.title)}  ({_(ex.level)})", lambda e=ex: self.start_example(e))
        menu.addSeparator()
        menu.addAction(_("Fichier Lean à vérifier (exemple)"), lambda: self._load_text_for_check(VERIFY_SAMPLE, _("Exemple à vérifier")))
        self.ex_btn.setMenu(menu)
        self.tex_btn = button(_("📂 Importer ▾"), tip=_("Prendre un énoncé dans un fichier LaTeX, ou faire vérifier un "
                              "fichier .lean (vous pouvez aussi glisser le fichier sur la fenêtre)"))
        im = QMenu(self)
        im.addAction(_("Un théorème d'un fichier .tex…"), self.import_tex)
        im.addAction(_("Un fichier .lean à vérifier… (Ctrl+O)"), self.open_file)
        self.tex_btn.setMenu(im)
        self.open_btn = self.tex_btn
        self.stage_combo = QComboBox()
        self.stage_combo.addItems(stage_labels())
        self.stage_combo.setToolTip(_("Que doit modifier votre demande ? « Auto » : l'outil devine (énoncé, preuve ou "
                                      "explication) d'après vos mots."))
        self.send_btn = button(_("Envoyer"), "Primary", _("Envoyer (Ctrl+Entrée)"), self.send)
        for w in (self.ex_btn, self.tex_btn):
            row.addWidget(w)
        row.addStretch(1)
        self.stage_label = label(_("Modifier :"), "Muted")
        row.addWidget(self.stage_label)
        row.addWidget(self.stage_combo)
        row.addWidget(self.send_btn)
        ll.addLayout(row)
        split.addWidget(left)

        # ---------------- right: artefacts
        self.tabs = QTabWidget()
        st = QWidget()
        sl = QVBoxLayout(st)
        sl.setContentsMargins(6, 6, 6, 6)
        self.stmt_info = label("", "Muted", wrap=True)
        sl.addWidget(self.stmt_info)
        self.editor = LeanEditor()
        self.editor.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.editor.setPlaceholderText(_("L'énoncé Lean apparaîtra ici après la traduction. Vous pouvez aussi l'écrire "
                                         "vous-même :\n\ntheorem exemple (a b : ℝ) : a + b = b + a := by sorry\n\n"
                                         "Astuce : tapez \\R puis espace pour obtenir ℝ, \\le pour ≤, \\to pour →."))
        sl.addWidget(self.editor, 1)
        er = QHBoxLayout()
        self.verify_btn = button(_("✔ Vérifier"), tip=_("Faire vérifier ce texte par Lean (F5)"), slot=self.verify)
        self.prove_btn = button(_("✨ Prouver cet énoncé"), "Primary",
                                _("L'IA cherche une preuve de cet énoncé, puis l'explique (Ctrl+Maj+Entrée)"), self.prove)
        self.save_btn = button(_("Enregistrer…"), tip=_("Enregistrer ce texte dans un fichier .lean (Ctrl+S)"),
                               slot=self.save_file)
        er.addWidget(self.verify_btn)
        er.addWidget(self.prove_btn)
        er.addStretch(1)
        er.addWidget(self.save_btn)
        sl.addLayout(er)
        hint = label(_("Astuce : \\R → ℝ, \\N → ℕ, \\le → ≤, \\to → →, \\forall → ∀ (tapez puis Espace)."), "Muted")
        hint.setStyleSheet("font-size: 9pt;")
        sl.addWidget(hint)
        self.tabs.addTab(st, _("Énoncé Lean"))

        pr = QWidget()
        pl = QVBoxLayout(pr)
        pl.setContentsMargins(6, 6, 6, 6)
        self.proof_info = label("", "Muted", wrap=True)
        pl.addWidget(self.proof_info)
        self.proof_view = LeanEditor(read_only=True)
        self.proof_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.proof_view.setPlaceholderText(_("Aucune preuve pour l'instant."))
        pl.addWidget(self.proof_view, 1)
        prr = QHBoxLayout()
        self.copy_btn = button(_("📋 Copier"), "Primary", _("Copier la preuve dans le presse-papiers"), self.copy_final)
        self.save_proof_btn = button(_("💾 Enregistrer…"), tip=_("Enregistrer la preuve dans un fichier .lean"),
                                     slot=self.save_final)
        self.tex_out_btn = button(_("📄 LaTeX ▾"), tip=_("Exporter énoncé, preuve et explication en LaTeX (compatible Overleaf)"))
        tm = QMenu(self)
        tm.addAction(_("Enregistrer un fichier .tex…"), self.export_tex)
        tm.addAction(_("Copier le code LaTeX"), self.copy_tex)
        tm.addAction(_("Ouvrir Overleaf (navigateur)"), self.open_overleaf)
        self.tex_out_btn.setMenu(tm)
        self.l4w_btn = button("Lean4Web ↗", tip=_("Ouvrir la preuve dans votre Lean4Web local (navigateur).\n"
                              "Attention : Lean4Web utilise une autre version de Lean."), slot=self.open_lean4web)
        for b in (self.copy_btn, self.save_proof_btn, self.tex_out_btn, self.l4w_btn):
            prr.addWidget(b)
        prr.addStretch(1)
        pl.addLayout(prr)
        self.tabs.addTab(pr, _("Preuve"))

        ex = QWidget()
        xl = QVBoxLayout(ex)
        xl.setContentsMargins(6, 6, 6, 6)
        self.explain_view = QTextBrowser()
        self.explain_view.setPlaceholderText(_("L'explication en langage courant apparaîtra ici après la preuve."))
        xl.addWidget(self.explain_view, 1)
        xr = QHBoxLayout()
        self.explain_btn = button(_("💬 Expliquer"), tip=_("Une IA explique la preuve en langage courant (≈ 20 s)"),
                                  slot=self.explain)
        xr.addWidget(self.explain_btn)
        xr.addWidget(button(_("Copier l'explication"), tip=_("Copier le texte de l'explication"), slot=self.copy_explanation))
        xr.addStretch(1)
        xl.addLayout(xr)
        self.tabs.addTab(ex, _("Explication"))

        at = QWidget()
        al = QVBoxLayout(at)
        al.setContentsMargins(6, 6, 6, 6)
        self.attempts = QListWidget()
        self.attempts.setMaximumHeight(120)
        self.attempts.setWordWrap(True)
        self.attempts.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.attempts.setToolTip(_("Chaque essai de l'IA. Cliquez pour voir sa réflexion et le code testé."))
        self.attempts.currentRowChanged.connect(self._show_attempt)
        self.attempts.itemActivated.connect(self._goto_line)
        al.addWidget(self.attempts)
        self.reasoning = QPlainTextEdit()
        self.reasoning.setReadOnly(True)
        self.reasoning.setFont(theme.mono_font(10))
        self.reasoning.setMaximumBlockCount(20000)
        self.reasoning.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.reasoning.setPlaceholderText(_("La réflexion de l'IA (souvent en anglais) s'affiche ici en direct."))
        al.addWidget(self.reasoning, 1)
        self.tabs.addTab(at, _("Essais"))

        self.errors_view = QPlainTextEdit()
        self.errors_view.setReadOnly(True)
        self.errors_view.setFont(theme.mono_font(10))
        self.errors_view.setPlaceholderText(_("Les messages de Lean (erreurs, avertissements) apparaîtront ici."))
        self.tabs.addTab(self.errors_view, _("Messages Lean"))
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 6)
        lay.addWidget(split, 1)

        # ---------------- shortcuts, timers, signals
        shortcut(self, "Ctrl+Return", self.send)
        shortcut(self, "Ctrl+Shift+Return", self.prove)
        shortcut(self, "F5", self.verify)
        shortcut(self, "Escape", self.stop)
        shortcut(self, "Ctrl+S", self.save_file)
        shortcut(self, "Ctrl+O", self.open_file)
        shortcut(self, "Ctrl+N", self.new_dossier)
        self._flush = QTimer(self)
        self._flush.setInterval(80)
        self._flush.timeout.connect(self._flush_tokens)
        for svc in (ctx.prover, ctx.formalizer):
            svc.attemptStarted.connect(self._attempt_started)
            svc.token.connect(self._token)
            svc.attemptUpdated.connect(self._attempt_updated)
        ctx.explainer.token.connect(self._explain_token)
        ctx.verifier.finished.connect(self._verified)
        ctx.workspacesChanged.connect(self._fill_ws)
        ctx.translateRequest.connect(self.start_problem)
        ctx.proveRequest.connect(self.start_statement)
        ctx.texRequest.connect(self.load_tex)
        self.pipe.changed.connect(self.refresh)
        self.pipe.listChanged.connect(self.refresh_list)
        self.pipe.stageChanged.connect(self._stage)
        self.pipe.loading.connect(self._loading)
        self.editor.textChanged.connect(self._editor_changed)
        self._fill_ws()
        did = ctx.session.get("dossier")
        if did:
            self.pipe.open(did)
        self.refresh_list()
        self.refresh()

    # ================================================================ state helpers
    def _running(self) -> bool:
        return self.pipe.busy or self.ctx.verifier.busy

    def _set_running(self, on: bool):
        for w in (self.send_btn, self.prove_btn, self.verify_btn, self.explain_btn, self.ws_combo, self.new_btn,
                  self.delete_btn, self.dossier_combo, self.tex_btn, self.open_btn, self.ex_btn, *self.quick_btns):
            w.setEnabled(not on)
        self.stop_btn.setEnabled(on)
        if not on:
            self.busy.stop()
            self._flush.stop()
            self._flush_tokens()

    def _status(self, title: str, detail: str = "", color: str | None = None):
        self.status_title.setText(title)
        self.status_title.setStyleSheet(f"color: {color};" if color else "")
        self.status_detail.setText(detail)

    def _need_ws(self):
        ws = self.ctx.workspace()
        if ws is None or ws.check():
            self.ctx.banner.emit(friendly("workspace"), "\n".join(ws.problems) if ws else _("Aucun espace sélectionné."))
            return None
        return ws

    def _fill_ws(self):
        self.ws_combo.blockSignals(True)
        self.ws_combo.clear()
        for w in self.ctx.workspaces:
            self.ws_combo.addItem(_(w.label) + ("" if not w.problems else _("  — indisponible")), w.key)
            i = self.ws_combo.count() - 1
            self.ws_combo.setItemData(i, _(w.description) + ("\n\n⚠️ " + " ".join(w.problems) if w.problems else ""),
                                      Qt.ToolTipRole)
            if w.problems:
                self.ws_combo.model().item(i).setEnabled(False)
            if w.key == self.ctx.settings.workspace:
                self.ws_combo.setCurrentIndex(i)
        self.ws_combo.blockSignals(False)

    def _ws_changed(self, i: int):
        key = self.ws_combo.itemData(i)
        if key:
            self.ctx.settings.workspace = key
            self.ctx.save_later()

    def _tries_changed(self, v: int):
        self.ctx.settings.prove_attempts = v
        self.ctx.save_later()

    def _pause_changed(self, on: bool):
        self.ctx.settings.pause_after_translation = on
        self.ctx.save_later()

    def _editor_changed(self):
        if self.editor.extraSelections():
            self.editor.set_error_lines({})

    # ================================================================ dossiers list + thread rendering
    def refresh_list(self):
        self.dossier_combo.blockSignals(True)
        self.dossier_combo.clear()
        cur = self.pipe.dossier
        if cur is None:
            self.dossier_combo.addItem(_("(nouveau problème)"), "")
        for d in self.pipe.store.list():
            when = _dt.datetime.fromtimestamp(d.updated).strftime("%d/%m %H:%M")
            mark = "✅ " if d.proof else ("✍️ " if d.statement else "")
            self.dossier_combo.addItem(f"{mark}{d.title}   ·   {when}", d.id)
            if cur and d.id == cur.id:
                self.dossier_combo.setCurrentIndex(self.dossier_combo.count() - 1)
        self.dossier_combo.blockSignals(False)

    def _combo_open(self, i: int):
        did = self.dossier_combo.itemData(i)
        if did and (not self.pipe.dossier or did != self.pipe.dossier.id):
            if not self.pipe.open(did):
                self.ctx.toast.emit(_("Une opération est en cours : arrêtez-la d'abord (Échap)."), None, None)
                self.refresh_list()

    def refresh(self):
        d = self.pipe.dossier
        self.thread.setHtml(self._thread_html())
        sb = self.thread.verticalScrollBar()
        QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))
        has_stmt = bool(d and d.statement)
        has_proof = bool(d and d.proof_is_current)
        self.quick.setVisible(has_stmt)
        for b, need_proof in zip(self.quick_btns, (False, True, True, True)):
            b.setVisible(has_proof or not need_proof)
        self.rename_btn.setEnabled(d is not None)
        self.delete_btn.setEnabled(d is not None and not self.pipe.busy)
        self.input.setPlaceholderText(
            _("Écrivez votre problème avec vos mots (français ou anglais, LaTeX accepté).\nExemple : « Montrer que la "
              "somme de deux entiers pairs est paire. »") if not has_stmt else
            _("Une précision, une correction ? Par exemple : « ajoute l'hypothèse n > 0 », « preuve plus courte », "
              "« explique l'étape 2 »."))
        self.send_btn.setText(_("✨ Prouver") if not has_stmt else _("Envoyer"))
        self.stage_combo.setVisible(has_stmt)
        self.stage_label.setVisible(has_stmt)
        if d is None:
            if not self.pipe.busy:
                self._status(_("Nouveau problème"), _("Décrivez-le en bas, puis cliquez sur « Prouver » : l'IA le traduit "
                                                      "en Lean, cherche une preuve, et l'explique."))
            return
        # artefacts
        if d.statement and d.statement != self._editor_from_dossier:
            self._editor_from_dossier = d.statement
            self.editor.set_text_undoable(d.statement)
        if d.statements:
            v = d.statements[d.cur_statement]
            src = _("écrit par vous") if v.source == "user" else _("traduit par l'IA")
            ok = _("Lean l'accepte") if v.ok else _("Lean le refuse")
            self.stmt_info.setText(_("Version {n} sur {t} · {src} · {ok}. Relisez-le : la preuve porte exactement sur "
                                     "ce texte.").format(n=d.cur_statement + 1, t=len(d.statements), src=src, ok=ok))
        else:
            self.stmt_info.setText(_("Pas encore d'énoncé."))
        if d.proof:
            self.proof_view.setPlainText(d.proof)
            note = "" if d.proof_is_current else _(" ⚠️ Cette preuve correspond à une ancienne version de l'énoncé.")
            self.proof_info.setText(_("Preuve {n} sur {t}, vérifiée par Lean (sans « sorry », axiomes standard).").format(
                n=d.cur_proof + 1, t=len(d.proofs)) + note)
        else:
            self.proof_view.clear()
            self.proof_info.setText("")
        if d.explanation and not self.pipe.stage == "explanation":
            self.explain_view.setMarkdown(texio.display_markdown(d.explanation))
        elif not d.explanation and not self.pipe.stage == "explanation":
            self.explain_view.clear()
        if not self.pipe.busy:
            self._idle_status()

    def _idle_status(self):
        d = self.pipe.dossier
        if not d:
            return
        last = d.events[-1] if d.events else None
        if d.proof_is_current and d.explanation:
            self._status(_("✅ Prouvé et expliqué"), _("Demandez une précision ci-dessous, ou exportez (onglet « Preuve »)."), theme.OK)
        elif d.proof_is_current:
            self._status(_("✅ Prouvé"), _("Lean a vérifié la preuve. Cliquez sur « 💬 Expliquer » pour une explication."), theme.OK)
        elif last and last.kind == "error":
            self._status(_("⚠️ À vous de jouer"), last.text, theme.WARN)
        elif d.statement:
            self._status(_("Énoncé prêt"), _("Relisez-le dans l'onglet « Énoncé Lean », puis cliquez sur « Prouver cet énoncé »."))
        else:
            self._status(_("Dossier vide"), _("Décrivez votre problème en bas."))

    def _thread_html(self) -> str:
        d = self.pipe.dossier
        t, m = theme.TEXT, theme.MUTED
        if d is None or not d.events:
            ex = "".join(f"<li>{html.escape(_(e.blurb))}</li>" for e in EXAMPLES[1:5])
            return (f"<div style='color:{m};padding:16px'><p style='font-size:13pt;color:{t}'><b>{_('Comment ça marche')}</b></p>"
                    f"<p>① {_('Écrivez votre problème en bas, avec vos mots.')}<br>"
                    f"② {_('L’IA le traduit en Lean, cherche une preuve que Lean vérifie, puis l’explique.')}<br>"
                    f"③ {_('Vous pouvez ensuite demander des corrections ou des précisions : tout reste dans ce dossier.')}</p>"
                    f"<p>{_('Idées :')}</p><ul>{ex}</ul></div>")
        rows = []
        for e in d.events:
            when = _dt.datetime.fromtimestamp(e.t).strftime("%H:%M")
            text = html.escape(e.text).replace("\n", "<br>")
            if e.kind == "user":
                tag = {"statement": _("énoncé"), "proof": _("preuve"), "explanation": _("explication")}.get(e.stage, "")
                tag = f" <span style='color:{m}'>· {tag}</span>" if tag else ""
                rows.append(f"<table cellpadding='9' cellspacing='0' style='margin:4px 0 4px 40px;background-color:#22304D'>"
                            f"<tr><td><b>{_('Vous')}</b> <span style='color:{m}'>{when}</span>{tag}<br>{text}</td></tr></table>")
                continue
            color = {"statement": "#1D2A40", "proof": "#16301F", "explanation": "#2A2440", "error": "#3A1D20",
                     "info": theme.PANEL}.get(e.kind, theme.PANEL)
            icon = {"statement": "∀", "proof": "✅", "explanation": "💬", "error": "⚠️", "info": "ℹ️"}.get(e.kind, "•")
            extra = ""
            if e.kind == "statement" and e.ref is not None and e.ref < len(d.statements):
                code = d.statements[e.ref].code
                sig = code[code.rfind("theorem"):] if "theorem" in code else code
                extra = (f"<pre style='font-family:monospace;color:{t};white-space:pre-wrap'>{html.escape(sig.strip())}</pre>"
                         + self._version_links("statement", e.ref, d.cur_statement))
            elif e.kind == "proof" and e.ref is not None and e.ref < len(d.proofs):
                n = d.proofs[e.ref].code.count("\n")
                extra = (f"<span style='color:{m}'>{_('{n} lignes de Lean').format(n=n)} · </span>"
                         + self._version_links("proof", e.ref, d.cur_proof))
            elif e.kind == "explanation" and e.ref is not None and e.ref < len(d.explanations):
                full = d.explanations[e.ref].code
                plain = re.sub(r"\*\*|__|`|^#+\s*", "", full, flags=re.M)
                short = plain if len(plain) <= 280 else plain[:280].rsplit(" ", 1)[0] + " …"
                extra = (f"<div style='color:{m}'>{html.escape(texio.display_markdown(short)).replace(chr(10), '<br>')}</div>"
                         + self._version_links("explanation", e.ref, d.cur_explanation))
            rows.append(f"<table cellpadding='9' cellspacing='0' style='margin:4px 0 4px 0;background-color:{color}'>"
                        f"<tr><td><b>{icon}</b> <span style='color:{m}'>{when}</span><br>{text}{extra}</td></tr></table>")
        return "".join(rows)

    def _version_links(self, kind: str, idx: int, cur: int) -> str:
        view = f"<a href='show:{kind}:{idx}' style='color:{theme.ACCENT_H}'>{_('Voir')}</a>"
        if idx == cur:
            return f"<br>{view} · <span style='color:{theme.OK}'>{_('version actuelle')}</span>"
        return (f"<br>{view} · <a href='restore:{kind}:{idx}' style='color:{theme.ACCENT_H}'>"
                f"{_('Revenir à cette version')}</a>")

    def _anchor(self, url: QUrl):
        parts = url.toString().split(":")
        if len(parts) != 3:
            return
        action, kind, idx = parts[0], parts[1], int(parts[2])
        tab = {"statement": 0, "proof": 1, "explanation": 2}[kind]
        if action == "restore":
            if self._running():
                self.ctx.toast.emit(_("Une opération est en cours : arrêtez-la d'abord (Échap)."), None, None)
                return
            self.pipe.restore(kind, idx)
        elif action == "show":
            d = self.pipe.dossier
            if kind == "statement":
                self.editor.set_text_undoable(d.statements[idx].code)
            elif kind == "proof":
                self.proof_view.setPlainText(d.proofs[idx].code)
            elif kind == "explanation":
                self.explain_view.setMarkdown(texio.display_markdown(d.explanations[idx].code))
        self.tabs.setCurrentIndex(tab)

    # ================================================================ dossier actions
    def new_dossier(self):
        if self._running():
            return
        self.pipe.dossier = None
        self.ctx.session.pop("dossier", None)
        self._editor_from_dossier = ""
        self.editor.clear()
        self.file_path = ""
        self.attempts.clear()
        self.reasoning.clear()
        self.errors_view.clear()
        self.refresh_list()
        self.refresh()
        self.input.setFocus()

    def rename_dossier(self):
        d = self.pipe.dossier
        if not d:
            return
        title, ok = QInputDialog.getText(self, _("Renommer le dossier"), _("Nouveau titre :"), text=d.title)
        if ok:
            self.pipe.rename(title)

    def delete_dossier(self):
        did = self.pipe.delete_current()
        if not did:
            return
        self.new_dossier()
        self.ctx.toast.emit(_("Dossier supprimé."), lambda: self.pipe.undelete(did), None)

    # ================================================================ sending requests
    def send(self):
        if self._running():
            return
        text = self.input.toPlainText().strip()
        if not text:
            self.input.setFocus()
            return
        if self._need_ws() is None:
            return
        d = self.pipe.dossier
        self.input.clear()
        if d is None or not d.statement:
            self.pipe.start(text) if d is None else self.pipe.request(text)
        else:
            self.pipe.request(text, STAGE_KEYS[self.stage_combo.currentIndex()])
        self.stage_combo.setCurrentIndex(0)

    def _quick(self, text: str, stage: str):
        if stage == "statement":
            self.stage_combo.setCurrentIndex(1)
            self.input.setPlainText(_("L'énoncé est faux : "))
            self.input.setFocus()
            self.input.moveCursor(QTextCursor.End)
            return
        if self._running():
            return
        self.pipe.request(text, stage)

    def start_problem(self, text: str):
        """Home « Prouver un théorème »: new dossier, fully automatic chain."""
        self.ctx.navigate.emit("lean")
        if self._running():
            self.ctx.toast.emit(_("Une opération est en cours : arrêtez-la d'abord (Échap)."), None, None)
            return
        if self._need_ws() is None:
            return
        self.pipe.start(text)

    def start_statement(self, statement: str, problem: str = ""):
        """Home examples: new dossier with a ready statement → prove → explain."""
        self.ctx.navigate.emit("lean")
        if self._running():
            self.ctx.toast.emit(_("Une recherche est déjà en cours : arrêtez-la d'abord (Échap)."), None, None)
            return
        if self._need_ws() is None:
            return
        self.pipe.start_with_statement(statement, problem)

    def start_example(self, ex):
        self.start_statement(ex.statement, example_text(ex))

    def prove(self):
        if self._running():
            return
        code = self.editor.toPlainText()
        try:
            leancheck.theorem_name(leancheck.prepare_statement(code))
        except leancheck.StatementError as e:
            self.ctx.banner.emit(Friendly(_("Énoncé incomplet"), _(str(e)) + "\n" + _("Exemple : ") +
                                          "theorem t (a : ℕ) : a + 0 = a := by sorry", [], "warn"), "")
            return
        if self._need_ws() is None:
            return
        d = self.pipe.dossier
        if d is None:
            self.pipe.start_with_statement(code, "", title=Path(self.file_path).stem if self.file_path else "")
            return
        if code.strip() != d.statement.strip():
            self.pipe.set_statement(code)
        self.pipe.prove_current()

    def explain(self):
        if self._running():
            return
        d = self.pipe.dossier
        if not d or not d.proof:
            self._status(_("Il n'y a pas encore de preuve à expliquer"),
                         _("Lancez d'abord une preuve (« Prouver »)."), theme.WARN)
            return
        self.pipe.explain_current()

    def stop(self):
        if self.pipe.busy:
            self.pipe.cancel()
        elif self.ctx.verifier.busy:
            self.ctx.verifier.cancel()

    # ================================================================ pipeline feedback
    def _stage(self, stage: str):
        if not stage:
            self._set_running(False)
            self.refresh()
            return
        self._set_running(True)
        self._flush.start()
        texts = {"statement": (_("① Traduction en Lean…"), _("L'IA écrit l'énoncé en Lean, puis Lean contrôle qu'il est valide.")),
                 "proof": (_("② Recherche de preuve…"), _("L'IA écrit une preuve ; si Lean la refuse, elle corrige et réessaie.")),
                 "explanation": (_("③ Explication…"), _("L'IA explique la preuve en langage courant."))}[stage]
        self._status(*texts)
        self.busy.start(texts[0])
        if stage == "explanation":
            self._expl_live = ""
            self.explain_view.clear()
            self.tabs.setCurrentIndex(2)
        else:
            self._active = self.ctx.formalizer if stage == "statement" else self.ctx.prover
            self.attempts.clear()
            self.reasoning.clear()
            self.tabs.setCurrentIndex(3)

    def _loading(self, stage: str):
        name = {"statement": _("du traducteur"), "proof": _("du prouveur"), "explanation": _("de l'explicateur")}[stage]
        self.busy.start(_("Chargement {name} en mémoire graphique… (environ 5 à 30 s)").format(name=name))

    def _attempt_started(self, i: int):
        if self.sender() is not self._active:
            return
        a = self._active.attempts[i]
        it = QListWidgetItem()
        self.attempts.addItem(it)
        self._render_item(i)
        self._live_index = i
        self.attempts.setCurrentRow(i)
        self.reasoning.clear()
        n = self._active.n
        what = {"translation": _("traduit votre problème"), "initial": _("écrit une preuve"),
                "refinement": _("réécrit la preuve selon votre demande")}.get(a.kind, _("écrit une correction"))
        self.busy.start(_("Essai {i}/{n} : l'IA {what}…").format(i=i + 1, n=n, what=what), maximum=n)
        self.busy.progress(i, n)

    def _render_item(self, i: int):
        attempts = self._active.attempts
        if not (0 <= i < len(attempts)) or self.attempts.item(i) is None:
            return
        a = attempts[i]
        extra = []
        if a.tokens:
            extra.append(_("{n} tokens, {s:.0f} tok/s").format(n=a.tokens, s=a.tps))
        if a.compile_seconds:
            extra.append(_("Lean {s:.0f} s").format(s=a.compile_seconds))
        kind = {"initial": _("1re tentative"), "translation": _("traduction"), "refinement": _("selon votre demande")}.get(
            a.kind, _("correction"))
        self.attempts.item(i).setText(f"{STATUS_ICON.get(a.status, '•')} " + _("Essai {i} ({kind}) — {s}").format(
            i=i + 1, kind=kind, s=_(a.summary) if a.summary else _(a.status)) + (f"   [{', '.join(extra)}]" if extra else ""))

    def _token(self, i: int, t: str):
        if self.sender() is self._active and i == self._live_index and self.attempts.currentRow() == i:
            self._buf.append(t)

    def _explain_token(self, t: str):
        self._expl_live += t

    def _flush_tokens(self):
        if self._buf:
            text, self._buf = "".join(self._buf), []
            sb = self.reasoning.verticalScrollBar()
            at_end = sb.value() >= sb.maximum() - 4
            c = self.reasoning.textCursor()
            c.movePosition(QTextCursor.End)
            c.insertText(text)
            if at_end:
                sb.setValue(sb.maximum())
        if self.pipe.stage == "explanation" and self._expl_live:
            txt = leancheck.clean_model_text(self._expl_live) if ("</think>" in self._expl_live or
                                                                  "<think>" not in self._expl_live) else ""
            if txt:
                self.explain_view.setMarkdown(texio.display_markdown(txt))
                self.explain_view.verticalScrollBar().setValue(self.explain_view.verticalScrollBar().maximum())

    def _attempt_updated(self, i: int):
        if self.sender() is not self._active:
            return
        self._render_item(i)
        a = self._active.attempts[i] if i < len(self._active.attempts) else None
        if a and a.status == "compilation":
            what = _("l'énoncé") if a.kind == "translation" else _("la preuve")
            self.busy.start(_("Essai {i}/{n} : Lean vérifie {what}…").format(i=i + 1, n=self._active.n, what=what),
                            maximum=self._active.n)
            self.busy.progress(i, self._active.n)
        if self.attempts.currentRow() == i:
            self._show_attempt(i)

    def _show_attempt(self, i: int):
        attempts = self._active.attempts
        if not (0 <= i < len(attempts)):
            return
        a = attempts[i]
        self._buf = []
        self.reasoning.setPlainText(a.raw)
        self.reasoning.verticalScrollBar().setValue(self.reasoning.verticalScrollBar().maximum())
        self.errors_view.setPlainText(a.errors_text or _(a.summary) or "")

    # ================================================================ verify a file / statement
    def verify(self):
        if self._running():
            return
        ws = self._need_ws()
        if not ws:
            return
        code = self.editor.toPlainText()
        if not code.strip():
            self._status(_("L'éditeur est vide"), _("Écrivez du code Lean, ou ouvrez un fichier .lean."), theme.WARN)
            return
        self._active = _NoAttempts()
        self._set_running(True)
        self.busy.start(_("Lean vérifie le texte… (quelques secondes ; jusqu'à 1 minute la première fois)"))
        self._status(_("Vérification en cours…"), _("Espace : {ws}").format(ws=_(ws.label)))
        self.editor.set_error_lines({})
        self.ctx.verifier.compile(code, ws, self.ctx.settings.compile_timeout_s, None)

    def _verified(self, res: CompileResult):
        self._set_running(False)
        if res.cancelled:
            self._status(_("Vérification annulée"), "", theme.MUTED)
            return
        if res.infra_error and not res.verdict.errors:
            self._status(_("Lean n'a pas pu vérifier"), _(res.verdict.summary), theme.ERR)
            self.ctx.banner.emit(friendly("workspace"), res.infra_error)
            return
        v = res.verdict
        self.attempts.clear()
        lines, out = {}, []
        for m in leancheck.parse_lean_json(res.stdout):
            sev = {"error": _("❌ Erreur"), "warning": _("⚠️ Avertissement"), "information": _("ℹ️ Info")}.get(m.severity, m.severity)
            out.append(_("{sev} — ligne {l}, colonne {c} :").format(sev=sev, l=m.line, c=m.col + 1) + f"\n{m.text}\n")
            if m.severity == "error":
                lines[m.line] = m.text
            it = QListWidgetItem(_("{sev}, ligne {l} : {t}").format(sev=sev, l=m.line, t=m.text.splitlines()[0][:110] if m.text else ""))
            it.setData(Qt.UserRole, m.line)
            it.setToolTip(m.text)
            self.attempts.addItem(it)
        self.errors_view.setPlainText("\n".join(out) or _("Aucun message : tout est correct."))
        self.tabs.setCurrentIndex(4)
        self.editor.set_error_lines(lines)
        if v.ok:
            self._status("✅ " + _(v.summary), _("Vérifié en {s:.1f} s.").format(s=res.seconds), theme.OK)
        elif v.has_sorry and not v.errors:
            self._status(_("⚠️ Accepté, mais incomplet"), _("Le texte contient « sorry » : une preuve manque. "
                         "Cliquez sur « Prouver cet énoncé » pour que l'IA la cherche."), theme.WARN)
        elif res.timed_out:
            self._status("⏱ " + _(v.summary), _("Augmentez le délai dans « Système » ou simplifiez le fichier."), theme.WARN)
        else:
            self._status("❌ " + _(v.summary), _("Les lignes en rouge contiennent des erreurs. Double-cliquez sur un "
                         "message (onglet « Essais ») pour aller à la ligne."), theme.ERR)

    def _goto_line(self, item: QListWidgetItem):
        ln = item.data(Qt.UserRole)
        if isinstance(ln, int):
            self.tabs.setCurrentIndex(0)
            block = self.editor.document().findBlockByNumber(ln - 1)
            c = self.editor.textCursor()
            c.setPosition(block.position())
            self.editor.setTextCursor(c)
            self.editor.setFocus()

    # ================================================================ files
    def _load_text_for_check(self, text: str, title: str):
        if self._running():
            return
        self.new_dossier()
        self.pipe.new(title=title)
        self.editor.set_text_undoable(text)
        self.tabs.setCurrentIndex(0)
        self.verify()

    def open_file(self):
        start = self.file_path or self.ctx.session.get("last_dir", str(Path.home()))
        path, _f = QFileDialog.getOpenFileName(self, _("Ouvrir un fichier Lean"), start, _("Fichiers Lean (*.lean);;Tous (*)"))
        if path:
            self.load_file(path)

    def load_file(self, path: str, verify: bool = True):
        """Open a .lean file in a new dossier and (by default) verify it immediately."""
        self.ctx.navigate.emit("lean")
        if self._running():
            self.ctx.toast.emit(_("Une opération est en cours : arrêtez-la d'abord (Échap)."), None, None)
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            self.ctx.banner.emit(Friendly(_("Impossible d'ouvrir le fichier"), _("Le fichier n'est pas lisible (droits "
                                          "ou encodage). Choisissez un autre fichier."), [], "warn"), str(e))
            return
        self.ctx.session["last_dir"] = str(Path(path).parent)
        self.new_dossier()
        self.pipe.new(title=Path(path).name)
        self.file_path = path
        self.editor.set_text_undoable(text)
        self.tabs.setCurrentIndex(0)
        self.ctx.toast.emit(_("Fichier ouvert : {name}").format(name=Path(path).name), None, None)
        if verify:
            self.verify()

    def _save_text(self, text: str, suggested: str, filt: str) -> str | None:
        start = self.ctx.session.get("last_dir", str(Path.home() / "Documents"))
        path, _f = QFileDialog.getSaveFileName(self, _("Enregistrer"), str(Path(start) / suggested), filt)
        if not path:
            return None
        ext = Path(suggested).suffix
        if not path.endswith(ext):
            path += ext
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as e:
            self.ctx.banner.emit(Friendly(_("Enregistrement impossible"), _("Choisissez un autre dossier."), [], "warn"), str(e))
            return None
        self.ctx.session["last_dir"] = str(Path(path).parent)
        self.ctx.toast.emit(_("Enregistré : {name}").format(name=Path(path).name), None, None)
        return path

    def _name(self) -> str:
        d = self.pipe.dossier
        return d.theorem if d and d.theorem else "preuve"

    def save_file(self):
        p = self._save_text(self.editor.toPlainText(), self._name() + ".lean", _("Fichiers Lean (*.lean)"))
        if p:
            self.file_path = p

    def save_final(self):
        if self.proof_view.toPlainText().strip():
            self._save_text(self.proof_view.toPlainText(), self._name() + ".lean", _("Fichiers Lean (*.lean)"))

    def copy_final(self):
        QGuiApplication.clipboard().setText(self.proof_view.toPlainText())
        self.ctx.toast.emit(_("Preuve copiée dans le presse-papiers."), None, None)

    def copy_explanation(self):
        d = self.pipe.dossier
        if not d or not d.explanation:
            self.ctx.toast.emit(_("Aucune explication à copier."), None, None)
            return
        QGuiApplication.clipboard().setText(texio.display_markdown(d.explanation))
        self.ctx.toast.emit(_("Explication copiée."), None, None)

    def open_lean4web(self):
        base = lean4web_url()
        if base:
            QDesktopServices.openUrl(QUrl(base + "/#code=" + urllib.parse.quote(self.proof_view.toPlainText())))
        else:
            self.ctx.toast.emit(_("Lean4Web ne répond pas sur cet ordinateur."), None, None)

    # ================================================================ LaTeX in / out
    def import_tex(self):
        start = self.ctx.session.get("last_dir", str(Path.home()))
        path, _f = QFileDialog.getOpenFileName(self, _("Importer un fichier LaTeX"), start, _("Fichiers LaTeX (*.tex);;Tous (*)"))
        if path:
            self.load_tex(path)

    def load_tex(self, path: str):
        """Read a .tex file, let the user pick a statement, and prepare a new dossier with it."""
        self.ctx.navigate.emit("lean")
        if self._running():
            self.ctx.toast.emit(_("Une opération est en cours : arrêtez-la d'abord (Échap)."), None, None)
            return
        try:
            tex = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self.ctx.banner.emit(Friendly(_("Impossible d'ouvrir le fichier"), _("Le fichier n'est pas lisible (droits)."),
                                          [], "warn"), str(e))
            return
        items = texio.extract_statements(tex)
        if not items:
            self.ctx.banner.emit(Friendly(_("Aucun énoncé trouvé"), _("Ce fichier .tex ne contient ni théorème, ni lemme, "
                                          "ni exercice, ni texte exploitable."), [], "warn"), "")
            return
        chosen = items[0]
        if len(items) > 1:
            labels = [f"{_(it.title)} : {it.body.replace(chr(10), ' ')[:70]}…" for it in items]
            pick, ok = QInputDialog.getItem(self, _("Choisir l'énoncé"), _("Ce fichier contient plusieurs énoncés.\n"
                                            "Lequel voulez-vous prouver ?"), labels, 0, False)
            if not ok:
                return
            chosen = items[labels.index(pick)]
        self.ctx.session["last_dir"] = str(Path(path).parent)
        self.new_dossier()
        self.input.setPlainText(chosen.body)
        self.ctx.toast.emit(_("Importé : {t}. Cliquez sur « Prouver ».").format(t=_(chosen.title)), None, None)

    def latex_source(self) -> str:
        d = self.pipe.dossier
        code = self.proof_view.toPlainText()
        stmt = ""
        if d and d.statement:
            stmt = d.statement[d.statement.rfind("theorem"):]
        ws = self.ctx.workspace()
        meta = f"{_(ws.label).split(' (')[0]}, Lean {ws.toolchain.split(':')[-1].lstrip('v')}" if ws else ""
        return texio.export_document(d.problem if d else "", stmt, code, self._name(), meta,
                                     explanation=d.explanation if d else "")

    def export_tex(self):
        if not self.proof_view.toPlainText().strip():
            self.ctx.toast.emit(_("Il n'y a pas encore de preuve à exporter."), None, None)
            return
        if self._save_text(self.latex_source(), self._name() + ".tex", _("Fichiers LaTeX (*.tex)")):
            self.ctx.toast.emit(_("Dans Overleaf : « Nouveau projet → Téléverser », compilateur XeLaTeX."), None, None)

    def copy_tex(self):
        QGuiApplication.clipboard().setText(self.latex_source())
        self.ctx.toast.emit(_("Code LaTeX copié : collez-le dans un fichier .tex d'Overleaf (compilateur XeLaTeX)."), None, None)

    def open_overleaf(self):
        QDesktopServices.openUrl(QUrl(self.ctx.settings.overleaf_url))
