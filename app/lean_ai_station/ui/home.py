"""Home: type your problem in plain language (or import a .tex) + one big action; one-click example exercises."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QPlainTextEdit, QScrollArea, QVBoxLayout,
                               QWidget)

from ..examples import EXAMPLES, example_text
from ..i18n import _
from . import theme
from .widgets import Card, button, label

LEVEL_COLORS = {"Initiation": "#3FB950", "Collège": "#58A6FF", "Lycée": "#A371F7", "Licence": "#E3B341",
                "Olympiade": "#F85149"}


class HomePage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(36, 28, 36, 28)
        lay.setSpacing(14)

        lay.addWidget(label(_("Lean AI Station"), "H1"))
        lay.addWidget(label(_("Vous décrivez un problème de mathématiques ; l'IA l'écrit dans le langage <b>Lean</b> puis "
                            "cherche une preuve ; <b>Lean</b>, un logiciel de vérification, contrôle chaque ligne. "
                            "Une preuve n'est jamais affichée comme « réussie » sans son feu vert.<br>"
                            "Rien n'est envoyé sur Internet : l'IA est un fichier installé chez vous, exécuté par la carte graphique de l'ordinateur (à défaut, par son processeur)."),
                            "Muted", wrap=True))

        card = Card(margins=18)
        card.lay.addWidget(label(_("Quel problème voulez-vous prouver ?"), "H2"))
        self.nl = QPlainTextEdit()
        self.nl.setMinimumHeight(90)
        self.nl.setMaximumHeight(130)
        self.nl.setPlaceholderText(_("Écrivez-le comme vous le diriez à un collègue, en français ou en anglais.\n"
                                   "Exemple : « Montrer que la somme de deux entiers pairs est paire. »\n"
                                   "Formules LaTeX acceptées : $a^2 + b^2 \\ge 2ab$."))
        card.lay.addWidget(self.nl)
        row = QHBoxLayout()
        self.big = button(_("✨  Prouver un théorème"), "Big",
                          _("L'IA traduit votre problème en Lean, cherche une preuve vérifiée par Lean, puis l'explique"),
                          slot=self._go)
        row.addWidget(self.big)
        col = QVBoxLayout()
        col.addWidget(button(_("📄 Importer un fichier .tex…"), tip=_("Prendre un théorème, un lemme ou un exercice dans un "
                             "document LaTeX (vous pouvez aussi glisser le .tex sur la fenêtre)"), slot=self._tex))
        col.addWidget(button(_("Je sais écrire du Lean"), "Link", _("Ouvrir directement l'éditeur Lean (Ctrl+2)"),
                             lambda: ctx.navigate.emit("lean")))
        row.addLayout(col)
        row.addStretch(1)
        card.lay.addLayout(row)
        self.status = label(obj="Muted", wrap=True)
        card.lay.addWidget(self.status)
        lay.addWidget(card)

        steps = QHBoxLayout()
        for n, (t, d) in enumerate([("Vous décrivez", "Avec vos mots, ou en important un .tex. Vous pouvez aussi écrire directement du Lean."),
                                    ("L'IA traduit en Lean", "Elle écrit l'énoncé officiel. Il reste affiché : vous pouvez le relire et demander une correction à tout moment."),
                                    ("L'IA prouve, Lean vérifie", "Si Lean refuse une preuve, l'IA corrige et réessaie. Ensuite : explication en langage courant, export LaTeX.")], 1):
            c = Card(margins=12)
            c.lay.addWidget(label(f"<b>{n}.  {_(t)}</b>"))
            c.lay.addWidget(label(_(d), "Muted", wrap=True))
            steps.addWidget(c)
        lay.addLayout(steps)

        lay.addSpacing(6)
        lay.addWidget(label(_("Ou essayez un exercice : un clic lance la recherche de preuve"), "H2"))
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, ex in enumerate(EXAMPLES):
            c = Card()
            c.setMinimumWidth(260)
            top = QHBoxLayout()
            t = label(_(ex.title), "H2", wrap=True)
            lvl = label(_(ex.level))
            col_ = LEVEL_COLORS.get(ex.level, theme.MUTED)
            lvl.setStyleSheet(f"color: {col_}; border: 1px solid {col_}; border-radius: 9px; padding: 1px 8px; font-size: 9pt;")
            lvl.setToolTip(_("Niveau indicatif de l'exercice"))
            top.addWidget(t, 1)
            top.addWidget(lvl, 0, Qt.AlignTop)
            c.lay.addLayout(top)
            c.lay.addWidget(label(_(ex.blurb), "Muted", wrap=True))
            c.lay.addStretch(1)
            b = button(_("▶  Prouver"), "Primary", _("Lance la recherche de preuve pour « {t} »").format(t=_(ex.title)),
                       slot=lambda _c=False, e=ex: ctx.proveRequest.emit(e.statement, example_text(e)))
            b.setAccessibleName(_("Prouver : {t}").format(t=_(ex.title)))
            c.lay.addWidget(b, 0, Qt.AlignRight)
            grid.addWidget(c, i // 3, i % 3)
        lay.addLayout(grid)
        lay.addStretch(1)
        ctx.server.stateChanged.connect(self.refresh)
        ctx.modelsChanged.connect(self.refresh)
        ctx.workspacesChanged.connect(self.refresh)
        self.refresh()

    def _go(self):
        text = self.nl.toPlainText().strip()
        if text:
            self.ctx.translateRequest.emit(text)
        else:
            self.nl.setFocus()
            self.status.setText(_("✍️ Écrivez d'abord votre problème dans la zone ci-dessus (ou importez un .tex)."))

    def _tex(self):
        start = self.ctx.session.get("last_dir", str(Path.home()))
        path, _f = QFileDialog.getOpenFileName(self, _("Importer un fichier LaTeX"), start, _("Fichiers LaTeX (*.tex);;Tous (*)"))
        if path:
            self.ctx.navigate.emit("lean")
            self.ctx.texRequest.emit(path)

    def refresh(self, *_a):
        st = self.ctx.server.state
        ws = self.ctx.workspace()
        model = self.ctx.default_model()
        parts = []
        if st == "ready":
            parts.append(_("🟢 Modèle prêt"))
        elif st == "starting":
            parts.append(_("🟡 Chargement du modèle…"))
        elif model:
            parts.append(_("⚪ Le modèle se chargera automatiquement au premier clic"))
        else:
            parts.append(_("⚠️ Aucun modèle installé (voir « Modèles »)"))
        if self.ctx.formalizer_model() is None:
            parts.append(_("⚠️ Traducteur français → Lean absent : vous pouvez écrire l'énoncé en Lean (voir « Aide »)"))
        parts.append(_("Espace Lean : {ws}").format(ws=_(ws.label)) if ws else _("⚠️ Aucun espace Lean prêt (voir « Système »)"))
        self.status.setText("   ·   ".join(parts))
