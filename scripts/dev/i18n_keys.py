#!/usr/bin/env python3
"""List every French source string that must exist in the English catalogue (used by tests/test_i18n.py)."""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2] / "app" / "lean_ai_station"
sys.path.insert(0, str(ROOT.parent))


def call_keys() -> set[str]:
    keys = set()
    for f in ROOT.rglob("*.py"):
        if f.name in ("i18n_en.py",):
            continue
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_" and n.args and \
                    isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                keys.add(n.args[0].value)
    return keys


def data_keys() -> set[str]:
    from lean_ai_station import errors, examples, leancheck, texio
    from lean_ai_station.ui import home, main_window, server_page, lean_page, wizard
    keys = set()
    for e in examples.EXAMPLES:
        keys |= {e.title, e.level, e.blurb}
    for f in errors.MESSAGES.values():
        keys |= {f.title} | ({f.message} if f.message else set()) | {a for a, _ in f.actions}
    keys |= {lab for _p, lab in leancheck.FORBIDDEN} | set(texio._LABELS.values())
    keys |= {t for _k, t, _s in main_window.NAV}
    keys |= {t for t, _c in server_page.STATE_TXT.values()}
    keys |= set(lean_page.STATUS_ICON)
    src = pathlib.Path(server_page.__file__).read_text(encoding="utf-8")
    keys |= {"f16 (précis, plus de mémoire)", "q8_0 (recommandé)", "q4_0 (économe)"}
    keys |= {"Prouveur (Lean 4.9, Mathlib de Goedel)", "Lean actuel (Mathlib récent)",
             "Version exacte utilisée pour entraîner Goedel-Prover-V2 : meilleurs résultats du modèle.",
             "Lean et Mathlib récents, pour le travail quotidien et les cours."}
    for mod in (home, wizard):
        for n in ast.walk(ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))):
            if isinstance(n, ast.Tuple) and all(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in n.elts):
                for x in n.elts:
                    if " " in x.value or any(c in x.value for c in "éèà!🎉"):
                        keys.add(x.value)
    return keys


def all_keys() -> set[str]:
    return {k for k in call_keys() | data_keys() if k.strip()}


if __name__ == "__main__":
    from lean_ai_station.i18n_en import EN
    missing = sorted(all_keys() - set(EN))
    print(f"{len(all_keys())} keys, {len(missing)} missing")
    if "--dump" in sys.argv:
        import json
        print(json.dumps(missing, ensure_ascii=False, indent=0))
