# Changelog

## 1.0.0 — 2026-10-04 (première version)

**Fonctions**
- Décrire un problème en français/anglais (LaTeX accepté) ou importer un `.tex` → traduction en énoncé Lean
  (Goedel-Formalizer-V2-8B) contrôlée par Lean, relecture obligatoire, puis recherche de preuve avec auto-correction
  (Goedel-Prover-V2-8B). Une preuve n'est acceptée que si Lean l'accepte sans `sorry`/`admit`, avec les axiomes
  standard seulement, pour l'énoncé exact relu.
- Explication d'une preuve Lean en français (Qwen3-8B) ; marche aussi sur une preuve collée.
- Export LaTeX (XeLaTeX, compatible Overleaf) avec énoncé, version Lean, explication et preuve ; import `.tex`.
- Vérification d'un fichier `.lean` (glisser-déposer). Assistant de premier démarrage, aide « Lean pour débutants ».
- Garde-fous : détection de boucles de génération, repli automatique en cas de mémoire graphique insuffisante,
  récupération après arrêt brutal, aucun processus orphelin, mode hors-ligne qui bloque le réseau de l'application.
- Deux espaces Lean : Lean 4.9 + Mathlib de Goedel (compilé depuis les sources) et Lean récent.

**Installation**
- `install.sh` multi-distributions (Arch, Fedora, Debian/Ubuntu, openSUSE), moteur CUDA ou processeur seul,
  versions et modèles épinglés avec SHA-256 ; `export_offline.sh` / `uninstall.sh`.

**Limites connues** : voir `ACCEPTANCE.md` (section « Not verified / known limits ») et le README.
