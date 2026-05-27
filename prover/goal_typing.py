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
    r'|\bsorted\b|\bappend\b|\bconcat\b|\bzip\b|\bremdups\b|\bsplit\b'
    # Set/sum/product/interval syntax — { ... }, sum/prod functions, intervals
    # like `{..<n}`. Without this, goals like `sum f {..<n} = ...` get
    # treated as arithmetic and `sum` gets annotated as `::nat`.
    r'|\{|\}|\bsum\b|\bprod\b|\bSup\b|\bInf\b|\bSUM\b|\bPROD\b'
    r'|\bMAX\b|\bMIN\b|\bLEAST\b|\bGREATEST\b'
    r'|\.\.<|\.\.|<\.\.)'
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
    # Higher-order math functions (returning ranged values; never nat-typed
    # at the head position even when the result is a nat)
    "sum", "prod", "Sup", "Inf", "SUM", "PROD", "MAX", "MIN",
    "LEAST", "GREATEST", "image", "vimage", "inv", "inv_into",
    "Domain", "Range", "Field", "trancl", "rtrancl", "converse",
    "inj", "inj_on", "surj", "bij", "bij_betw", "refl", "sym",
    "trans", "antisym", "asym", "irrefl", "acyclic",
    # Numeric/analytic functions (MiniF2F-heavy). Annotating these as `::nat`
    # or `::real` corrupts the term: `(floor::real) (x/y)` is parsed as a real
    # applied to `(x/y)`, which is a type error and fails as
    # `Undefined type name: real`.
    "floor", "ceiling", "round", "frac", "sqrt", "root", "ln", "log",
    "exp", "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "sinh", "cosh", "tanh", "sgn", "norm", "fact", "binomial",
    "of_int", "of_nat", "of_real", "real_of_nat", "real_of_int",
    "numeral", "power", "pow", "Re", "Im", "cnj", "ii",
    # Combinatorics infix constants. `n choose k` is binomial coefficient,
    # `choose` is a constant, not a free var. Annotating as `(choose::nat)`
    # produces `n (choose::nat) k` which fails to parse.
    "choose", "div_mod", "dvd", "coprime",
    # Arithmetic constants that take an argument (Isabelle's `inverse`,
    # `uminus`, etc.) — `(inverse::real)` mangles to `(inverse::real) 8`
    # which Isabelle reads as the constant `inverse` ascribed to type `real`
    # then applied to 8, failing with `Undefined type name: real`.
    "inverse", "uminus", "plus", "minus", "times", "divide",
    "even", "odd", "prime", "Max", "Min",
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
        # Heuristic: skip if the var is preceded by a backslash (Isabelle escape),
        # OR if it sits inside an Isabelle ASCII escape like `\<sigma>`, `\<forall>`.
        # In `\<sigma>` the char immediately before `sigma` is `<`, not `\` — so we
        # also check for `<` preceded by `\`. Without this guard the annotator
        # rewrites `\<sigma>` → `\<(sigma::real)>`, which is invalid Isabelle and
        # causes every downstream `by ...` to parse-fail with
        # `Malformed symbolic character: "\<"`.
        start, end = m.span()
        if start > 0 and goal[start - 1] == "\\":
            return var
        if start >= 2 and goal[start - 1] == "<" and goal[start - 2] == "\\":
            return var
        # Also skip if immediately followed by `>` and preceded somewhere by `\<`
        # within the last few chars (covers `\<forall>` even if regex word-boundary
        # interacts oddly).
        if end < len(goal) and goal[end] == ">" and "\\<" in goal[max(0, start - 3):start]:
            return var
        # Heuristic: if the "var" is immediately followed by `(` (optionally
        # after whitespace), it's a function application — `foo (x+y)` means
        # foo is being called, so foo is a constant, not a free var to type.
        # This catches math functions we forgot to list in _RESERVED
        # (e.g. an LLM-generated goal using `arctan (...)`).
        tail = goal[end:end + 4]
        if re.match(r"\s*\(", tail):
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
        # Isabelle ASCII escapes — `sigma`/`forall` inside `\<...>` MUST NOT be
        # rewritten as `\<(sigma::real)>` (breaks the symbolic char and causes
        # downstream `by` to parse-fail).
        r"bij \<sigma> ⟹ \<forall> x. \<sigma> x = 5 * x - 12 ⟹ x = 47 / 24",
        # `floor` is a constant (function), not a free var — must NOT be
        # annotated as `(floor::real)`, which would make Isabelle complain
        # `Undefined type name: real` because `floor :: 'a => int`.
        "⋀x y. f x y = x - y * floor (x/y) ⟹ f ((3::real)/8) (- 2/5) = - 1/40",
    ]
    for s in samples:
        print(f"{s!r}\n  → {annotate_numeric_vars(s)!r}\n")
