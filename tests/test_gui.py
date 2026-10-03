"""Fast GUI tests (no model needed): every page renders, empty states, banner, undo, double clicks."""
import pytest

from lean_ai_station import config
from lean_ai_station.errors import friendly
from lean_ai_station.ui import theme
from lean_ai_station.ui.context import AppContext
from lean_ai_station.ui.main_window import NAV, MainWindow


@pytest.fixture
def win(qtbot, qapp):
    theme.apply(qapp)
    s = config.Settings()
    s.wizard_done = True
    ctx = AppContext(s, {})
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    w.show()
    with qtbot.waitSignal(ctx.workspacesChanged, timeout=20000):
        ctx.refresh_models(then=ctx.refresh_workspaces)
    yield w
    ctx.server.shutdown_blocking()


def test_all_pages_render(win, tmp_path):
    for key, _t, _k in NAV:
        win.navigate(key)
        assert win.stack.currentWidget() is win.pages[key]
        assert win.nav_btns[key].isChecked()
        img = win.grab()
        assert not img.isNull()


def test_banner_details_hidden_by_default(win):
    win.ctx.banner.emit(friendly("server_crashed"), "Traceback: raw stuff")
    assert win.banner.isVisible()
    from PySide6.QtWidgets import QPushButton
    texts = [b.text() for b in win.banner.findChildren(QPushButton) if b.isVisibleTo(win.banner)]
    assert "Redémarrer le modèle" in texts and "Afficher les détails" in texts
    assert not win.banner.details.isVisible()          # raw details only behind « Afficher les détails »
    assert "Traceback" not in win.banner.msg.text()


def test_chat_clear_is_undoable(win, qtbot):
    chat = win.pages["chat"]
    chat.history = [{"role": "user", "content": "bonjour"}]
    chat.new_chat()
    assert chat.history == []
    assert win.toast.undo_btn.isVisible()
    qtbot.mouseClick(win.toast.undo_btn, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.LeftButton)
    assert chat.history and chat.history[0]["content"] == "bonjour"


def test_editor_unicode_abbreviation(win, qtbot):
    from PySide6.QtCore import Qt
    lean = win.pages["lean"]
    win.navigate("lean")
    ed = lean.editor
    ed.setPlainText("")
    ed.setFocus()
    qtbot.keyClicks(ed, "x : \\R")
    qtbot.keyClick(ed, Qt.Key_Space)
    assert ed.toPlainText() == "x : ℝ "


def test_prove_rejects_bad_statement_without_starting(win):
    lean = win.pages["lean"]
    lean.editor.setPlainText("def f := 1")
    shown = []
    win.ctx.banner.connect(lambda f, d: shown.append(f.title))
    lean.prove()
    assert shown and "Énoncé" in shown[0]
    assert lean.prove_btn.isEnabled() and not win.ctx.prover.running


def test_double_click_prove_starts_once(win, monkeypatch):
    lean = win.pages["lean"]
    calls = []
    monkeypatch.setattr(win.ctx, "ensure_model", lambda then: calls.append(then))
    ws = [w for w in win.ctx.workspaces if not w.problems]
    if not ws:
        pytest.skip("no ready Lean workspace on this machine")
    win.ctx.settings.workspace = ws[0].key
    lean.editor.setPlainText("theorem t : 1 = 1 := by sorry")
    lean.prove()
    lean.prove()
    lean.prove_btn.click()
    assert len(calls) == 1
    lean.stop()
    assert lean.prove_btn.isEnabled()


def test_offline_blocks_remote_requests(win):
    from lean_ai_station.services import Net
    win.ctx.set_offline(True)
    assert not Net.allowed("https://huggingface.co/api/models")
    assert Net.allowed("http://127.0.0.1:8765/health")
    assert not win.pages["models"].dl_btn.isEnabled()
    win.ctx.set_offline(False)
    assert Net.allowed("https://huggingface.co/api/models")
    win.ctx.set_offline(True)


def test_gpu_unavailable_falls_back_to_cpu(win, monkeypatch, tmp_path):
    ctx = win.ctx
    started = []
    monkeypatch.setattr(ctx.server, "start", lambda path, s, vram: started.append(vram))
    titles = []
    ctx.banner.connect(lambda f, d: titles.append(f.title))
    ctx.gpu.unavailable.emit("NVIDIA-SMI has failed")
    assert ctx.gpu_ok is False
    fake = tmp_path / "m.gguf"
    fake.write_bytes(b"GGUF")
    ctx.load_model(fake)
    assert started == [None]                       # None VRAM => plan_launch puts 0 layers on GPU
    assert any("Carte graphique" in t for t in titles)
    from lean_ai_station.services import plan_launch
    assert plan_launch(fake, ctx.settings.server, None).gpu_layers == 0


def test_disk_nearly_full_warns(win, monkeypatch):
    ctx = win.ctx
    titles = []
    ctx.banner.connect(lambda f, d: titles.append(f.title))
    monkeypatch.setattr(ctx, "disk_free_gb", lambda *a: 2.0)
    assert ctx.check_disk() is False
    assert titles == ["Disque presque plein"]
    assert win.banner.isVisible()
