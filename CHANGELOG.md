# Changelog

## 1.1.4 — 2026-10-04

**Corrections** (signalées : « il fait toujours la réciproque », « unknown constant qui se répète »)
- **Sens de l'implication** : « un carré pair est issu d'un entier pair » était compris à l'envers (« le carré d'un
  entier pair est pair »). La consigne de l'étape « comprendre » impose maintenant de repérer ce qui est supposé et ce
  qui est à conclure, et l'énoncé compris est **affiché en français** dans le fil (l'anglais reste pour le traducteur).
- **Les demandes de suivi sont lues par l'IA** (Qwen3) au lieu d'un tri par mots-clés : « et la réciproque », « j'ai
  dit : si n² est pair alors n est pair » produisent un **nouvel énoncé** (affiché, retraduit, prouvé, expliqué) au lieu
  de reprouver le même théorème. « Preuve plus courte », « réessaie avec… » relancent la preuve ; « explique… »
  l'explication. Les mots-clés restent en secours (réponse inutilisable ou modèle absent).
- **Noms inventés qui reviennent à chaque essai** : les noms refusés par Lean pendant une recherche sont maintenant
  rappelés dans chaque nouvel essai (« ces noms n'existent pas… », avec les vrais noms proches), au lieu d'être oubliés
  à chaque nouveau départ ; si le modèle réutilise quand même un nom refusé, l'outil abandonne cette piste et repart
  de zéro avec l'avertissement. Le message du Lean récent (`Unknown identifier` avec des accents graves) est aussi reconnu.

## 1.1.3 — 2026-10-04

**Amélioration** (demandée après la 1.1.2)
- **Lemmes inventés** : quand Lean répond qu'un nom n'existe pas (`unknown constant`, `unknown identifier`, ou
  « invalid field notation » pour `Real.…`, `Nat.…` en Lean 4.9), l'outil cherche dans la Mathlib de l'espace de
  travail les déclarations aux noms proches et les donne au prouveur avec leur énoncé, dans le message de correction.
  Exemple réel : `Polynomial.exists_root_of_odd_degree` → `Polynomial.exists_root_of_degree_eq_one`,
  `Polynomial.exists_root_of_splits`… L'index (≈ 164 000 déclarations pour Lean 4.9, ≈ 275 000 pour le Lean actuel)
  se construit en arrière-plan en 5 à 10 s la première fois, puis se recharge en 1 à 2 s.

## 1.1.2 — 2026-10-04

**Corrections** (signalées : « les 8 essais reproduisent la même erreur », sur deux dossiers)
- **Les essais s'enlisaient sur la même piste** : chaque essai corrigeait le précédent, jusqu'à 7 corrections d'affilée
  de la même idée, et la conversation grossissait au point de ne laisser que ≈ 1 500 tokens de réponse (preuve coupée,
  `sorry`, même erreur à chaque essai). Désormais : au plus 2 corrections par piste puis un nouvel essai depuis zéro
  (comme Goedel-Prover-V2), nouveau départ immédiat si Lean renvoie exactement la même erreur, et au moins
  8 192 tokens garantis par réponse.
- **Les demandes après un échec n'atteignaient pas le prouveur** : « réessaie la preuve », « en utilisant les
  propriétés algébriques… » étaient envoyées au traducteur (énoncé retraduit à l'identique) et le prouveur ne voyait
  jamais l'indication. Elles relancent maintenant la recherche de preuve, avec la demande transmise au prouveur comme
  indice ; seules les demandes qui portent sur l'énoncé (« ajoute l'hypothèse… ») le font retraduire.

## 1.1.1 — 2026-10-04

**Corrections** (signalées avec une vidéo : « théorème de la base incomplète »)
- **Mauvaise traduction d'un théorème cité par son nom** : le traducteur ne recevait que le nom et a formalisé un
  autre résultat (« une famille de plus de dim V vecteurs est liée »). Nouvelle étape **« comprendre »** avant la
  traduction : l'IA généraliste reformule la demande en un énoncé mathématique précis et complet (affiché dans le fil :
  « Problème compris ainsi »), et c'est ce texte que le traducteur met en Lean. Si la demande ne désigne aucun résultat
  connu, l'outil s'arrête et demande l'énoncé au lieu d'en inventer un.
- **Le prouveur tournait en rond sans être arrêté** : il recyclait les mêmes paragraphes avec de petites variations
  (le détecteur ne voyait que les répétitions exactes), jusqu'à la limite de 16 384 tokens (≈ 6 min par essai).
  Nouveau détecteur de raisonnement circulaire, réglé sur de vraies réponses du modèle : il laisse au modèle le temps
  de sortir seul d'une hésitation (cas observé qui finit par une preuve correcte) et coupe au-delà (≈ 6 000 tokens),
  puis passe à l'essai suivant.
- Le détecteur de répétitions exactes (depuis la 1.0) pouvait arrêter une **preuve correcte** qui répète plusieurs
  fois le même bloc de tactiques (cas réel accepté par Lean) : il est désormais plus exigeant à l'intérieur du code.

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
