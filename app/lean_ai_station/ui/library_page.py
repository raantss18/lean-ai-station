"""Library tab: every result Lean has accepted, reused automatically as a lemma in later proofs (same Lean version)."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLineEdit, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..i18n import _
from .widgets import LeanEditor, button, label


class LibraryPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.lib = ctx.pipeline.library
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 12)
        head = QHBoxLayout()
        head.addWidget(label(_("Bibliothèque de résultats"), "H2"))
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Rechercher (titre, nom, symbole…)"))
        self.search.setMinimumWidth(280)
        self.search.textChanged.connect(self.fill)
        head.addWidget(self.search)
        head.addWidget(button(_("Tout exporter en .lean…"), tip=_("Un seul fichier Lean avec tous vos résultats prouvés"),
                              slot=self.export_all))
        lay.addLayout(head)
        lay.addWidget(label(_("Chaque théorème prouvé est rangé ici automatiquement. Quand un nouveau problème lui "
                              "ressemble, il est proposé à l'IA comme lemme (avec sa preuve), pour la même version de "
                              "Lean."), "Muted", wrap=True))
        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([_("Titre"), _("Nom Lean"), _("Espace Lean"), _("Date")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._sel)
        split.addWidget(self.table)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        self.code = LeanEditor(read_only=True)
        self.code.setPlaceholderText(_("Sélectionnez un résultat pour voir son énoncé et sa preuve."))
        bl.addWidget(self.code, 1)
        row = QHBoxLayout()
        self.copy_btn = button(_("📋 Copier"), "Primary", _("Copier l'énoncé et la preuve"), self.copy)
        self.open_btn = button(_("Ouvrir le dossier d'origine"), tip=_("Rouvrir la discussion qui a produit ce résultat"),
                               slot=self.open_dossier)
        self.del_btn = button(_("Retirer de la bibliothèque"), "Danger", _("Il ne sera plus proposé (annulable)"), self.remove)
        row.addWidget(self.copy_btn)
        row.addWidget(self.open_btn)
        row.addStretch(1)
        row.addWidget(self.del_btn)
        bl.addLayout(row)
        split.addWidget(bottom)
        lay.addWidget(split, 1)
        self.empty = label(_("La bibliothèque est vide : elle se remplit à chaque preuve réussie."), "Muted", wrap=True)
        lay.addWidget(self.empty)
        ctx.pipeline.changed.connect(self.fill)
        self.fill()

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self.lib.load()
        self.fill()

    def _entries(self):
        q = self.search.text().strip().lower()
        out = sorted(self.lib.entries, key=lambda e: -e.t)
        if q:
            out = [e for e in out if q in (e.title + " " + e.name + " " + e.statement).lower()]
        return out

    def fill(self, *_a):
        self.table.setRowCount(0)
        labels = {w.key: _(w.label) for w in self.ctx.workspaces}
        for e in self._entries():
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, v in enumerate([e.title, e.name, labels.get(e.workspace, e.workspace),
                                   _dt.datetime.fromtimestamp(e.t).strftime("%d/%m/%Y %H:%M")]):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, e.name)
                self.table.setItem(r, c, it)
        self.empty.setVisible(not self.lib.entries)
        self._sel()

    def _current(self):
        items = self.table.selectedItems()
        return self.lib.get(items[0].data(Qt.UserRole)) if items else None

    def _sel(self):
        e = self._current()
        self.code.setPlainText(e.code if e else "")
        for b in (self.copy_btn, self.open_btn, self.del_btn):
            b.setEnabled(e is not None)
        if e:
            self.open_btn.setEnabled(bool(e.dossier) and self.ctx.pipeline.store.load(e.dossier) is not None)

    def copy(self):
        e = self._current()
        if e:
            QGuiApplication.clipboard().setText(e.code)
            self.ctx.toast.emit(_("Copié."), None, None)

    def open_dossier(self):
        e = self._current()
        if e and self.ctx.pipeline.open(e.dossier):
            self.ctx.navigate.emit("lean")

    def remove(self):
        e = self._current()
        if not e:
            return
        self.lib.remove(e.name)
        self.fill()
        self.ctx.toast.emit(_("« {t} » retiré de la bibliothèque.").format(t=e.title),
                            lambda: (self.lib.restore(e), self.fill()), None)

    def export_all(self):
        if not self.lib.entries:
            self.ctx.toast.emit(_("La bibliothèque est vide."), None, None)
            return
        start = self.ctx.session.get("last_dir", str(Path.home() / "Documents"))
        path, _f = QFileDialog.getSaveFileName(self, _("Exporter la bibliothèque"), str(Path(start) / "bibliotheque.lean"),
                                               _("Fichiers Lean (*.lean)"))
        if not path:
            return
        from ..leancheck import GOEDEL_HEADER
        from ..dossiers import closure
        parts = [GOEDEL_HEADER.rstrip()]
        for e in closure(self.lib, sorted(self.lib.entries, key=lambda e: e.t)):
            parts.append(f"-- {e.title}  ({e.workspace})\n{e.code.strip()}")
        Path(path).write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        self.ctx.toast.emit(_("Enregistré : {name}").format(name=Path(path).name), None, None)
