# prover/goal_typing.py
"""Heuristic type annotation for goals extracted without explicit type info.

Many goal files (e.g. `hol_main_easy_goals.txt`) contain bare lemma statements
like

    m + (n + m) = n + (m + m)
    m398 <= m398 + n398

with no `::nat` annotation. Isabelle infers the most general type (`'a` with
`+`/`<=` typeclass constraints), which makes these goals unprovable in
isolation — `simp`/`auto`/`arith` fail at the typeclass level, not because
the math is wrong.

This module annotates the FIRST occurrence of each lowercase free variable
with `::nat` when the goal looks arithmetic, leaving propositional / list /
set goals alone. Subsequent occurrences are inferred by Isabelle from the
first annotation, so a single touch per variable is enough.
"""
from __future__ import annotations

import re
from typing import Optional

# Operators / functions that strongly indicate a numeric goal.
# Includes:
#   - arithmetic operators: + * / ≤ ≥ <= >= div mod
#   - successor: Suc
#   - number-theoretic helpers: gcd lcm
#   - ordered-type functions: min max abs (need linorder/ord, otherwise
#     `by simp` can't close even trivial identities like `max n n = n`)
_NUMERIC_OPS = re.compile(
    r'(?:\+|\*|/|≤|≥|<=|>=|\bdiv\b|\bmod\b|\bSuc\b|\bgcd\b|\blcm\b'
    r'|\bmin\b|\bmax\b|\babs\b)'
)

# Patterns that disqualify a goal from numeric annotation: list / set / option /
# function operators, or anything that's clearly not numeric.
# Added: take, drop, nth, butlast, last, tl, hd, distinct, sorted, append.
# These are list operations whose presence means the goal's free variables
# are list/element types, not nats. Previously these slipped through and the
# annotator wrote nonsense like `(take::nat)` and `(xs::nat)`.
_LIST_INDICATORS = re.compile(
    r'(?:\bset\b|\bmap\b|\brev\b|\blength\b|\bhd\b|\btl\b|\bconverse\b'
    r'|\bfilter\b|\bfold[lr]?\b|@|\[|\]|#|\bSome\b|\bNone\b|\bcard\b'
    r'|\bUNIV\b|∈|∉|⊆|⊂|∪|∩|\\<in>|\\<subseteq>'
    r'|\btake\b|\bdrop\b|\bnth\b|\bbutlast\b|\blast\b|\bdistinct\b'
    r'|\bsorted\b|\bappend\b|\bconcat\b|\bzip\b|\bremdups\b|\bsplit\b)'
)

# Already-annotated variables of the form (x::T) — we won't re-annotate these.
_ANNOTATED_VAR = re.compile(r'\(\s*([a-z][A-Za-z0-9_]*)\s*::\s*[A-Za-z_][A-Za-z0-9_]*\s*\)')

# Any type hint of the form (thing::T), where `thing` may be a numeric literal
# (`0`, `123`) or a variable. Used to learn the dominant type so we annotate
# unannotated variables consistently — e.g. `(0::int) ≤ abs a171` should
# annotate `a171` as `int`, not as the default `nat`.
_ANNOTATED_ANY = re.compile(r'\(\s*(?:[0-9]+|[a-z][A-Za-z0-9_]*)\s*::\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)')

# Free-variable candidates: lowercase start, alphanumeric.
_BARE_VAR = re.compile(r'\b([a-z][A-Za-z0-9_]*)\b')

# Words that must NEVER be treated as free variables — Isabelle/HOL keywords,
# constants, and common library function names.
_RESERVED = frozenset({
    # Keywords / commands
    "lemma", "theorem", "fix", "fixes", "shows", "assumes", "and",
    "proof", "qed", "by", "apply", "done", "obtain", "show",
    "have", "thus", "hence", "then", "from", "where", "let", "in",
    "if", "then", "else", "case", "of", "do", "fun", "definition",
    # Logical constants
    "True", "true", "False", "false", "not", "or", "Not",
    # Numeric / order constants & operators referenced by name
    "div", "mod", "gcd", "lcm", "abs", "min", "max", "Suc",
    # Type names
    "nat", "int", "real", "bool", "complex", "rat",
    # Set/list-related (defensive — should be excluded by _LIST_INDICATORS anyway)
    "set", "map", "rev", "length", "hd", "tl", "card", "finite",
    "fst", "snd", "fold", "foldl", "foldr", "filter", "concat",
    "take", "drop", "nth", "butlast", "last", "distinct", "sorted",
    "append", "zip", "remdups", "split",
    # Common list variables — these are list-typed, never nat-typed
    "xs", "ys", "zs", "ws", "as", "bs",
    # Common predicate / function variables
    "p", "q", "f", "g", "h", "r", "s",
    "Some", "None", "the", "id",
    # Common one-letter type-class instance names that aren't free vars
    "x", "y", "z",  # NOTE: these CAN be free vars; only block when context unclear
})

# We do annotate single letters if they appear in arithmetic — so override:
_RESERVED_HARD = _RESERVED - {"x", "y", "z"}


def looks_arithmetic(goal: str) -> bool:
    """True iff the goal contains numeric operators and no obvious list/set markers."""
    if not _NUMERIC_OPS.search(goal):
        return False
    if _LIST_INDICATORS.search(goal):
        return False
    return True


def annotate_numeric_vars(goal: str, *, sort: str = "nat") -> str:
    """If `goal` looks arithmetic, annotate the first occurrence of each
    unannotated lowercase free variable with `::{sort}`.

    Idempotent: a goal that's already annotated is returned unchanged.
    Conservative: list/set/predicate goals pass through untouched.

    >>> annotate_numeric_vars("m + (n + m) = n + (m + m)")
    '(m::nat) + ((n::nat) + m) = n + (m + m)'
    >>> annotate_numeric_vars("A0 ∧ B0 ⟶ A0")
    'A0 ∧ B0 ⟶ A0'
    >>> annotate_numeric_vars("(m::nat) + n = n + m")
    '(m::nat) + n = n + m'
    """
    if not looks_arithmetic(goal):
        return goal

    # Variables that are already annotated — don't re-annotate them.
    already = {m.group(1) for m in _ANNOTATED_VAR.finditer(goal)}
    # All types appearing in (X::T) annotations (variables AND literals).
    # We use these to learn the dominant type so we annotate any remaining
    # unannotated variables consistently. E.g. `(0::int) ≤ abs a171` has no
    # pre-annotated variables, but `(0::int)` tells us to use `int` for `a171`.
    existing_types = [m.group(1) for m in _ANNOTATED_ANY.finditer(goal)]
    # Prefer numeric concrete types (int/nat/real); otherwise take the first.
    effective_sort = sort
    if existing_types:
        for t in existing_types:
            if t in ("nat", "int", "real", "rat", "complex"):
                effective_sort = t
                break
        else:
            effective_sort = existing_types[0]
    else:
        # Heuristic: if the goal uses unary negation (e.g. `-a`, `abs (-x)`)
        # the default sort `nat` is wrong because nat has no negative numbers.
        # Detect a `-` that's NOT preceded by an operand (i.e. it's unary) and
        # default to `int` instead.
        # Pattern: `-` at goal start, or preceded by `(`, `=`, `⟹`, `,`,
        # a logical operator, or another arithmetic operator + whitespace.
        # We approximate with: `(?:^|[(=⟹⟶⟷,∧∨¬<≤>≥+\-*/ ])\s*-\s*[A-Za-z]`.
        if re.search(r"(?:^|[(=⟹⟶⟷,∧∨¬<≤>≥+\-*/ ])\s*-\s*[A-Za-z]", goal):
            effective_sort = "int"

    seen: set[str] = set()

    def repl(m: "re.Match[str]") -> str:
        var = m.group(1)
        if var in _RESERVED_HARD or var in already or var in seen:
            return var
        # Heuristic: skip if the var is preceded by a backslash (Isabelle escape).
        start, _ = m.span()
        if start > 0 and goal[start - 1] == "\\":
            return var
        seen.add(var)
        return f"({var}::{effective_sort})"

    return _BARE_VAR.sub(repl, goal)


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    samples = [
        "m + (n + m) = n + (m + m)",
        "m398 <= m398 + n398",
        "A0 ∧ B0 ⟶ A0",
        "rev (rev xs) = xs",
        "set (xs @ ys) = set xs ∪ set ys",
        "(m::nat) + n = n + m",
        "Suc m + n = Suc (m + n)",
        # Real-world case from hol_main_easy_goals.txt:2766
        # Existing `::int` annotation must propagate to `b265` too,
        # not be overridden with a default `::nat`.
        "- ((a265::int) + b265) = (-a265) + (-b265)",
        "(x::real) + y = y + x",
        # Literal-only annotation: `(0::int)` must seed `a171` as int, not nat.
        "(0::int) ≤ abs a171",
        # Unary minus: should default to `int`, not `nat` (nat has no negatives).
        "abs (-a) = abs a",
    ]
    for s in samples:
        print(f"{s!r}\n  → {annotate_numeric_vars(s)!r}\n")
