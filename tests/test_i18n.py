"""Every French string shown by the application has an English translation with the same placeholders."""
import importlib.util
import re
from pathlib import Path

from lean_ai_station import i18n
from lean_ai_station.i18n_en import EN

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("i18n_keys", ROOT / "scripts" / "dev" / "i18n_keys.py")
keys_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keys_mod)


def test_every_visible_string_is_translated():
    missing = sorted(keys_mod.all_keys() - set(EN))
    assert not missing, f"{len(missing)} untranslated: {missing[:10]}"


def test_placeholders_match():
    bad = [k for k, v in EN.items() if sorted(re.findall(r"\{[^}]*\}", k)) != sorted(re.findall(r"\{[^}]*\}", v))]
    assert not bad, bad


def test_no_french_left_in_english_catalogue():
    french = re.compile(r"[éèàùçêâîôû]|(?<!\\)\b(le|la|les|une|des|est|pas|vous|votre|avec|pour)\b", re.I)
    allowed = {"Goedel-Formalizer", "mradermacher"}
    bad = [v for v in EN.values() if french.search(v) and not any(a in v for a in allowed)]
    assert not bad, bad[:10]


def test_switch_and_fallback():
    try:
        i18n.set_language("en")
        assert i18n._("Prêt") == "Ready" and i18n._("texte inconnu") == "texte inconnu"
        assert i18n._("Lean a trouvé {n} erreurs.").format(n=3) == "Lean found 3 errors."
        i18n.set_language("xx")
        assert i18n.language() == "fr"
    finally:
        i18n.set_language("fr")
