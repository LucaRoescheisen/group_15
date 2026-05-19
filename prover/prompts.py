# prover/prompts.py (optimized, compatible)
import re
from typing import List, Optional, Sequence

SYSTEM_STEPS = """You are an Isabelle/HOL proof expert.
Given:
• the lemma goal,
• the accepted proof lines so far, and
• the latest printed subgoals text (including schematic variables and assumptions),

propose 3–8 SHORT next proof *commands*, ONE per line, that can be appended inside the current proof.
Rules:
- Output ONLY `apply`-style commands starting with `apply ` or `apply(`.
- Prefer small, locally-sound steps that reduce subgoals.
- When relevant, use already-proven facts/lemmas via `simp add:`, `simp only:`, `auto simp add:`, `intro`, `elim`, `rule`, `metis`, etc.
- Use structured searchers prudently: `fastforce`, `blast`, `clarify`, `clarsimp`, `linarith`, `arith`.
- Split on datatypes or booleans when subgoals suggest it: `apply (cases x)`, `apply (cases rule: list.exhaust)`, `apply (induction n)`, `apply (induction xs)`.
- You may rewrite with packages: `apply (subst ...)`, `apply (simp add: algebra_simps field_simps)`, `apply (simp split: option.splits if_splits)`.
- Do NOT emit comments, bullets, `have`/`show`, or code fences.
- Never use placeholder names like `foo_def`, `bar_def`, or `my_fun_def`.
- Only use `*_def` if that exact name already appears in the “Helpful facts”.
- section or (rarely) in the latest subgoal text; otherwise avoid `*_def`.

Examples of acceptable lines:
apply simp
apply (unfolding my_fun_def)
apply (simp add: Let_def)
apply (simp add: my_fun_def)
apply (subst my_fun_def[symmetric])
apply (simp split: if_splits option.splits prod.splits sum.splits list.splits)
apply auto
apply (auto simp add: algebra_simps)
apply (simp only: append_assoc)
apply arith
apply (clarsimp)
apply (cases xs)
apply (cases rule: option.exhaust)
apply (cases xs rule: list.exhaust)
apply (induction n)
apply (induction xs arbitrary: ys)
apply (induction rule: measure_induct_rule[of …])
apply (rule_tac x=… in exI)
apply (metis append_assoc)
apply (rule conjI)
apply (erule disjE)
apply (intro impI)
apply fastforce
apply blast
apply (subst append_Nil2)
"""

SYSTEM_FINISH = """You are an Isabelle/HOL proof expert.
Given:
• the lemma goal,
• the accepted proof lines so far, and
• the latest printed subgoals text,

propose 3–8 SHORT finishing commands that can close the current proof.
Rules:
- Output only one command per line, starting with `by ` or the single word `done`.
- Only use `done` when there are **no subgoals remaining** in the latest state.
- Use available facts/lemmas when helpful (e.g., `by (simp add: <facts>)`, `by (metis <facts>)`, `by (rule <thm>)`).
- Prefer simple finishers first (`done`, `by simp`, `by auto`) before heavier tactics (`by blast`, `by fastforce`, `by (metis ...)`, `by linarith`).
- No comments or code fences.

Examples:
done
by simp
by (simp add: subset_antisym)
by clarsimp
by auto
by (auto intro: subsetI)
by arith
by presburger
by blast
by meson
by fastforce
by (metis append_assoc map_append)
by (rule_tac x=… in exI, simp)
by (simp add: algebra_simps)
by (cases xs, simp_all)
"""

USER_TEMPLATE = """Goal:
{goal}

Accepted steps so far:
{steps}

Latest printed subgoals (may be partial):
{state_hint}

Helpful facts (lemmas already available in context):
{facts}

Constraints:
- Output ONLY the commands, one per line.
- 3 to 8 candidates.
"""

# Precompiled once
_LINE_RE   = re.compile(r"^\s*(?:[-*]\s*)?([a-zA-Z].*?)\s*$")
# Keep CONTENTS of fenced code blocks, drop just the ``` markers + optional
# language tag. Previously this deleted the whole block, which silently lost
# Gemini's actual proof when it replied with ```isabelle ... ``` (the default).
_FENCE_RE  = re.compile(r"```(?:[A-Za-z0-9_+-]*)\s*\n?(.*?)\n?```", re.DOTALL)
# Inline `code` (backtick spans). We keep the content; backtick is not part of
# Isabelle tactic syntax, so dropping the ticks is safe.
_INLINE_TICK_RE = re.compile(r"`([^`\n]+)`")
_OLENUM_RE = re.compile(r"^\d+\.\s*")
_WS_RE     = re.compile(r"\s+")
# Lead-ins like "Try this:", "Try: ", "Suggestion: ", "Use:" — strip them so
# the rest of the line can match an `apply ...` / `by ...` prefix.
_LEAD_IN_RE = re.compile(
    r"^(?:try\s+this\s*:|try\s*:|suggestion\s*:|use\s*:|finisher\s*:|tactic\s*:)\s*",
    re.IGNORECASE,
)


def parse_ollama_lines(text: str, allowed_prefixes: Sequence[str], max_items: int) -> List[str]:
    """
    Extract LLM output lines that start with one of `allowed_prefixes`.

    Tolerant to:
      - ```language ... ``` code fences (content is kept, fences are stripped).
      - Inline `backticked` spans (ticks stripped, content kept).
      - Bullet / numbered list markers.
      - Lead-ins like "Try this:", "Suggestion: ", etc.
      - Lines that contain an `apply ...` / `by ...` *after* prose, e.g.
        "Step 1: apply auto" → "apply auto".
    """
    if not text or text.startswith("__ERROR__"):
        return []

    # Keep fenced-block CONTENT, drop the fences themselves.
    text = _FENCE_RE.sub(lambda m: m.group(1), text)
    # Drop inline backticks (keep the content).
    text = _INLINE_TICK_RE.sub(lambda m: m.group(1), text)

    out: List[str] = []
    seen = set()
    prefixes = tuple(allowed_prefixes) if not isinstance(allowed_prefixes, tuple) else allowed_prefixes

    for ln in text.splitlines():
        m = _LINE_RE.match(ln)
        if not m:
            continue
        cand = _OLENUM_RE.sub("", m.group(1).strip())
        # Strip a single lead-in word like "Try this:" / "Suggestion:".
        cand = _LEAD_IN_RE.sub("", cand).strip()
        if not cand or len(cand) > 200:
            continue
        # Drop comment-only lines (Markdown headers, etc.).
        # Note: '#' is also Isabelle's cons operator, but it's unusual to start
        # a candidate line with a bare '#', so a leading-'#' filter is safe.
        if cand.startswith("#"):
            continue

        # Direct prefix match (original behaviour).
        picked: Optional[str] = None
        if cand.startswith(prefixes):
            picked = cand
        else:
            # Fallback: search inside the line for the first prefix occurrence.
            # Captures cases like "Step 1: apply auto" or "I suggest apply simp"
            # which Gemini emits when it wraps tactics in prose.
            for p in prefixes:
                idx = cand.find(p)
                if idx != -1:
                    tail = cand[idx:]
                    # If there's more prose after the tactic (e.g. a period
                    # followed by an English sentence), cut at the first
                    # sentence-ending punctuation that isn't inside parens.
                    depth = 0
                    end = len(tail)
                    for i, ch in enumerate(tail):
                        if ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth = max(0, depth - 1)
                        elif depth == 0 and ch in ".!?" and i + 1 < len(tail) and tail[i + 1] in (" ", "\t"):
                            end = i
                            break
                    picked = tail[:end].strip().rstrip(",;.")
                    break

        if not picked:
            continue
        picked = _WS_RE.sub(" ", picked)
        if picked not in seen:
            seen.add(picked)
            out.append(picked)
        if len(out) >= max_items:
            break
    return out


# ---------------------------------------------------------------------------
# Smoke test: `python -m prover.prompts` exercises the parser on realistic
# LLM outputs (plain, fenced, lead-in, prose-prefixed).
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    samples = {
        "plain bullet list": "- apply auto\n- apply simp\n- apply (induction xs)",
        "numbered list": "1. apply auto\n2. apply simp",
        "code fence (Gemini default)": "Here is a proof:\n```isabelle\napply (unfold bij_betw_def)\napply auto\nby blast\n```",
        "inline backticks": "Try `apply auto` or `apply (simp add: bij_betw_def)`.",
        "lead-in 'Try this:'": "Try this: apply auto\nTry this: apply (induction n)",
        "prose-prefixed": "Step 1: apply auto. Step 2: by blast",
        "mixed prose + code": "I suggest apply (cases x). Then apply simp finishes.",
    }
    for name, text in samples.items():
        result = parse_ollama_lines(text, ["apply ", "apply(", "by ", "by("], 8)
        print(f"=== {name} ===\nINPUT: {text!r}\nPARSED: {result}\n")
