# Changelog

## 1.1.0 — 2026-10-04

**Nouveautés**
- **Dossiers et fil de discussion** : chaque preuve devient un dossier. Après le résultat, on écrit simplement une demande
  (« ajoute l'hypothèse n > 0 », « preuve plus courte », « explique l'étape 2 ») ; l'outil devine l'étape à refaire
  (énoncé, preuve ou explication), modifiable à la main, et garde toutes les versions (« Revenir à cette version »).
  Les dossiers sont listés, renommables, supprimables (annulable) et rouverts plus tard.
- **Enchaînement entièrement automatique** traduire → prouver → expliquer, avec changement automatique du modèle
  (traducteur, prouveur, explicateur). Option « Pause pour relire l'énoncé ».
- **Mémoire** : profil personnel lu par le traducteur et l'explicateur ; historique du dossier pour les corrections ;
  **bibliothèque** des résultats prouvés, réutilisés comme lemmes quand un nouveau problème leur ressemble (onglet dédié).
- **Interface en anglais** (bouton 🌐 ou Système) : interface, aide, exemples et langue des explications.
- **Mises à jour de Lean/Mathlib et des modèles Goedel** : vérification chaque semaine au démarrage (GitHub et
  Hugging Face uniquement, désactivable), notification (bandeau + notification du bureau), installation en un clic.
  La nouvelle version est installée à côté de l'ancienne et vérifiée (théorème test pour Mathlib, chargement et réponse
  pour un modèle), puis l'ancienne est **supprimée automatiquement** ; une version de Lean encore utilisée par un de vos
  projets est conservée. En cas d'échec, rien n'est changé. Aussi en ligne de commande : `scripts/updater.py check`.
- Onglet Chat retiré (le prouveur n'est pas un modèle de discussion).

**Corrections**
- Une vérification Lean relancée juste après « Arrêter » pouvait planter ou reprendre un résultat périmé (numéros de tâche).
- Les signaux d'une génération annulée pouvaient perturber la suivante.


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
