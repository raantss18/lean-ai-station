"""Reusable widgets: Banner, BusyBar, Toast, Card, LeanEditor (line numbers, highlighting, unicode input)."""
from __future__ import annotations

import re

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy,
                               QTextEdit, QVBoxLayout, QWidget)

from . import theme
from ..errors import Friendly


def label(text: str = "", obj: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if obj:
        lab.setObjectName(obj)
    lab.setWordWrap(wrap)
    return lab


def button(text: str, obj: str | None = None, tip: str = "", slot=None) -> QPushButton:
    b = QPushButton(text)
    if obj:
        b.setObjectName(obj)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.PointingHandCursor)
    return b


class Card(QFrame):
    def __init__(self, parent=None, margins: int = 16):
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(margins, margins, margins, margins)
        self.lay.setSpacing(8)


class Banner(QFrame):
    """Plain-language message with action buttons and collapsible technical details."""
    action = Signal(str)
    COLORS = {"error": ("#3A1D20", "#6E2A2E", "⛔"), "warn": ("#3A3018", "#6B5A22", "⚠️"),
              "info": ("#1D2A40", "#2F4A86", "ℹ️"), "ok": ("#16301F", "#2C6B3C", "✅")}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Banner")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        top = QHBoxLayout()
        self.icon = label()
        self.icon.setFixedWidth(26)
        self.title = label(obj="H2")
        top.addWidget(self.icon)
        top.addWidget(self.title, 1)
        self.close_btn = button("✕", tip="Fermer ce message", slot=self.hide)
        self.close_btn.setFixedWidth(34)
        top.addWidget(self.close_btn)
        lay.addLayout(top)
        self.msg = label(wrap=True)
        self.msg.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.msg)
        self.btns = QHBoxLayout()
        self.btns.addStretch(1)
        lay.addLayout(self.btns)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setFont(theme.mono_font(9))
        self.details.setMaximumHeight(160)
        self.details.hide()
        lay.addWidget(self.details)
        self.hide()

    def show_message(self, f: Friendly, details: str = ""):
        bg, border, icon = self.COLORS.get(f.level, self.COLORS["error"])
        self.setStyleSheet(f"QFrame#Banner {{ background: {bg}; border: 1px solid {border}; }}")
        self.icon.setText(icon)
        self.title.setText(f.title)
        self.msg.setText(f.message)
        while self.btns.count() > 1:
            w = self.btns.takeAt(1).widget()
            if w:
                w.deleteLater()
        for text, act in f.actions:
            self.btns.addWidget(button(text, "Primary", slot=lambda _=False, a=act: self._act(a)))
        self.details.hide()
        if details.strip():
            self.details.setPlainText(details.strip())
            t = button("Afficher les détails", "Link")
            t.clicked.connect(lambda: (self.details.setVisible(not self.details.isVisible()),
                                       t.setText("Masquer les détails" if self.details.isVisible() else "Afficher les détails")))
            self.btns.insertWidget(1, t)
        self.show()

    def _act(self, a: str):
        self.hide()
        self.action.emit(a)


class BusyBar(QFrame):
    """'Something is running' strip: text + indeterminate/determinate bar + Annuler."""
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        self.text = label()
        self.bar = QProgressBar()
        self.bar.setMaximumWidth(260)
        self.bar.setTextVisible(False)
        self.cancel = button("Annuler", tip="Arrêter cette opération (Échap)", slot=self.cancelled.emit)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.bar)
        lay.addWidget(self.cancel)
        self.hide()

    def start(self, text: str, cancellable: bool = True, maximum: int = 0):
        self.text.setText(text)
        self.bar.setRange(0, maximum)
        self.cancel.setVisible(cancellable)
        self.show()

    def progress(self, value: int, maximum: int | None = None, text: str | None = None):
        if maximum is not None:
            self.bar.setRange(0, maximum)
        self.bar.setValue(value)
        if text:
            self.text.setText(text)

    def stop(self):
        self.hide()


class Toast(QFrame):
    """Transient bottom message, optionally with an « Annuler » (undo) button."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setStyleSheet(f"QFrame#Card {{ background: {theme.PANEL2}; border: 1px solid {theme.ACCENT}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 8, 8)
        self.text = label()
        self.undo_btn = button("Annuler", "Primary")
        lay.addWidget(self.text)
        lay.addWidget(self.undo_btn)
        self._undo = None
        self.undo_btn.clicked.connect(self._do_undo)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._expire)
        self.hide()

    def show_toast(self, text: str, undo=None, ms: int = 6000, on_expire=None):
        if self._undo is not None and self._expire_cb:
            self._expire_cb()            # previous pending action becomes final
        self.text.setText(text)
        self._undo, self._expire_cb = undo, on_expire
        self.undo_btn.setVisible(undo is not None)
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self.timer.start(ms)

    _expire_cb = None

    def _place(self):
        p = self.parentWidget()
        if p:
            self.move((p.width() - self.width()) // 2, p.height() - self.height() - 46)

    def _do_undo(self):
        u, self._undo, self._expire_cb = self._undo, None, None
        self.hide()
        if u:
            u()

    def _expire(self):
        cb, self._undo, self._expire_cb = self._expire_cb, None, None
        self.hide()
        if cb:
            cb()


# ------------------------------------------------------------------ Lean editor
ABBREVIATIONS = {
    "R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "to": "→", "r": "→", "l": "←", "iff": "↔", "lr": "↔",
    "le": "≤", "ge": "≥", "ne": "≠", "neq": "≠", "and": "∧", "or": "∨", "not": "¬", "forall": "∀", "all": "∀",
    "exists": "∃", "ex": "∃", "in": "∈", "notin": "∉", "sub": "⊆", "subset": "⊆", "ssub": "⊂", "cap": "∩",
    "cup": "∪", "inter": "∩", "union": "∪", "empty": "∅", "lam": "λ", "fun": "λ", "x": "×", "times": "×",
    "cdot": "·", ".": "·", "sqrt": "√", "inf": "∞", "infty": "∞", "alpha": "α", "beta": "β", "gamma": "γ",
    "delta": "δ", "epsilon": "ε", "eps": "ε", "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "sigma": "σ",
    "phi": "φ", "psi": "ψ", "omega": "ω", "Sum": "∑", "sum": "∑", "Prod": "∏", "prod": "∏", "circ": "∘",
    "comp": "∘", "|": "∣", "dvd": "∣", "langle": "⟨", "rangle": "⟩", "<": "⟨", ">": "⟩", "^-1": "⁻¹", "inv": "⁻¹",
    "1": "₁", "2": "₂", "3": "₃", "0": "₀", "n": "ₙ", "abs": "|", "norm": "‖", "deg": "°", "mid": "∣",
}
KEYWORDS = r"\b(theorem|lemma|example|def|by|have|show|from|fun|let|in|with|match|at|calc|intro|intros|exact|apply|" \
           r"rw|simp|norm_num|linarith|nlinarith|ring|ring_nf|field_simp|omega|decide|constructor|cases|rcases|" \
           r"obtain|induction|use|refine|positivity|gcongr|aesop|tauto|exfalso|contradiction|unfold|specialize|" \
           r"import|open|namespace|end|section|variable|set_option|noncomputable|instance|structure|class|where|if|then|else)\b"


class LeanHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(700)
            f.setFontItalic(italic)
            return f
        self.rules = [
            (re.compile(KEYWORDS), fmt("#C792EA", bold=True)),
            (re.compile(r"\b(sorry|admit)\b"), fmt(theme.ERR, bold=True)),
            (re.compile(r"\b\d+(\.\d+)?\b"), fmt("#F78C6C")),
            (re.compile(r"[ℝℕℤℚℂ]|\b(Real|Nat|Int|Rat|Complex|Prop|Type|Finset|Set)\b"), fmt("#82AAFF")),
            (re.compile(r"[→←↔∀∃∧∨¬≤≥≠∈∉⊆∩∪λ×·∘⟨⟩∣]"), fmt("#89DDFF")),
        ]
        self.comment = fmt("#7D8596", italic=True)

    def highlightBlock(self, text):  # noqa: N802
        for rx, f in self.rules:
            for m in rx.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), f)
        i = text.find("--")
        if i >= 0:
            self.setFormat(i, len(text) - i, self.comment)
        # block comments /- ... -/
        self.setCurrentBlockState(0)
        continuing = self.previousBlockState() == 1
        start = 0 if continuing else text.find("/-")
        while start >= 0:
            end = text.find("-/", start if continuing else start + 2)
            continuing = False
            if end < 0:
                self.setCurrentBlockState(1)
                self.setFormat(start, len(text) - start, self.comment)
                break
            self.setFormat(start, end - start + 2, self.comment)
            start = text.find("/-", end + 2)


class _LineArea(QWidget):
    def __init__(self, ed):
        super().__init__(ed)
        self.ed = ed

    def sizeHint(self):
        return QSize(self.ed.line_area_width(), 0)

    def paintEvent(self, e):  # noqa: N802
        self.ed.paint_lines(e)


class LeanEditor(QPlainTextEdit):
    """Code editor; typing `\\R` then space/tab inserts ℝ (same abbreviations as VS Code Lean)."""

    def __init__(self, parent=None, read_only: bool = False):
        super().__init__(parent)
        self.setFont(theme.mono_font(11))
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 2)
        self.setReadOnly(read_only)
        self.hl = LeanHighlighter(self.document())
        self.area = _LineArea(self)
        self.blockCountChanged.connect(lambda _: self.setViewportMargins(self.line_area_width(), 0, 0, 0))
        self.updateRequest.connect(self._upd)
        self.setViewportMargins(self.line_area_width(), 0, 0, 0)
        self._marks: dict[int, str] = {}

    def line_area_width(self) -> int:
        return 14 + self.fontMetrics().horizontalAdvance("9") * max(3, len(str(self.blockCount())))

    def _upd(self, rect, dy):
        if dy:
            self.area.scroll(0, dy)
        else:
            self.area.update(0, rect.y(), self.area.width(), rect.height())

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.area.setGeometry(QRect(cr.left(), cr.top(), self.line_area_width(), cr.height()))

    def set_error_lines(self, lines: dict[int, str]):
        """Highlight lines (1-based) with errors; tooltip-free, just a red background."""
        self._marks = lines
        sels = []
        for ln in lines:
            block = self.document().findBlockByNumber(ln - 1)
            if not block.isValid():
                continue
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor("#4A1F24"))
            sel.format.setProperty(QTextCharFormat.FullWidthSelection, True)
            sel.cursor = QTextCursor(block)
            sels.append(sel)
        self.setExtraSelections(sels)
        self.area.update()

    def paint_lines(self, e):
        p = QPainter(self.area)
        p.fillRect(e.rect(), QColor(theme.PANEL))
        block = self.firstVisibleBlock()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        h = self.fontMetrics().height()
        while block.isValid() and top <= e.rect().bottom():
            n = block.blockNumber() + 1
            p.setPen(QColor(theme.ERR if n in self._marks else "#5C6370"))
            p.drawText(0, top, self.area.width() - 6, h, Qt.AlignRight, str(n))
            block = block.next()
            top += int(self.blockBoundingRect(block).height()) if block.isValid() else h

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key_Space, Qt.Key_Tab) and not self.isReadOnly() and self._expand():
            if e.key() == Qt.Key_Tab:
                return
        super().keyPressEvent(e)

    def _expand(self) -> bool:
        c = self.textCursor()
        line = c.block().text()[: c.positionInBlock()]
        m = re.search(r"\\([^\s\\]+)$", line)
        if not m or m.group(1) not in ABBREVIATIONS:
            return False
        c.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(m.group(0)))
        c.insertText(ABBREVIATIONS[m.group(1)])
        return True

    def set_text_undoable(self, text: str):
        """Replace content but keep it undoable with Ctrl+Z."""
        c = self.textCursor()
        c.beginEditBlock()
        c.select(QTextCursor.Document)
        c.insertText(text)
        c.endEditBlock()
        self.moveCursor(QTextCursor.Start)


def shortcut(widget: QWidget, keys: str, slot) -> QShortcut:
    s = QShortcut(QKeySequence(keys), widget)
    s.setContext(Qt.WidgetWithChildrenShortcut)
    s.activated.connect(slot)
    return s


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet(f"color: {theme.BORDER}; background: {theme.BORDER}; max-height: 1px;")
    f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return f
