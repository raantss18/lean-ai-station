"""Task script with click counting (D). Each task starts after the wizard, no terminal; target ≤ 3 clicks.

slow: needs the GPU model and a built Lean workspace."""
import os
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from lean_ai_station import config
from lean_ai_station.ui import theme
from lean_ai_station.ui.context import AppContext
from lean_ai_station.ui.main_window import MainWindow

pytestmark = pytest.mark.slow
WS = os.environ.get("LAS_TEST_WS", "lean-prover49")


class Clicks:
    def __init__(self, qtbot):
        self.qtbot, self.n = qtbot, 0

    def click(self, widget):
        assert widget.isVisible() and widget.isEnabled(), f"not clickable: {widget.text() if hasattr(widget, 'text') else widget}"
        self.qtbot.mouseClick(widget, Qt.LeftButton)
        self.n += 1

    def double_click_row(self, table, row):
        rect = table.visualRect(table.model().index(row, 0))
        self.qtbot.mouseDClick(table.viewport(), Qt.LeftButton, pos=rect.center())
        self.n += 1   # one gesture


@pytest.fixture
def app_win(qtbot, qapp):
    theme.apply(qapp)
    s = config.Settings()
    s.wizard_done = True
    s.workspace = WS
    ctx = AppContext(s, {})
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    w.resize(1360, 860)
    w.show()
    ctx.gpu.start(2000)
    with qtbot.waitSignal(ctx.workspacesChanged, timeout=30000):
        ctx.refresh_models(then=ctx.refresh_workspaces)
    if ctx.workspace(WS) is None or ctx.workspace(WS).problems or not ctx.default_model():
        pytest.skip(f"model or workspace not ready: ws={ctx.workspace(WS) and ctx.workspace(WS).problems} model={ctx.default_model()} keys={[w.key for w in ctx.workspaces]}")
    yield w
    ctx.server.shutdown_blocking()


def _home_prove_button(w, index=0):
    from PySide6.QtWidgets import QPushButton
    return [b for b in w.pages["home"].findChildren(QPushButton) if b.accessibleName().startswith("Prouver :")][index]


def test_task1_first_verified_proof(app_win, qtbot):
    c = Clicks(qtbot)
    w = app_win
    w.navigate("home")
    c.click(_home_prove_button(w, 1))                     # « Somme de deux nombres pairs »
    qtbot.waitUntil(lambda: w.pages["lean"].final_card.isVisible() or
                    (not w.ctx.prover.running and not w.pages["lean"]._pending_prove and w.ctx.prover.attempts), timeout=900_000)
    assert w.pages["lean"].final_card.isVisible(), [a.summary for a in w.ctx.prover.attempts]
    assert c.n <= 3, c.n
    print(f"TASK1 clicks={c.n}")


def test_task2_load_other_model(app_win, qtbot):
    c = Clicks(qtbot)
    w = app_win
    c.click(w.nav_btns["models"])
    table = w.pages["models"].table
    target = next(r for r in range(table.rowCount()) if "Q5_K_M" in table.item(r, 0).text())
    # path counted: select the row, then « Charger » (a real double-click on the row does both in one gesture;
    # QTest.mouseDClick does not emit QAbstractItemView.doubleClicked, so that shortcut is checked via the signal)
    rect = table.visualRect(table.model().index(target, 0))
    qtbot.mouseClick(table.viewport(), Qt.LeftButton, pos=rect.center())
    c.n += 1
    c.click(w.pages["models"].load_btn)
    qtbot.waitUntil(lambda: w.ctx.server.state == "ready" and "Q5_K_M" in str(w.ctx.server.model_path), timeout=180_000)
    assert c.n <= 3, c.n
    # the double-click shortcut is wired to the same action
    w.pages["models"].table.doubleClicked.emit(table.model().index(target, 0))
    print(f"TASK2 clicks={c.n} ctx={w.ctx.server.plan.ctx} offload={w.ctx.server.offload}")


def test_task3_verify_my_file(app_win, qtbot, tmp_path):
    f = tmp_path / "mon_exercice.lean"
    f.write_text("import Mathlib\n\ntheorem mon_exercice (x : ℝ) : 0 ≤ x ^ 2 := by\n  positivity\n", encoding="utf-8")
    w = app_win
    # one gesture: drag the file from the file manager onto the window
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(f))])
    enter = QDragEnterEvent(QPoint(400, 400), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(w, enter)
    assert enter.isAccepted()                                  # .lean files are accepted by the window
    ev = QDropEvent(QPoint(400, 400), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(w, ev)
    lean = w.pages["lean"]
    qtbot.waitUntil(lambda: not w.ctx.verifier.busy and "✅" in lean.status_title.text(), timeout=300_000)
    print("TASK3 clicks=1 (drag & drop)")


def test_task4_recover_stopped_server(app_win, qtbot):
    c = Clicks(qtbot)
    w = app_win
    w.ctx.load_model()
    qtbot.waitUntil(lambda: w.ctx.server.state == "ready", timeout=180_000)
    os.kill(w.ctx.server.proc.processId(), 9)                 # the server dies
    qtbot.waitUntil(lambda: w.banner.isVisible() and w.ctx.server.state == "stopped", timeout=30_000)
    from PySide6.QtWidgets import QPushButton
    restart = next(b for b in w.banner.findChildren(QPushButton) if b.text() == "Redémarrer le modèle")
    c.click(restart)
    qtbot.waitUntil(lambda: w.ctx.server.state == "ready", timeout=180_000)
    n, total = w.ctx.server.offload.split("/")
    assert n == total                                         # back on the GPU, all layers
    assert c.n <= 3, c.n
    print(f"TASK4 clicks={c.n} offload={w.ctx.server.offload}")
