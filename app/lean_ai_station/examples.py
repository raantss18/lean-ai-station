"""Built-in exercises (school → university → olympiad). Statements compile with both
Lean 4.9-era Mathlib (Goedel) and current Mathlib."""
from dataclasses import dataclass

from .i18n import _


@dataclass(frozen=True)
class Example:
    key: str
    title: str
    level: str
    blurb: str
    statement: str


EXAMPLES = [
    Example("logique", "Et logique", "Initiation",
            "Si P et Q sont vraies, alors « P et Q » est vraie.",
            "theorem et_logique (P Q : Prop) (hp : P) (hq : Q) : P ∧ Q := by sorry"),
    Example("pairs", "Somme de deux nombres pairs", "Collège",
            "La somme de deux entiers pairs est paire.",
            "theorem somme_pairs (a b : ℕ) (ha : Even a) (hb : Even b) : Even (a + b) := by sorry"),
    Example("identite", "Identité remarquable", "Collège",
            "(a + b)² = a² + 2ab + b² pour tous réels a, b.",
            "theorem identite_remarquable (a b : ℝ) : (a + b) ^ 2 = a ^ 2 + 2 * a * b + b ^ 2 := by sorry"),
    Example("amgm", "Inégalité 2ab ≤ a² + b²", "Lycée",
            "Conséquence de (a − b)² ≥ 0.",
            "theorem deux_ab_le (a b : ℝ) : 2 * a * b ≤ a ^ 2 + b ^ 2 := by sorry"),
    Example("cercle", "Équation de cercle", "Lycée",
            "Si x² + y² = 2x − 4y − 5, alors x + y = −1 (exemple officiel de Goedel-Prover).",
            "theorem square_equation_solution {x y : ℝ} (h : x ^ 2 + y ^ 2 = 2 * x - 4 * y - 5) : x + y = -1 := by sorry"),
    Example("triangulaire", "Inégalité triangulaire", "Licence",
            "|a + b| ≤ |a| + |b| pour tous réels a, b.",
            "theorem triangulaire (a b : ℝ) : |a + b| ≤ |a| + |b| := by sorry"),
    Example("divisible", "Produit de trois entiers consécutifs", "Licence",
            "n(n + 1)(n + 2) est divisible par 6.",
            "theorem six_dvd (n : ℕ) : 6 ∣ n * (n + 1) * (n + 2) := by sorry"),
    Example("cone", "Volume d'un cône (miniF2F)", "Olympiade",
            "mathd_algebra_478 : B = 30, h = 6,5 et V = Bh/3 donnent V = 65.",
            "theorem mathd_algebra_478 (b h v : ℝ) (h₀ : 0 < b ∧ 0 < h ∧ 0 < v) (h₁ : v = 1 / 3 * (b * h))\n"
            "    (h₂ : b = 30) (h₃ : h = 13 / 2) : v = 65 := by sorry"),
]

VERIFY_SAMPLE = """import Mathlib

-- Cliquez sur « Vérifier » : Lean contrôle chaque ligne.
theorem carre_positif (x : ℝ) : 0 ≤ x ^ 2 := by
  positivity

example : (2 : ℕ) + 2 = 4 := by
  norm_num
"""


def example_text(ex: Example) -> str:
    """The problem as a teacher would say it, in the interface language."""
    return _(ex.blurb)
