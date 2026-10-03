"""Dark theme (palette + stylesheet). Contrast checked: body text #E6E9EF on #15171C ≈ 14:1."""
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

ASSETS = (Path(__file__).parent.parent / "assets").as_posix()

BG = "#15171C"
PANEL = "#1D2027"
PANEL2 = "#252933"
BORDER = "#323744"
TEXT = "#E6E9EF"
MUTED = "#A3ABBA"
ACCENT = "#5B8CFF"
ACCENT_H = "#7AA2FF"
OK = "#3FB950"
WARN = "#E3B341"
ERR = "#F85149"

QSS = f"""
* {{ font-size: 10.5pt; }}
QWidget {{ background: {BG}; color: {TEXT}; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
#Sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
#Sidebar QPushButton {{ text-align: left; padding: 10px 14px; border: none; border-radius: 8px; background: transparent;
    color: {MUTED}; font-size: 11pt; }}
#Sidebar QPushButton:hover {{ background: {PANEL2}; color: {TEXT}; }}
#Sidebar QPushButton:checked {{ background: {PANEL2}; color: {TEXT}; border-left: 3px solid {ACCENT}; }}
#AppTitle {{ font-size: 13pt; font-weight: 700; padding: 6px 8px 14px 8px; background: transparent; }}
#StatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}
#StatusBar QLabel {{ background: transparent; color: {MUTED}; }}
QLabel#H1 {{ font-size: 20pt; font-weight: 700; }}
QLabel#H2 {{ font-size: 13pt; font-weight: 600; }}
QLabel#Muted {{ color: {MUTED}; }}
QFrame#Card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#Card QLabel, QFrame#Card QCheckBox, QGroupBox QCheckBox {{ background: transparent; }}
QFrame#Banner {{ border-radius: 10px; }}
QFrame#Banner QLabel {{ background: transparent; }}
QPushButton {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: #6B7280; border-color: {PANEL2}; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_H}; }}
QPushButton#Primary:disabled {{ background: #34446B; color: #A9B4CC; }}
QPushButton#Big {{ background: {ACCENT}; border: none; color: white; font-weight: 700; font-size: 15pt;
    padding: 18px 28px; border-radius: 14px; }}
QPushButton#Big:hover {{ background: {ACCENT_H}; }}
QPushButton#Danger {{ background: #3A1D20; border: 1px solid #6E2A2E; color: #FFB4AE; }}
QPushButton#Danger:hover {{ border-color: {ERR}; }}
QPushButton#Danger:disabled {{ background: {PANEL2}; border-color: {PANEL2}; color: #6B7280; }}
QWidget#Clear, QStackedWidget#Clear {{ background: transparent; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 20px;
    background: {PANEL2}; border-left: 1px solid {BORDER}; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 8px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 8px; }}
QAbstractSpinBox::up-arrow {{ image: url({ASSETS}/up.svg); width: 10px; height: 6px; }}
QAbstractSpinBox::down-arrow {{ image: url({ASSETS}/down.svg); width: 10px; height: 6px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: none; }}
QComboBox::down-arrow {{ image: url({ASSETS}/down.svg); width: 10px; height: 6px; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QPushButton#Link {{ background: transparent; border: none; color: {ACCENT_H}; padding: 2px 4px; text-decoration: underline; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QTextBrowser, QListWidget, QTableWidget {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px;
    selection-background-color: #2F4A86; }}
QComboBox QAbstractItemView {{ background: {PANEL2}; border: 1px solid {BORDER}; selection-background-color: #2F4A86; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QHeaderView::section {{ background: {PANEL2}; color: {MUTED}; border: none; padding: 6px; font-weight: 600; }}
QTableWidget {{ gridline-color: {BORDER}; }}
QListWidget::item {{ padding: 6px; border-radius: 6px; }}
QListWidget::item:selected {{ background: #2A3550; }}
QProgressBar {{ background: {PANEL2}; border: none; border-radius: 5px; height: 10px; text-align: center; color: {TEXT}; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QSlider::groove:horizontal {{ height: 6px; background: {PANEL2}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 16px; margin: -6px 0; border-radius: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 30px; }}
QSplitter::handle {{ background: {BORDER}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; }}
QTabBar::tab {{ background: {PANEL}; padding: 7px 14px; border-top-left-radius: 8px; border-top-right-radius: 8px; color: {MUTED}; }}
QTabBar::tab:selected {{ background: {PANEL2}; color: {TEXT}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 10px; margin-top: 14px; padding-top: 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; }}
"""


def mono_font(size: int = 11) -> QFont:
    for fam in ("JetBrains Mono", "JetBrainsMono Nerd Font Mono", "DejaVu Sans Mono", "Noto Sans Mono"):
        if fam in QFontDatabase.families():
            f = QFont(fam, size)
            f.setStyleHint(QFont.Monospace)
            return f
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setPointSize(size)
    return f


def apply(app) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in [(QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, PANEL),
                      (QPalette.AlternateBase, PANEL2), (QPalette.Text, TEXT), (QPalette.Button, PANEL2),
                      (QPalette.ButtonText, TEXT), (QPalette.Highlight, "#2F4A86"), (QPalette.HighlightedText, TEXT),
                      (QPalette.ToolTipBase, PANEL2), (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, "#7D8596"),
                      (QPalette.Link, ACCENT_H)]:
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
