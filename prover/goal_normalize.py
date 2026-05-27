# prover/goal_normalize.py
"""Normalize benchmark goal strings before sending to Isabelle.

Many goal files (notably `hol_main_easy_goals.txt`) were extracted from theory
files spanning multiple Isabelle versions and re-rendered through tools that
introduced cosmetic-but-fatal mutations:

  1. **Deprecated library names** — `prod_case`, `sum_case`, etc. were renamed
     to `case_prod`, `case_sum` etc. in modern Isabelle. The old names are no
     longer constants in `Main`, so Isabelle parses them as unbound free
     variables, making the goal unprovable.

  2. **Look-alike Unicode characters** — typesetting glyphs that *render*
     correctly but are not Isabelle keywords. The most common is `⁻¹` (U+207B
     SUPERSCRIPT MINUS + U+00B9 SUPERSCRIPT ONE) being used in place of the
     Isabelle inverse mark `¯¹` (U+00AF MACRON + U+00B9). Isabelle's lexer
     rejects the former with "Inner lexical error / Failed to parse prop".

This module fixes both classes of issue with conservative, well-documented
string substitutions.
"""
from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# (1) Deprecated identifier renames.
#
# Map: deprecated name → modern Isabelle name. We only rename whole-word
# matches (regex `\b`) to avoid clobbering substrings of unrelated identifiers
# (e.g. `prod_case` should NOT match `my_prod_case_def`).
#
# The list is conservative: only renames documented as safe drop-ins in the
# Isabelle release notes. When in doubt, leave the identifier alone.
# ---------------------------------------------------------------------------
_DEPRECATED_RENAMES = {
    # Datatype case combinators (renamed in Isabelle ~2014)
    "prod_case":   "case_prod",
    "sum_case":    "case_sum",
    "nat_case":    "case_nat",
    "list_case":   "case_list",
    "option_case": "case_option",
    "bool_case":   "case_bool",
    # `split` was the very old name for `case_prod`. Removed/renamed long ago.
    # Found in hol_main_mid_goals_test.txt:  `split f (a,b) = f a b`.
    "split":       "case_prod",
    # The corresponding *_def facts also get renamed.
    "prod_case_def":   "case_prod_def",
    "sum_case_def":    "case_sum_def",
    "nat_case_def":    "case_nat_def",
    "list_case_def":   "case_list_def",
    "option_case_def": "case_option_def",
    "bool_case_def":   "case_bool_def",
    "split_def":       "case_prod_def",
}

_RENAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _DEPRECATED_RENAMES) + r")\b"
)


# ---------------------------------------------------------------------------
# (2) Unicode lookalikes that Isabelle's lexer does NOT accept.
#
# Each tuple is (bad_char_or_sequence, replacement). We do simple string-level
# substitution — no surrounding context is needed because these characters
# either are or are not Isabelle keywords.
#
# IMPORTANT — characters Isabelle DOES recognize and we MUST NOT rewrite:
#   ⟹  U+27F9  \<Longrightarrow>
#   ⟶  U+27F6  \<longrightarrow>
#   ⟷  U+27F7  \<longleftrightarrow>
#   ⋀  U+22C0  \<And>
#   ∈ ∉ ⊆ ⊂ ∪ ∩ ≤ ≥ ¬ ∧ ∨ ∀ ∃  — all native Isabelle math symbols
# So the substitution list below is intentionally short and conservative.
# ---------------------------------------------------------------------------
_UNICODE_FIXES: list[tuple[str, str]] = [
    # Inverse / converse:
    #   ⁻¹  is U+207B (SUPERSCRIPT MINUS) + U+00B9 (SUPERSCRIPT ONE)
    #   Isabelle wants ¯¹ (U+00AF MACRON + U+00B9). The ASCII form
    #   \<inverse> would also work, but staying in Unicode keeps the
    #   goal pretty-printable.
    ("⁻¹", "¯¹"),
    # Standalone SUPERSCRIPT MINUS used alone in math contexts: replace
    # with MACRON so that a sequence like `r⁻` becomes `r¯`. Rare, but
    # harmless because U+207B isn't an Isabelle keyword either.
    ("⁻", "¯"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
_BINDER_TYPE_SPACE = re.compile(
    r"(\b(?:LEAST|GREATEST|SOME|THE|MIN|MAX)\s+[A-Za-z_][A-Za-z0-9_]*)\s+::"
)


def normalize_goal(goal: str) -> str:
    """Return a normalized form of `goal` suitable for Isabelle parsing.

    Idempotent: normalize_goal(normalize_goal(g)) == normalize_goal(g).
    """
    if not goal:
        return goal

    # (1) Rename deprecated identifiers.
    out = _RENAME_RE.sub(lambda m: _DEPRECATED_RENAMES[m.group(1)], goal)

    # (2) Unicode lookalike fixes.
    for bad, good in _UNICODE_FIXES:
        if bad in out:
            out = out.replace(bad, good)

    # (3) Tighten stray whitespace in binder type ascriptions.
    # MiniF2F goals sometimes ship as `LEAST x ::nat. P x` (note the space
    # before `::`). Isabelle's binder parser rejects this with
    # `Inner syntax error / Failed to parse prop`. The canonical form is
    # `LEAST x::nat. P x` (no space). We collapse the space for the common
    # binders: LEAST/GREATEST/SOME/THE/MIN/MAX.
    out = _BINDER_TYPE_SPACE.sub(r"\1::", out)

    return out


def normalization_report(original: str, normalized: str) -> Optional[str]:
    """If `original != normalized`, return a short human-readable summary of
    what changed (for logging); otherwise return None."""
    if original == normalized:
        return None
    changes: list[str] = []
    for old, new in _DEPRECATED_RENAMES.items():
        if re.search(rf"\b{re.escape(old)}\b", original):
            changes.append(f"{old}→{new}")
    for bad, good in _UNICODE_FIXES:
        if bad in original:
            changes.append(f"U+{ord(bad[0]):04X}→U+{ord(good[0]):04X}")
    return ", ".join(changes) if changes else "(unspecified)"


# ---------------------------------------------------------------------------
# CLI smoke test: `python -m prover.goal_normalize`
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    samples = [
        # Deprecated name (from hol_main_easy_goals.txt)
        "prod_case f (a,b) = f a b",
        # Inverse character (from hol_main_easy_goals.txt)
        "sym r ⟹ sym (r⁻¹)",
        # Plain goal, should pass through untouched
        "rev (rev xs) = xs",
        # Modern goal, should pass through untouched
        "case_prod f (a, b) = f a b",
        # `⟹` (U+27F9) is Isabelle's `\<Longrightarrow>` — must NOT be rewritten
        "A ⟹ A ∨ B",
        # Compound: both kinds of normalization at once
        "sum_case f g (Inl x) = f x ⟹ sym (r⁻¹)",
        # Stray space in binder type ascription (MiniF2F)
        "(LEAST x ::nat. [30 * x = 42] (mod 47)) = 39",
        "(GREATEST n ::nat. n ≤ 10) = 10",
    ]
    for s in samples:
        n = normalize_goal(s)
        rep = normalization_report(s, n)
        flag = f"   [{rep}]" if rep else ""
        print(f"{s!r}\n  → {n!r}{flag}\n")
