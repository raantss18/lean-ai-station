"""Beginner help: what Lean is, how this tool works, a small glossary. French and English versions."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QTextBrowser, QVBoxLayout

from ..i18n import _, pick
from . import theme
from .widgets import button

STYLE = f"""
<style>
 body {{ color:{theme.TEXT}; }} h2 {{ color:{theme.ACCENT_H}; margin-top:18px; }} code {{ background:{theme.PANEL2}; padding:1px 4px; }}
 td {{ padding:3px 12px 3px 0; vertical-align:top; }} .m {{ color:{theme.MUTED}; }}
</style>"""

HTML_FR = STYLE + """
<h1>Lean AI Station en deux minutes</h1>
<p><b>Lean</b> est un logiciel de vérification de preuves mathématiques. On lui donne un énoncé et une preuve, écrits
dans un langage très précis ; Lean contrôle chaque ligne et répond simplement : <i>« correct »</i> ou <i>« voici
l'erreur »</i>. Une preuve acceptée par Lean est donc fiable.</p>
<p>Écrire ce langage demande de l'entraînement. Cet outil confie ce travail à trois <b>IA</b>, chacune spécialisée, et
fait vérifier le résultat par Lean. L'IA peut se tromper ; <b>Lean, lui, ne se trompe pas</b> : c'est lui qui décide.</p>

<h2>Comment faire</h2>
<ol>
<li><b>Écrivez votre problème</b> avec vos mots, en français ou en anglais (formules LaTeX acceptées), ou importez un
fichier <code>.tex</code>. Cliquez sur <b>« Prouver »</b>.</li>
<li>Tout s'enchaîne seul : l'IA <b>traduit</b> le problème en Lean, une autre IA <b>cherche une preuve</b> (si Lean la
refuse, elle lit l'erreur et corrige), puis une troisième l'<b>explique</b> en langage courant. Le bon modèle est chargé
automatiquement à chaque étape (quelques secondes).</li>
<li>Le résultat reste dans un <b>dossier</b> : vous pouvez ensuite écrire une demande, comme dans une discussion :
« ajoute l'hypothèse n &gt; 0 », « une preuve plus courte », « explique l'étape 2 ». L'outil devine quelle étape refaire
et garde toutes les versions (lien « Revenir à cette version »).</li>
<li>Exportez : copie, fichier <code>.lean</code>, ou document <b>LaTeX</b> pour Overleaf (énoncé, preuve, explication).</li>
</ol>

<h2>Pourquoi relire l'énoncé Lean ?</h2>
<p>Lean prouve exactement ce qu'on lui demande. Si la traduction oublie une hypothèse (par exemple « <i>n</i> positif »)
ou change un nombre, Lean prouvera <i>un autre énoncé</i> — correctement, mais ce ne sera pas le vôtre. L'énoncé est
affiché dans le fil et dans l'onglet « Énoncé Lean » : jetez-y un œil, et cliquez sur « Corriger l'énoncé… » au besoin.
(Option : « ⚙ Options → Pause pour relire l'énoncé » arrête l'enchaînement après la traduction.)</p>

<h2>Mémoire</h2>
<ul>
<li><b>Votre profil</b> (onglet Système) : votre public, vos notations, vos habitudes. Il est lu avant chaque traduction
et chaque explication.</li>
<li><b>Le fil du dossier</b> : chaque demande de correction s'appuie sur les versions précédentes.</li>
<li><b>La bibliothèque</b> : chaque résultat prouvé y est rangé et proposé comme lemme pour les preuves suivantes
(même version de Lean).</li>
</ul>

<h2>À quoi ressemble un énoncé Lean ?</h2>
<p><code>theorem somme_pairs (a b : ℕ) (ha : Even a) (hb : Even b) : Even (a + b) := by sorry</code></p>
<table>
<tr><td><code>theorem nom</code></td><td class="m">« théorème », avec un nom</td></tr>
<tr><td><code>(a b : ℕ)</code></td><td class="m">« soient a et b deux entiers naturels »</td></tr>
<tr><td><code>(ha : Even a)</code></td><td class="m">hypothèse : « a est pair » (ha est son nom)</td></tr>
<tr><td><code>: Even (a + b)</code></td><td class="m">ce qu'on veut démontrer : « a + b est pair »</td></tr>
<tr><td><code>:= by sorry</code></td><td class="m">« la preuve va ici » ; <code>sorry</code> signifie <i>à compléter</i>.</td></tr>
</table>

<h2>Petit lexique</h2>
<table>
<tr><td><b>Mathlib</b></td><td class="m">la grande bibliothèque de résultats mathématiques que Lean connaît</td></tr>
<tr><td><b>Tactique</b></td><td class="m">une étape de preuve : <code>linarith</code> (inégalités), <code>ring</code> (calcul), <code>simp</code> (simplifier), <code>induction</code>…</td></tr>
<tr><td><b>ℝ ℕ ℤ ℚ</b></td><td class="m">réels, entiers naturels, relatifs, rationnels. Tapez <code>\\R</code> + Espace pour ℝ, <code>\\le</code> pour ≤.</td></tr>
<tr><td><b>∀ ∃ → ∧ ∨ ¬</b></td><td class="m">pour tout, il existe, implique, et, ou, non</td></tr>
<tr><td><b>Essai</b></td><td class="m">une tentative de l'IA, vérifiée par Lean ; en cas d'échec, l'essai suivant tient compte des erreurs</td></tr>
<tr><td><b>Token</b></td><td class="m">un morceau de mot (≈ ¾ de mot) ; « tokens/s » mesure la vitesse d'écriture de l'IA</td></tr>
</table>

<h2>Limites à connaître</h2>
<ul>
<li>L'IA réussit bien les exercices de lycée et de licence ; les problèmes d'olympiade échouent souvent.</li>
<li>« Pas de preuve trouvée » ne veut pas dire que l'énoncé est faux.</li>
<li>L'explication est écrite par une IA : elle peut être maladroite. La preuve Lean, elle, est vérifiée.</li>
<li>Rien n'est envoyé sur Internet. Seule la recherche hebdomadaire de mises à jour (Lean, modèles Goedel) contacte
GitHub et Hugging Face ; elle se désactive dans l'onglet Système.</li>
</ul>
"""

HTML_EN = STYLE + """
<h1>Lean AI Station in two minutes</h1>
<p><b>Lean</b> is a program that checks mathematical proofs. You give it a statement and a proof written in a very
precise language; Lean checks every line and answers simply: <i>“correct”</i> or <i>“here is the error”</i>. A proof
accepted by Lean can therefore be trusted.</p>
<p>Writing that language takes practice. This tool hands the job to three specialised <b>AI models</b> and has Lean check
the result. The AI can be wrong; <b>Lean cannot</b>: Lean has the final say.</p>

<h2>How to use it</h2>
<ol>
<li><b>Write your problem</b> in your own words, in English or French (LaTeX formulas welcome), or import a
<code>.tex</code> file. Click <b>“Prove”</b>.</li>
<li>Everything runs on its own: one AI <b>translates</b> the problem into Lean, another <b>searches for a proof</b> (when
Lean rejects it, it reads the error and fixes it), and a third one <b>explains</b> it in plain language. The right model
is loaded automatically for each step (a few seconds).</li>
<li>The result stays in a <b>dossier</b>: you can then type a request, as in a chat: “add the hypothesis n &gt; 0”,
“a shorter proof”, “explain step 2”. The tool works out which step to redo and keeps every version
(“Go back to this version” link).</li>
<li>Export: copy, <code>.lean</code> file, or a <b>LaTeX</b> document for Overleaf (statement, proof, explanation).</li>
</ol>

<h2>Why read the Lean statement?</h2>
<p>Lean proves exactly what it is asked. If the translation forgets a hypothesis (say “<i>n</i> positive”) or changes a
number, Lean will prove <i>another statement</i> — correctly, but it will not be yours. The statement is shown in the
thread and in the “Lean statement” tab: take a look, and click “Fix the statement…” if needed.
(Option: “⚙ Options → Pause to review the statement” stops the chain after the translation.)</p>

<h2>Memory</h2>
<ul>
<li><b>Your profile</b> (System tab): your audience, notations, habits. It is read before every translation and
explanation.</li>
<li><b>The dossier thread</b>: every follow-up request builds on the previous versions.</li>
<li><b>The library</b>: every proven result is stored and offered as a lemma for later proofs (same Lean version).</li>
</ul>

<h2>What does a Lean statement look like?</h2>
<p><code>theorem sum_even (a b : ℕ) (ha : Even a) (hb : Even b) : Even (a + b) := by sorry</code></p>
<table>
<tr><td><code>theorem name</code></td><td class="m">“theorem”, with a name</td></tr>
<tr><td><code>(a b : ℕ)</code></td><td class="m">“let a and b be natural numbers”</td></tr>
<tr><td><code>(ha : Even a)</code></td><td class="m">hypothesis: “a is even” (ha is its name)</td></tr>
<tr><td><code>: Even (a + b)</code></td><td class="m">what we want to prove: “a + b is even”</td></tr>
<tr><td><code>:= by sorry</code></td><td class="m">“the proof goes here”; <code>sorry</code> means <i>to be done</i>.</td></tr>
</table>

<h2>Glossary</h2>
<table>
<tr><td><b>Mathlib</b></td><td class="m">the large library of mathematical results that Lean knows</td></tr>
<tr><td><b>Tactic</b></td><td class="m">a proof step: <code>linarith</code> (inequalities), <code>ring</code> (algebra), <code>simp</code> (simplify), <code>induction</code>…</td></tr>
<tr><td><b>ℝ ℕ ℤ ℚ</b></td><td class="m">reals, naturals, integers, rationals. Type <code>\\R</code> + Space for ℝ, <code>\\le</code> for ≤.</td></tr>
<tr><td><b>∀ ∃ → ∧ ∨ ¬</b></td><td class="m">for all, there exists, implies, and, or, not</td></tr>
<tr><td><b>Attempt</b></td><td class="m">one try by the AI, checked by Lean; after a failure the next attempt uses the errors</td></tr>
<tr><td><b>Token</b></td><td class="m">a piece of a word (≈ ¾ of a word); “tokens/s” is the AI's writing speed</td></tr>
</table>

<h2>Limits</h2>
<ul>
<li>The AI does well on high-school and undergraduate exercises; olympiad problems often fail.</li>
<li>“No proof found” does not mean the statement is false.</li>
<li>The explanation is written by an AI and can be clumsy. The Lean proof is the verified part.</li>
<li>Nothing is sent over the Internet. Only the weekly update check (Lean, Goedel models) contacts GitHub and
Hugging Face; it can be turned off in the System tab.</li>
</ul>
"""


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Aide — Lean pour les débutants"))
        self.resize(820, 700)
        lay = QVBoxLayout(self)
        view = QTextBrowser()
        view.setHtml(pick(HTML_FR, HTML_EN))
        view.setOpenExternalLinks(True)
        lay.addWidget(view)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button(_("Fermer"), "Primary", slot=self.accept))
        lay.addLayout(row)
