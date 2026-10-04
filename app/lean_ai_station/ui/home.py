"""Home: type your problem in plain language (or import a .tex) + one big action; one-click example exercises."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QPlainTextEdit, QScrollArea, QVBoxLayout,
                               QWidget)

from ..examples import EXAMPLES
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

        lay.addWidget(label("Lean AI Station", "H1"))
        lay.addWidget(label("Vous décrivez un problème de mathématiques ; l'IA l'écrit dans le langage <b>Lean</b> puis "
                            "cherche une preuve ; <b>Lean</b>, un logiciel de vérification, contrôle chaque ligne. "
                            "Une preuve n'est jamais affichée comme « réussie » sans son feu vert.<br>"
                            "Rien n'est envoyé sur Internet : l'IA est un fichier installé chez vous, exécuté par la carte graphique de l'ordinateur (à défaut, par son processeur).",
                            "Muted", wrap=True))

        card = Card(margins=18)
        card.lay.addWidget(label("Quel problème voulez-vous prouver ?", "H2"))
        self.nl = QPlainTextEdit()
        self.nl.setMinimumHeight(90)
        self.nl.setMaximumHeight(130)
        self.nl.setPlaceholderText("Écrivez-le comme vous le diriez à un collègue, en français ou en anglais.\n"
                                   "Exemple : « Montrer que la somme de deux entiers pairs est paire. »\n"
                                   "Formules LaTeX acceptées : $a^2 + b^2 \\ge 2ab$.")
        card.lay.addWidget(self.nl)
        row = QHBoxLayout()
        self.big = button("✨  Prouver un théorème", "Big",
                          "L'IA traduit votre problème en Lean ; vous relisez l'énoncé avant la recherche de preuve",
                          slot=self._go)
        row.addWidget(self.big)
        col = QVBoxLayout()
        col.addWidget(button("📄 Importer un fichier .tex…", tip="Prendre un théorème, un lemme ou un exercice dans un "
                             "document LaTeX (vous pouvez aussi glisser le .tex sur la fenêtre)", slot=self._tex))
        col.addWidget(button("Je sais écrire du Lean", "Link", "Ouvrir directement l'éditeur Lean (Ctrl+2)",
                             lambda: ctx.navigate.emit("lean")))
        row.addLayout(col)
        row.addStretch(1)
        card.lay.addLayout(row)
        self.status = label(obj="Muted", wrap=True)
        card.lay.addWidget(self.status)
        lay.addWidget(card)

        steps = QHBoxLayout()
        for n, (t, d) in enumerate([("Vous décrivez", "Avec vos mots, ou en important un .tex. Vous pouvez aussi écrire directement du Lean."),
                                    ("L'IA traduit en Lean", "Elle écrit l'énoncé officiel ; vous le relisez et le corrigez avant de continuer."),
                                    ("L'IA prouve, Lean vérifie", "Si Lean refuse une preuve, l'IA corrige et réessaie. Ensuite : explication en français, export LaTeX.")], 1):
            c = Card(margins=12)
            c.lay.addWidget(label(f"<b>{n}.  {t}</b>"))
            c.lay.addWidget(label(d, "Muted", wrap=True))
            steps.addWidget(c)
        lay.addLayout(steps)

        lay.addSpacing(6)
        lay.addWidget(label("Ou essayez un exercice : un clic lance la recherche de preuve", "H2"))
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, ex in enumerate(EXAMPLES):
            c = Card()
            c.setMinimumWidth(260)
            top = QHBoxLayout()
            t = label(ex.title, "H2", wrap=True)
            lvl = label(ex.level)
            col_ = LEVEL_COLORS.get(ex.level, theme.MUTED)
            lvl.setStyleSheet(f"color: {col_}; border: 1px solid {col_}; border-radius: 9px; padding: 1px 8px; font-size: 9pt;")
            lvl.setToolTip("Niveau indicatif de l'exercice")
            top.addWidget(t, 1)
            top.addWidget(lvl, 0, Qt.AlignTop)
            c.lay.addLayout(top)
            c.lay.addWidget(label(ex.blurb, "Muted", wrap=True))
            c.lay.addStretch(1)
            b = button("▶  Prouver", "Primary", f"Lance la recherche de preuve pour « {ex.title} »",
                       slot=lambda _=False, e=ex: ctx.proveRequest.emit(e.statement, e.blurb))
            b.setAccessibleName(f"Prouver : {ex.title}")
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
            self.status.setText("✍️ Écrivez d'abord votre problème dans la zone ci-dessus (ou importez un .tex).")

    def _tex(self):
        start = self.ctx.session.get("last_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Importer un fichier LaTeX", start, "Fichiers LaTeX (*.tex);;Tous (*)")
        if path:
            self.ctx.navigate.emit("lean")
            self.ctx.texRequest.emit(path)

    def refresh(self, *_):
        st = self.ctx.server.state
        ws = self.ctx.workspace()
        model = self.ctx.default_model()
        parts = []
        if st == "ready":
            parts.append("🟢 Modèle prêt")
        elif st == "starting":
            parts.append("🟡 Chargement du modèle…")
        elif model:
            parts.append("⚪ Le modèle se chargera automatiquement au premier clic")
        else:
            parts.append("⚠️ Aucun modèle installé (voir « Modèles »)")
        if self.ctx.formalizer_model() is None:
            parts.append("⚠️ Traducteur français → Lean absent : vous pouvez écrire l'énoncé en Lean (voir « Aide »)")
        parts.append(f"Espace Lean : {ws.label}" if ws else "⚠️ Aucun espace Lean prêt (voir « Système »)")
        self.status.setText("   ·   ".join(parts))
