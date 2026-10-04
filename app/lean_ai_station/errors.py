"""Plain-French messages: what happened + what to click next. Raw details go behind « Afficher les détails »."""
from __future__ import annotations

from dataclasses import dataclass, field

from .i18n import _


@dataclass
class Friendly:
    title: str
    message: str
    actions: list[tuple[str, str]] = field(default_factory=list)   # (button label, action id)
    level: str = "error"                                            # error | warn | info | ok


MESSAGES = {
    "server_crashed": Friendly(
        "Le moteur d'IA s'est arrêté",
        "Le serveur du modèle s'est fermé de façon inattendue. Cliquez sur « Redémarrer le modèle » pour le relancer ; "
        "votre travail n'est pas perdu.",
        [("Redémarrer le modèle", "restart_server")]),
    "server_down": Friendly(
        "Le modèle n'est pas chargé",
        "Il faut un modèle chargé pour générer une preuve. Cliquez sur « Charger le modèle » puis relancez.",
        [("Charger le modèle", "restart_server")]),
    "oom": Friendly(
        "Mémoire de la carte graphique insuffisante",
        "Le modèle ne tient pas sur la carte graphique, même après réduction automatique. Fermez les jeux ou "
        "logiciels qui utilisent la carte, ou choisissez un modèle plus petit (Q4_K_M) dans « Modèles ».",
        [("Réessayer", "restart_server"), ("Choisir un modèle", "goto_models")], "error"),
    "bad_model": Friendly(
        "Fichier de modèle illisible",
        "Le fichier du modèle est absent, incomplet ou abîmé. Choisissez un autre modèle dans « Modèles » "
        "ou retéléchargez-le.",
        [("Choisir un modèle", "goto_models")]),
    "no_binary": Friendly(
        "Moteur d'IA introuvable",
        "Le programme llama-server est introuvable. Ouvrez « Système » et cliquez sur « Recompiler ».",
        [("Ouvrir Système", "goto_system")]),
    "port": Friendly(
        "Port réseau occupé",
        "Aucun port local libre n'a été trouvé pour le serveur. Changez le port dans « Serveur ».",
        [("Ouvrir Serveur", "goto_server")]),
    "timeout": Friendly(
        "Le modèle met trop de temps à se charger",
        "Le chargement a dépassé 5 minutes. Réessayez ; si cela se reproduit, choisissez un modèle plus petit.",
        [("Réessayer", "restart_server")]),
    "workspace": Friendly(
        "L'espace de travail Lean n'est pas prêt",
        "Lean ne trouve pas Mathlib dans l'espace choisi. Choisissez un autre espace en haut de l'onglet Lean, "
        "ou ouvrez « Système » pour le réparer.",
        [("Ouvrir Système", "goto_system")]),
    "generation": Friendly(
        "La génération a échoué",
        "Le modèle a renvoyé une erreur. Réessayez ; si cela continue, redémarrez le modèle.",
        [("Redémarrer le modèle", "restart_server")]),
    "no_gpu": Friendly(
        "Carte graphique non détectée",
        "La carte NVIDIA n'est pas disponible : le modèle fonctionnera sur le processeur, beaucoup plus lentement. "
        "Redémarrer l'ordinateur règle souvent ce problème.",
        [], "warn"),
    "disk_low": Friendly(
        "Disque presque plein",
        "Il reste peu d'espace disque. Libérez de la place avant de télécharger ou d'importer un modèle.",
        [("Ouvrir Système", "goto_system")], "warn"),
    "crash_recovered": Friendly(
        "Session restaurée",
        "L'application s'était fermée brutalement. Votre texte et vos réglages ont été restaurés.",
        [], "info"),
    "offline_blocked": Friendly(
        "Mode hors-ligne actif",
        "Le téléchargement est désactivé en mode hors-ligne. Désactivez-le dans « Système » si vous avez Internet.",
        [("Ouvrir Système", "goto_system")], "info"),
    "no_formalizer": Friendly(
        "Le traducteur français → Lean n'est pas installé",
        "Pour écrire votre problème en français, il faut le modèle « Goedel-Formalizer » (≈ 5 Go). "
        "Avec Internet : ouvrez « Modèles » → téléchargez « mradermacher/Goedel-Formalizer-V2-8B-GGUF ». "
        "Sinon, écrivez directement l'énoncé en Lean dans l'onglet « Énoncé Lean ».",
        [("Ouvrir Modèles", "goto_models")], "warn"),
    "no_explainer": Friendly(
        "Le modèle d'explication n'est pas installé",
        "Pour expliquer une preuve en français, il faut le modèle « Qwen3-8B » (≈ 5 Go). Avec Internet : ouvrez "
        "« Modèles » → téléchargez « Qwen/Qwen3-8B-GGUF » (fichier Qwen3-8B-Q4_K_M.gguf).",
        [("Ouvrir Modèles", "goto_models")], "warn"),
    "statement": Friendly(
        "Énoncé incomplet",
        "", [], "warn"),
}


def friendly(kind: str, extra: str = "") -> Friendly:
    """Messages are translated when they are shown (the interface language can change at run time)."""
    f = MESSAGES.get(kind) or Friendly("Une erreur est survenue", "Réessayez. Si le problème persiste, ouvrez « Système ».")
    actions = [(_(label), act) for label, act in f.actions]
    if extra and not f.message:
        return Friendly(_(f.title), extra, actions, f.level)
    return Friendly(_(f.title), _(f.message), actions, f.level)
