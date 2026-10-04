"""Beginner help: what Lean is, how this tool works, a small glossary. Plain French, no prerequisites."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QTextBrowser, QVBoxLayout

from . import theme
from .widgets import button

HTML = f"""
<style>
 body {{ color:{theme.TEXT}; }} h2 {{ color:{theme.ACCENT_H}; margin-top:18px; }} code {{ background:{theme.PANEL2}; padding:1px 4px; }}
 td {{ padding:3px 12px 3px 0; vertical-align:top; }} .m {{ color:{theme.MUTED}; }}
</style>
<h1>Lean AI Station en deux minutes</h1>
<p><b>Lean</b> est un logiciel de vérification de preuves mathématiques. On lui donne un énoncé et une preuve, écrits
dans un langage très précis ; Lean contrôle chaque ligne et répond simplement : <i>« correct »</i> ou <i>« voici
l'erreur »</i>. Une preuve acceptée par Lean est donc fiable.</p>
<p>Écrire ce langage demande de l'entraînement. Cet outil confie ce travail à une <b>IA</b> (Goedel-Prover), puis fait
vérifier le résultat par Lean. L'IA peut se tromper ; <b>Lean, lui, ne se trompe pas</b> : c'est lui qui décide.</p>

<h2>Comment faire</h2>
<ol>
<li><b>Décrivez votre problème</b> avec vos mots, en français ou en anglais (formules LaTeX acceptées), ou importez
un fichier <code>.tex</code> pour reprendre un théorème, un lemme ou un exercice.</li>
<li>Cliquez sur <b>« Traduire en Lean »</b>. L'IA écrit l'énoncé en Lean et Lean vérifie qu'il est valide.
<b>Relisez-le</b> : c'est le seul moment où votre jugement est indispensable (voir plus bas).</li>
<li>Cliquez sur <b>« C'est bon : prouver »</b>. L'IA cherche une preuve ; si Lean la refuse, elle lit ses
erreurs, corrige et réessaie (jusqu'à 8 fois par défaut).</li>
<li>Quand Lean accepte, cliquez sur <b>« 💬 Expliquer en français »</b> : une IA raconte la preuve en langage courant,
étape par étape. Vous pouvez aussi <b>copier</b>, <b>enregistrer</b> ou <b>exporter en LaTeX</b> (compatible Overleaf, explication incluse).</li>
</ol>

<h2>Pourquoi relire la traduction ?</h2>
<p>Lean prouve exactement ce qu'on lui demande. Si la traduction oublie une hypothèse (par exemple « <i>n</i> positif »)
ou change un nombre, Lean prouvera <i>un autre énoncé</i> — correctement, mais ce ne sera pas le vôtre. Vérifiez
les hypothèses, les nombres et la conclusion dans la zone ②.</p>

<h2>À quoi ressemble un énoncé Lean ?</h2>
<p><code>theorem somme_pairs (a b : ℕ) (ha : Even a) (hb : Even b) : Even (a + b) := by sorry</code></p>
<table>
<tr><td><code>theorem nom</code></td><td class="m">« théorème », avec un nom de votre choix</td></tr>
<tr><td><code>(a b : ℕ)</code></td><td class="m">« soient a et b deux entiers naturels »</td></tr>
<tr><td><code>(ha : Even a)</code></td><td class="m">hypothèse : « a est pair » (ha est son nom)</td></tr>
<tr><td><code>: Even (a + b)</code></td><td class="m">ce qu'on veut démontrer : « a + b est pair »</td></tr>
<tr><td><code>:= by sorry</code></td><td class="m">« la preuve va ici » ; <code>sorry</code> signifie <i>à compléter</i>. L'IA le remplace par une vraie preuve.</td></tr>
</table>

<h2>Petit lexique</h2>
<table>
<tr><td><b>Mathlib</b></td><td class="m">la grande bibliothèque de résultats mathématiques que Lean connaît (algèbre, analyse, nombres…)</td></tr>
<tr><td><b>Tactique</b></td><td class="m">une étape de preuve : <code>linarith</code> (inégalités), <code>ring</code> (calcul), <code>simp</code> (simplifier), <code>induction</code>…</td></tr>
<tr><td><b>ℝ ℕ ℤ ℚ</b></td><td class="m">réels, entiers naturels, entiers relatifs, rationnels. Tapez <code>\\R</code> + Espace pour ℝ, <code>\\N</code> pour ℕ, <code>\\le</code> pour ≤.</td></tr>
<tr><td><b>∀ ∃ → ∧ ∨ ¬</b></td><td class="m">pour tout, il existe, implique, et, ou, non</td></tr>
<tr><td><b>Essai</b></td><td class="m">une tentative de l'IA : preuve écrite, puis vérifiée par Lean ; en cas d'échec, l'essai suivant tient compte des erreurs</td></tr>
<tr><td><b>Token</b></td><td class="m">un morceau de mot (≈ ¾ de mot) ; « tokens/s » mesure la vitesse d'écriture de l'IA</td></tr>
</table>

<h2>Limites à connaître</h2>
<ul>
<li>L'IA réussit bien les exercices de lycée et de licence ; les problèmes d'olympiade échouent souvent.</li>
<li>« Je n'ai pas trouvé de preuve » ne veut pas dire que l'énoncé est faux.</li>
<li>L'explication en français est écrite par une IA : elle peut être maladroite. La preuve Lean, elle, est vérifiée.</li>
<li>Vous avez déjà une preuve Lean (écrite à la main ou copiée) ? Collez-la dans la zone ② et cliquez sur « 💬 Expliquer ».</li>
<li>Tout fonctionne sans Internet : rien n'est envoyé à l'extérieur.</li>
</ul>
"""


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Aide — Lean pour les débutants")
        self.resize(820, 700)
        lay = QVBoxLayout(self)
        view = QTextBrowser()
        view.setHtml(HTML)
        view.setOpenExternalLinks(True)
        lay.addWidget(view)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Fermer", "Primary", slot=self.accept))
        lay.addLayout(row)
