"""Home: one big action + one-click example exercises."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

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

        lay.addWidget(label("Bienvenue dans Lean AI Station", "H1"))
        lay.addWidget(label("Écrivez un énoncé mathématique en Lean : l'IA cherche une preuve, et Lean la vérifie "
                            "rigoureusement. Tout fonctionne sur cet ordinateur, sans Internet.", "Muted", wrap=True))
        row = QHBoxLayout()
        self.big = button("✨  Prouver un théorème", "Big",
                          "Ouvre l'éditeur Lean pour écrire votre propre énoncé (Ctrl+2)",
                          slot=lambda: ctx.navigate.emit("lean"))
        row.addWidget(self.big)
        row.addSpacing(12)
        self.status = label(obj="Muted", wrap=True)
        row.addWidget(self.status, 1)
        lay.addLayout(row)

        lay.addSpacing(10)
        lay.addWidget(label("Exercices prêts à l'emploi — un clic pour lancer la preuve", "H2"))
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, ex in enumerate(EXAMPLES):
            c = Card()
            c.setMinimumWidth(260)
            top = QHBoxLayout()
            t = label(ex.title, "H2", wrap=True)
            lvl = label(ex.level)
            col = LEVEL_COLORS.get(ex.level, theme.MUTED)
            lvl.setStyleSheet(f"color: {col}; border: 1px solid {col}; border-radius: 9px; padding: 1px 8px; font-size: 9pt;")
            lvl.setToolTip("Niveau indicatif de l'exercice")
            top.addWidget(t, 1)
            top.addWidget(lvl, 0, Qt.AlignTop)
            c.lay.addLayout(top)
            c.lay.addWidget(label(ex.blurb, "Muted", wrap=True))
            c.lay.addStretch(1)
            b = button("▶  Prouver", "Primary", f"Lance la recherche de preuve pour « {ex.title} »",
                       slot=lambda _=False, s=ex.statement: self._prove(s))
            b.setObjectName("Primary")
            b.setAccessibleName(f"Prouver : {ex.title}")
            c.lay.addWidget(b, 0, Qt.AlignRight)
            grid.addWidget(c, i // 3, i % 3)
        lay.addLayout(grid)
        lay.addStretch(1)
        ctx.server.stateChanged.connect(self.refresh)
        ctx.modelsChanged.connect(self.refresh)
        ctx.workspacesChanged.connect(self.refresh)
        self.refresh()

    def _prove(self, statement: str):
        self.ctx.proveRequest.emit(statement)

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
        parts.append(f"Espace Lean : {ws.label}" if ws else "⚠️ Aucun espace Lean prêt (voir « Système »)")
        self.status.setText("\n".join(parts))
