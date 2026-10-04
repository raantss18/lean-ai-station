"""Update checks and installs: version logic on recorded data, install/switch/cleanup with stubbed downloads."""
import json
import sys
from pathlib import Path

import pytest

from lean_ai_station import updates

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import updater  # noqa: E402

SHA_OLD, SHA_NEW = "a" * 64, "b" * 64


def test_newest_stable_ignores_release_candidates():
    tags = ["v4.9.0", "v4.34.1", "v4.35.0-rc3", "v4.10.0", "nightly-testing-2026", "v4.34.0"]
    assert updates.newest_stable(tags) == "v4.34.1"
    assert updates.mathlib_candidate(tags, "v4.34.1") is None
    c = updates.mathlib_candidate(tags + ["v4.35.0"], "v4.34.1")
    assert c and c.latest == "v4.35.0" and c.args() == ["install-mathlib", "v4.35.0"]
    assert updates.mathlib_candidate(tags + ["v4.35.0"], "master") is None     # unpinned: never touched


def test_installed_mathlib_reads_lakefile(tmp_path):
    (tmp_path / "lakefile.toml").write_text('[[require]]\nname = "mathlib"\nrev = "v4.34.1"\n')
    assert updates.installed_mathlib(tmp_path) == "v4.34.1"
    assert updates.installed_mathlib(tmp_path / "missing") is None


def _models(d: Path, names: dict[str, str]):
    for n, sha in names.items():
        (d / n).write_bytes(b"x")
        (d / (n + ".sha256")).write_text(f"{sha}  {n}\n")


def test_installed_models_prefers_newest_q4(tmp_path):
    _models(tmp_path, {"Goedel-Prover-V2-8B.Q4_K_M.gguf": SHA_OLD, "Goedel-Prover-V2-8B.Q5_K_M.gguf": SHA_OLD,
                       "Goedel-Formalizer-V2-8B.Q4_K_M.gguf": SHA_OLD, "Qwen3-8B-Q4_K_M.gguf": SHA_OLD})
    inst = updates.installed_models(tmp_path)
    assert inst["prover"]["file"] == "Goedel-Prover-V2-8B.Q4_K_M.gguf" and inst["prover"]["sha256"] == SHA_OLD
    assert inst["formalizer"]["version"] == "2" and len(inst) == 2


def _tree(name, sha=SHA_NEW, size=5_000_000_000):
    return [{"path": "README.md"}, {"path": name, "lfs": {"oid": sha, "size": size}}]


def test_model_candidates_new_version_pending_and_revision():
    inst = {"prover": {"version": "2", "file": "Goedel-Prover-V2-8B.Q4_K_M.gguf", "sha256": SHA_OLD, "quant": "Q4_K_M"},
            "formalizer": {"version": "2", "file": "Goedel-Formalizer-V2-8B.Q4_K_M.gguf", "sha256": SHA_OLD,
                           "quant": "Q4_K_M"}}
    official = ["Goedel-LM/Goedel-Prover-V2-8B", "Goedel-LM/Goedel-Prover-V3-8B", "Goedel-LM/Goedel-Prover-V3-32B",
                "Goedel-LM/Goedel-Formalizer-V2-8B", "Goedel-LM/Goedel-Code-Prover-8B"]
    trees = {"mradermacher/Goedel-Prover-V3-8B-GGUF": _tree("Goedel-Prover-V3-8B.Q4_K_M.gguf"),
             "mradermacher/Goedel-Formalizer-V2-8B-GGUF": _tree("Goedel-Formalizer-V2-8B.Q4_K_M.gguf", SHA_OLD)}
    cands = updates.model_candidates(official, inst, trees.get)
    assert [(c.key, c.latest, c.installable) for c in cands] == [("prover", "V3", True)]
    assert cands[0].args() == ["install-model", "mradermacher/Goedel-Prover-V3-8B-GGUF",
                               "Goedel-Prover-V3-8B.Q4_K_M.gguf", SHA_NEW, "prover"]
    # no GGUF yet: notified but not installable
    cands = updates.model_candidates(official, inst, lambda r: None)
    assert [(c.key, c.installable, c.note) for c in cands] == [("prover", False, "gguf_pending")]
    # same version re-published with another checksum
    trees["mradermacher/Goedel-Formalizer-V2-8B-GGUF"] = _tree("Goedel-Formalizer-V2-8B.Q4_K_M.gguf", SHA_NEW)
    cands = updates.model_candidates(official[:1] + official[3:], inst, trees.get)
    assert [(c.key, c.note) for c in cands] == [("formalizer", "revision")]


def test_state_roundtrip_and_due(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "STATE_FILE", tmp_path / "u.json")
    c = updates.Candidate("mathlib", "mathlib", "Lean + Mathlib v4.35.0", "v4.34.1", "v4.35.0", spec={"tag": "v4.35.0"})
    updates.save_state({"t": 1000.0, "candidates": [c.__dict__], "errors": []})
    st = updates.load_state()
    assert updates.candidates_from(st)[0].latest == "v4.35.0"
    assert updates.due(st, now=1000.0 + updates.CHECK_EVERY_S) and not updates.due(st, now=2000.0)


# ---------------------------------------------------------------- installer (network stubbed)
def test_install_model_switches_then_deletes_obsolete_versions(tmp_path, monkeypatch):
    _models(tmp_path, {"Goedel-Prover-V2-8B.Q4_K_M.gguf": SHA_OLD, "Goedel-Prover-V2-8B.Q5_K_M.gguf": SHA_OLD,
                       "Goedel-Formalizer-V2-8B.Q4_K_M.gguf": SHA_OLD})
    new = "Goedel-Prover-V3-8B.Q4_K_M.gguf"

    def fake_download(cmd, **_k):
        dest = Path(cmd[3])
        dest.write_bytes(b"new")
        dest.with_name(dest.name + ".sha256").write_text(f"{cmd[4]}  {dest.name}\n")
        return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(updater.subprocess, "run", fake_download)
    monkeypatch.setattr(updater, "smoke_test", lambda p: None)
    monkeypatch.setattr(updater, "free_gb", lambda p: 100.0)
    res = updater.install_model("mradermacher/Goedel-Prover-V3-8B-GGUF", new, SHA_NEW, "prover", tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["Goedel-Formalizer-V2-8B.Q4_K_M.gguf", "Goedel-Formalizer-V2-8B.Q4_K_M.gguf.sha256",
                     new, new + ".sha256"]
    assert sorted(res["removed"]) == ["Goedel-Prover-V2-8B.Q4_K_M.gguf", "Goedel-Prover-V2-8B.Q5_K_M.gguf"]


def test_install_model_failed_smoke_test_keeps_old(tmp_path, monkeypatch):
    _models(tmp_path, {"Goedel-Prover-V2-8B.Q4_K_M.gguf": SHA_OLD})

    def fake_download(cmd, **_k):
        Path(cmd[3]).write_bytes(b"broken")
        Path(cmd[3] + ".sha256").write_text("x")
        return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(updater.subprocess, "run", fake_download)
    monkeypatch.setattr(updater, "free_gb", lambda p: 100.0)

    def bad(p):
        raise updater.Fail("smoke", "unknown architecture")
    monkeypatch.setattr(updater, "smoke_test", bad)
    with pytest.raises(updater.Fail):
        updater.install_model("r/x", "Goedel-Prover-V3-8B.Q4_K_M.gguf", SHA_NEW, "prover", tmp_path)
    assert (tmp_path / "Goedel-Prover-V2-8B.Q4_K_M.gguf").exists()
    assert not (tmp_path / "Goedel-Prover-V3-8B.Q4_K_M.gguf").exists()


def test_install_model_rejects_bad_arguments(tmp_path):
    with pytest.raises(updater.Fail):
        updater.install_model("r/x", "../../etc/passwd", SHA_NEW, "prover", tmp_path)
    with pytest.raises(updater.Fail):
        updater.install_model("r/x", "Goedel-Prover-V3-8B.Q4_K_M.gguf", "nothex", "prover", tmp_path)


def _ws(root: Path, rev="v4.34.1", tc="leanprover/lean4:v4.34.1"):
    cur = root / "lean-current"
    cur.mkdir(parents=True)
    (cur / "lakefile.toml").write_text(f'[[require]]\nname = "mathlib"\nrev = "{rev}"\n')
    (cur / "lean-toolchain").write_text(tc + "\n")
    return cur


def test_install_mathlib_switch_and_cleanup(tmp_path, monkeypatch):
    cur = _ws(tmp_path)

    def build(tag, cur_, nxt):
        nxt.mkdir()
        (nxt / "lakefile.toml").write_text(f'rev = "{tag}"\n')
        return "leanprover/lean4:v4.35.0"
    calls = []
    monkeypatch.setattr(updater, "_build_next", build)
    monkeypatch.setattr(updater, "free_gb", lambda p: 100.0)
    monkeypatch.setattr(updater, "elan_default", lambda: "stable")
    monkeypatch.setattr(updater, "toolchain_users", lambda tc, ignore: [])
    monkeypatch.setattr(updater, "run", lambda cmd, key, **k: calls.append(cmd) or "")
    res = updater.install_mathlib("v4.35.0", tmp_path)
    assert updates.installed_mathlib(cur) == "v4.35.0"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["lean-current"]
    assert calls and calls[0][-3:] == ["toolchain", "uninstall", "leanprover/lean4:v4.34.1"]
    assert res["removed"] == ["Mathlib v4.34.1", "Lean v4.34.1"]


def test_install_mathlib_keeps_toolchain_used_by_a_project(tmp_path, monkeypatch):
    _ws(tmp_path)
    monkeypatch.setattr(updater, "_build_next", lambda t, c, n: n.mkdir() or "leanprover/lean4:v4.35.0")
    monkeypatch.setattr(updater, "free_gb", lambda p: 100.0)
    monkeypatch.setattr(updater, "elan_default", lambda: "stable")
    monkeypatch.setattr(updater, "toolchain_users", lambda tc, ignore: [Path("/home/u/GitHub/project")])
    monkeypatch.setattr(updater, "run", lambda *a, **k: pytest.fail("must not uninstall"))
    assert updater.install_mathlib("v4.35.0", tmp_path)["removed"] == ["Mathlib v4.34.1"]


def test_install_mathlib_failure_leaves_old_version(tmp_path, monkeypatch):
    cur = _ws(tmp_path)

    def build(tag, cur_, nxt):
        nxt.mkdir()
        raise updater.Fail("verify", "error: unknown identifier")
    monkeypatch.setattr(updater, "_build_next", build)
    monkeypatch.setattr(updater, "free_gb", lambda p: 100.0)
    with pytest.raises(updater.Fail):
        updater.install_mathlib("v4.35.0", tmp_path)
    assert updates.installed_mathlib(cur) == "v4.34.1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["lean-current"]


def test_install_mathlib_refuses_low_disk_and_bad_tag(tmp_path, monkeypatch):
    _ws(tmp_path)
    with pytest.raises(updater.Fail):
        updater.install_mathlib("master; rm -rf ~", tmp_path)
    monkeypatch.setattr(updater, "free_gb", lambda p: 3.0)
    with pytest.raises(updater.Fail) as e:
        updater.install_mathlib("v4.35.0", tmp_path)
    assert e.value.key == "disk"


def test_toolchain_users_scans_projects(tmp_path):
    for d, tc in (("GitHub/a", "leanprover/lean4:v4.29.1"), ("Documents/b", "leanprover/lean4:v4.34.1"),
                  ("station/workspaces/lean-current", "leanprover/lean4:v4.34.1"),
                  ("GitHub/a/.lake/packages/x", "leanprover/lean4:v4.34.1")):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "lean-toolchain").write_text(tc + "\n")
    users = updater.toolchain_users("leanprover/lean4:v4.34.1", ignore=[tmp_path / "station/workspaces/lean-current"],
                                    home=tmp_path)
    assert users == [tmp_path / "Documents/b"]


def test_cli_reports_failure_protocol(capsys):
    assert updater.main(["install-mathlib", "not-a-tag"]) == 1
    assert capsys.readouterr().out.startswith("FAIL bad_tag")


# ---------------------------------------------------------------- GUI
@pytest.fixture
def ctx_win(qtbot, qapp):
    from lean_ai_station import config
    from lean_ai_station.ui.context import AppContext
    from lean_ai_station.ui.main_window import MainWindow
    s = config.Settings()
    s.wizard_done = True
    ctx = AppContext(s, {})
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    return ctx, w


def test_card_lists_updates_and_reports_install(ctx_win, monkeypatch, tmp_path):
    ctx, w = ctx_win
    monkeypatch.setattr(updates, "STATE_FILE", tmp_path / "u.json")
    c = updates.Candidate("mathlib", "mathlib", "Lean + Mathlib v4.35.0", "v4.34.1", "v4.35.0", spec={"tag": "v4.35.0"})
    shown = []
    ctx.banner.connect(lambda f, d: shown.append(f))
    monkeypatch.setattr("shutil.which", lambda n: None)          # no desktop notification during tests
    m = ctx.updates
    m._checked({"t": 1.0e9, "candidates": [c.__dict__], "errors": []}, manual=False)
    assert shown and shown[-1].actions == [(shown[-1].actions[0][0], "goto_system")]
    card = w.pages["system"].updates_card
    texts = [card.list.itemAt(i).widget().findChildren(type(card.status))[0].text() for i in range(card.list.count())]
    assert any("v4.35.0" in t for t in texts)
    # same candidate again: no second notification
    n = len(shown)
    m._checked({"t": 1.0e9 + 5, "candidates": [c.__dict__], "errors": []}, manual=False)
    assert len(shown) == n
    # a finished install removes it from the list and reports what was deleted
    m.current, m.proc = c, None
    m.output = ["STEP cleanup", "DONE " + json.dumps({"kind": "mathlib", "removed": ["Mathlib v4.34.1"]})]
    monkeypatch.setattr(ctx, "refresh_workspaces", lambda then=None: None)
    m._finished(0, None)
    assert not m.candidates and shown[-1].level == "ok" and "v4.34.1" in shown[-1].message
    # a failed one keeps the old version and says so
    m.current = c
    m.output = ["STEP verify", "FAIL verify error"]
    m.step = "verify"
    m._finished(1, None)
    assert shown[-1].level == "warn"


def test_install_runs_the_updater_process(ctx_win, qtbot, monkeypatch, tmp_path):
    """Real QProcess round trip: the updater refuses a bad tag, the GUI reports it and keeps the old version."""
    ctx, _w = ctx_win
    monkeypatch.setattr(updates, "STATE_FILE", tmp_path / "u.json")
    shown = []
    ctx.banner.connect(lambda f, d: shown.append((f, d)))
    bad = updates.Candidate("mathlib", "mathlib", "Lean + Mathlib vX", "v4.34.1", "vX", spec={"tag": "vX"})
    ctx.updates.install(bad)
    assert ctx.updates.busy
    qtbot.waitUntil(lambda: not ctx.updates.busy, timeout=30000)
    f, details = shown[-1]
    assert f.level == "warn" and "FAIL bad_tag" in details
