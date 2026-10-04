"""Fast GUI tests (no model needed): every page renders, empty states, banner, undo, double clicks."""
import pytest
from PySide6.QtCore import Qt

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


def test_formalizer_is_never_the_default_prover(win, tmp_path):
    from pathlib import Path
    from lean_ai_station.gguf import GGUFInfo
    ctx = win.ctx
    def info(name):
        return GGUFInfo(Path(name), 5_000_000_000, name, "qwen3", "Q4_K_M", 36, 40960, 4096, 8, 128)
    ctx.models = [info("/m/Goedel-Formalizer-V2-8B.Q4_K_M.gguf"), info("/m/Goedel-Prover-V2-8B.Q4_K_M.gguf")]
    ctx.settings.model_path = "/m/Goedel-Formalizer-V2-8B.Q4_K_M.gguf"
    assert "Prover" in str(ctx.default_model())
    assert "Formalizer" in str(ctx.formalizer_model())


def test_chat_tab_is_gone(win):
    assert "chat" not in win.pages and all(k != "chat" for k, _t, _s in NAV)


def test_explainer_is_never_the_default_prover(win):
    from pathlib import Path
    from lean_ai_station.gguf import GGUFInfo
    ctx = win.ctx
    def info(name):
        return GGUFInfo(Path(name), 5_000_000_000, name, "qwen3", "Q4_K_M", 36, 40960, 4096, 8, 128)
    ctx.models = [info("/m/Qwen3-8B-Q4_K_M.gguf"), info("/m/Goedel-Prover-V2-8B.Q4_K_M.gguf")]
    ctx.settings.model_path = "/m/Qwen3-8B-Q4_K_M.gguf"
    assert "Prover" in str(ctx.default_model()) and "Qwen3" in str(ctx.explainer_model())


def _no_models(win, monkeypatch, calls):
    from pathlib import Path
    monkeypatch.setattr(win.ctx, "ensure_model", lambda then, role="prover": calls.append(role))
    monkeypatch.setattr(win.ctx, "role_model", lambda role: Path(f"/models/{role}.gguf"))
    ws = [w for w in win.ctx.workspaces if not w.problems]
    if not ws:
        pytest.skip("no ready Lean workspace on this machine")
    win.ctx.settings.workspace = ws[0].key


def test_prove_rejects_bad_statement_without_starting(win):
    lean = win.pages["lean"]
    lean.editor.setPlainText("def f := 1")
    shown = []
    win.ctx.banner.connect(lambda f, d: shown.append(f.title))
    lean.prove()
    assert shown and "Énoncé" in shown[0]
    assert lean.prove_btn.isEnabled() and not win.ctx.pipeline.busy


def test_double_click_prove_starts_once(win, monkeypatch):
    calls = []
    _no_models(win, monkeypatch, calls)
    lean = win.pages["lean"]
    lean.new_dossier()
    lean.editor.setPlainText("theorem t : 1 = 1 := by sorry")
    lean.prove()
    lean.prove()
    lean.prove_btn.click()
    assert calls == ["prover"]
    lean.stop()
    assert not win.ctx.pipeline.busy and lean.prove_btn.isEnabled()


def test_home_button_starts_the_automatic_chain(win, qtbot, monkeypatch):
    calls = []
    _no_models(win, monkeypatch, calls)
    home = win.pages["home"]
    home._go()                                    # empty box: nothing is sent, the user is guided instead
    assert calls == [] and "Écrivez d'abord" in home.status.text()
    home.nl.setPlainText("Montrer que 2 + 2 = 4.")
    qtbot.mouseClick(home.big, Qt.LeftButton)
    assert calls == ["explainer"]                 # chain starts by understanding the problem (then the translator)
    d = win.ctx.pipeline.dossier
    assert d and d.problem == "Montrer que 2 + 2 = 4." and d.events[0].kind == "user"
    assert win.stack.currentWidget() is win.pages["lean"]
    win.pages["lean"].stop()


def test_send_in_empty_dossier_and_missing_model(win, monkeypatch):
    lean = win.pages["lean"]
    lean.new_dossier()
    lean.input.setPlainText("   ")
    lean.send()
    assert not win.ctx.pipeline.busy and win.ctx.pipeline.dossier is None
    shown = []
    win.ctx.banner.connect(lambda f, d: shown.append(f.title))
    monkeypatch.setattr(win.ctx, "formalizer_model", lambda: None)
    if not [w for w in win.ctx.workspaces if not w.problems]:
        pytest.skip("no ready Lean workspace")
    lean.input.setPlainText("Montrer que 2 + 2 = 4.")
    lean.send()
    assert shown and "traducteur" in shown[-1].lower()
    assert not win.ctx.pipeline.busy and lean.send_btn.isEnabled()
    assert win.ctx.pipeline.dossier.events[-1].kind == "error"


def test_import_tex_prepares_a_new_problem(win, tmp_path):
    tex = tmp_path / "cours.tex"
    tex.write_text(r"\begin{document}\begin{lemma}[Cauchy] Pour $a,b$ réels, $2ab\le a^2+b^2$.\end{lemma}\end{document}")
    lean = win.pages["lean"]
    lean.load_tex(str(tex))
    assert lean.input.toPlainText() == "Pour $a,b$ réels, $2ab\\le a^2+b^2$." and win.ctx.pipeline.dossier is None
    bad = tmp_path / "vide.tex"
    bad.write_text("% rien du tout")
    shown = []
    win.ctx.banner.connect(lambda f, d: shown.append(f.title))
    lean.load_tex(str(bad))
    assert shown == ["Aucun énoncé trouvé"]


def _dossier_with_versions(win, tmp_path):
    from lean_ai_station import dossiers as ds
    p = win.ctx.pipeline
    p.store = ds.DossierStore(tmp_path / "dossiers")
    d = p.new("Soit $n$ un entier.")
    i0 = d.add_statement("import Mathlib\n\ntheorem t (n : ℕ) : n = n := by sorry")
    d.add_event("statement", "v1", ref=i0)
    j0 = d.add_proof("import Mathlib\n\ntheorem t (n : ℕ) : n = n := by\n  rfl\n")
    d.add_event("proof", "ok", ref=j0)
    k0 = d.add_explanation("**Énoncé.** Tout entier est égal à lui-même ($n = n$).")
    d.add_event("explanation", "expl", ref=k0)
    i1 = d.add_statement("import Mathlib\n\ntheorem t (n : ℤ) : n = n := by sorry", source="user")
    d.add_event("statement", "v2", ref=i1)
    p._save()
    return d


def test_thread_shows_versions_and_restore_link(win, tmp_path):
    from PySide6.QtCore import QUrl
    d = _dossier_with_versions(win, tmp_path)
    lean = win.pages["lean"]
    html = lean.thread.toHtml()
    assert "Revenir à cette version" in html and "version actuelle" in html
    assert "theorem t (n : ℤ)" in lean.editor.toPlainText()
    assert not lean.quick_btns[1].isVisible() or not d.proof_is_current      # proof belongs to the old statement
    lean._anchor(QUrl("restore:proof:0"))
    assert d.proof_is_current and "theorem t (n : ℕ)" in lean.editor.toPlainText()
    assert "Retour à la version 1" in d.events[-1].text


def test_latex_export_has_statement_explanation_and_proof(win, tmp_path):
    _dossier_with_versions(win, tmp_path)
    win.ctx.pipeline.restore("proof", 0)
    tex = win.pages["lean"].latex_source()
    assert "Soit $n$ un entier." in tex and "theorem t (n : ℕ) : n = n := by sorry" in tex and "  rfl" in tex
    assert "Explication de la preuve" in tex and "% !TEX program = xelatex" in tex


def test_delete_dossier_is_undoable(win, tmp_path, qtbot):
    d = _dossier_with_versions(win, tmp_path)
    lean = win.pages["lean"]
    lean.delete_dossier()
    assert win.ctx.pipeline.dossier is None and not win.ctx.pipeline.store.load(d.id)
    qtbot.mouseClick(win.toast.undo_btn, Qt.LeftButton)
    assert win.ctx.pipeline.dossier and win.ctx.pipeline.dossier.id == d.id


def test_help_dialog_opens(win, qtbot):
    from PySide6.QtWidgets import QTextBrowser
    from lean_ai_station.ui.help import HelpDialog
    d = HelpDialog(win)
    qtbot.addWidget(d)
    d.show()
    assert "Pourquoi relire l'énoncé Lean" in d.findChildren(QTextBrowser)[0].toPlainText()


def test_explain_needs_a_proof(win):
    lean = win.pages["lean"]
    lean.new_dossier()
    lean.explain()
    assert "pas encore de preuve" in lean.status_title.text() and not win.ctx.pipeline.busy


def test_library_page_lists_and_removes(win, tmp_path, qtbot):
    from lean_ai_station import dossiers as ds
    lib = ds.Library(tmp_path / "lib.json")
    lib.add_proof("import Mathlib\n\ntheorem pair_x : True := trivial\n", "Vrai", "lean-prover49")
    win.ctx.pipeline.library = lib
    page = win.pages["library"]
    page.lib = lib
    win.navigate("library")
    page.fill()
    assert page.table.rowCount() == 1
    page.table.selectRow(0)
    assert "theorem pair_x" in page.code.toPlainText()
    page.remove()
    assert page.table.rowCount() == 0
    qtbot.mouseClick(win.toast.undo_btn, Qt.LeftButton)
    assert len(lib.entries) == 1


def test_language_switch_rebuilds_ui_in_english(qtbot, qapp):
    from lean_ai_station import i18n
    s = config.Settings()
    s.wizard_done = True
    ctx = AppContext(s, {})
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    holder = {"w": w}

    def rebuild(code):
        new = MainWindow(ctx)
        qtbot.addWidget(new)
        holder["w"].replaced = True
        holder["w"].close()
        holder["w"] = new
    ctx.languageChanged.connect(rebuild)
    try:
        ctx.request_language("en")
        nw = holder["w"]
        assert nw is not w and i18n.language() == "en" and ctx.settings.language == "en"
        texts = [b.text() for b in nw.nav_btns.values()]
        assert "📚  Library" in texts and "⚙  System" in texts
        assert nw.pages["lean"].new_btn.text() == "＋ New"
        assert ctx.server.state == "stopped"                       # nothing was shut down or restarted by the switch
    finally:
        i18n.set_language("fr")
        ctx.settings.language = "fr"
