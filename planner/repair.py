from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout
import requests
from typing import Callable
from planner.repair_inputs import _find_first_hole, _hole_line_bounds, _APPLY_OR_BY, _snippet_window, _clamp_line_index, _quick_state_and_errors, _extract_error_lines, _run_theory_with_timeout, _print_state_before_hole, _nearest_header, _recent_steps, _normalize_error_texts, _facts_from_state, get_counterexample_hints_for_repair, _earliest_failure_anchor
from planner.prompts import _LOCAL_SYSTEM, _LOCAL_USER, _BLOCK_SYSTEM, _BLOCK_USER
from prover.config import MODEL as DEFAULT_MODEL, OLLAMA_HOST, TIMEOUT_S as OLLAMA_TIMEOUT_S, OLLAMA_NUM_PREDICT, TEMP as OLLAMA_TEMP, TOP_P as OLLAMA_TOP_P
from prover.isabelle_api import build_theory, run_theory, last_print_state_block, finished_ok

# ========== Configuration ==========
_ISA_FAST_TIMEOUT_S = int(os.getenv("ISABELLE_FAST_TIMEOUT_S", "12"))
_ISA_VERIFY_TIMEOUT_S = int(os.getenv("ISABELLE_VERIFY_TIMEOUT_S", "30"))
_SESSION = requests.Session()
_REPAIR_RULES_JSON = os.getenv("REPAIR_RULES_JSON", "").strip()  # optional, declarative fallback rules

# ========== Regex Patterns ==========
_CTX_HEAD = re.compile(r"^\s*(?:using|from|with|then|ultimately|finally|also|moreover)\b")
_HAS_BODY = re.compile(r"^\s*(?:by\b|apply\b|proof\b|sorry\b|done\b)")
_INLINE_BY_TAIL = re.compile(r"\s+by\s+.+$")
_TACTIC_LINE = re.compile(r"^\s*(?:apply|by)\b|(?:\s)by\s+\S")
_STRUCTURAL_LINE = re.compile(r"^\s*(?:lemma|theorem|qed|next|proof|case|have|show|assume|fix|from|using|thus|hence|ultimately|finally|also|moreover|let|where)\b")
_HEAD_CMD_RE = re.compile(r"^\s*(have|show|obtain|then\s+show|thus|hence)\b")
_PROOF_RE = re.compile(r"^\s*proof\b")
_QED_RE = re.compile(r"^\s*qed\b")
_CASE_LINE_RE = re.compile(r"^\s*case\b")
_NEXT_OR_QED_RE = re.compile(r"^\s*(?:next|qed)\b")
_WRAPPED_THEOREM_HEAD = re.compile(r"(?mx)\A(?:[ \t]*(?:\(\*.*?\*\)|\<comment\>.*?\<\/comment\>)[ \t]*\n|[ \t]*\n)*[ \t]*(?:lemma|theorem|corollary)\b")
# Outline-level strategies we want to ban on whole-proof regen
_OUTLINE_PROOF_LINE   = re.compile(r"(?m)^\s*proof(?:\s*\(([^)]*)\))?\s*$")
_OUTLINE_BARE         = re.compile(r"(?m)^\s*(?:induction|cases|coinduction)\b.*$")
# Isabelle "Try this: <tactic> (Xms)" suggestion lines
_TRY_THIS_RE = re.compile(r"[Tt]ry this:\s*(.+?)(?:\s*\(\d+(?:\.\d+)?(?:ms|s)\))?\s*$", re.MULTILINE)

# ========== Utility Functions ==========

def _log(prefix: str, label: str, content: str, trace: bool = True) -> None:
    if trace and content:
        print(f"[{prefix}] {label} (len={len(content)}):\n{content if content.strip() else '  (empty)'}")

def _sanitize_llm_block(text: str) -> str:
    if not text:
        return text
    patterns = [
        r"^\s*<<<BLOCK\s*$",
        r"^\s*BLOCK\s*$",
        r"^\s*<<<PROOF\s*$",
        r"^\s*PROOF\s*$",
        r"^\s*```\s*$",
        r"^\s*```isabelle\s*$",
        r"^\s*```isar\s*$",
        # strip stray fence markers sometimes emitted by LLMs
        r"^\s*<<<\s*$",
        r"^\s*>>>\s*$",
    ]
    # Also drop accidental headers LLMs sometimes leak mid-repair
    header_patterns = [
        r"^\s*lemma\b.*$",
        r"^\s*theorem\b.*$",
        r"^\s*corollary\b.*$",
        r"^\s*proposition\b.*$",
        r"^\s*---\s*$",
    ]
    compiled = [re.compile(p) for p in (patterns + header_patterns)]
    lines = [l for l in text.splitlines() if not any(p.match(l) for p in compiled)]

    # Balance 'proof'/'qed' and cut off any text after the final balanced 'qed'
    balance = 0
    last_closed_idx = -1
    for i, l in enumerate(lines):
        if re.match(r"^\s*proof\b", l):
            balance += 1
        elif re.match(r"^\s*qed\b", l):
            if balance > 0:
                balance -= 1
                if balance == 0:
                    last_closed_idx = i
    if last_closed_idx != -1 and last_closed_idx + 1 < len(lines):
        lines = lines[: last_closed_idx + 1]

    return "\n".join(lines).strip()

def _is_effective_block(text: str) -> bool:
    return bool(_sanitize_llm_block(text or "").strip())

def _fingerprint_block(text: str) -> str:
    # Canonicalize a block to detect duplicates across repair rounds.
    # Normalises: whitespace, quote variants, ATP synonyms,
    # simp add: lemma ordering, and generated fact labels (f1/f2 -> fN).
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text.strip())
    # Normalize backticks and curly/smart quotes to plain ASCII
    t = t.replace("`", "")
    t = t.replace(chr(0x201c), chr(0x22)).replace(chr(0x201d), chr(0x22))
    t = t.replace(chr(0x2018), chr(0x27)).replace(chr(0x2019), chr(0x27))
    # Treat common ATP synonyms as identical so "by auto" == "by blast"
    t = re.sub(r"\bby\s+(auto|blast|fastforce|clarsimp)\b", "by ATP", t)
    # Sort simp add: lemma lists so ordering differences don't bypass dedup
    def _sort_simp(m: re.Match) -> str:
        return "simp add: " + " ".join(sorted(m.group(1).split()))
    t = re.sub(r"\bsimp\s+add:\s+([^\n\]()]+)", _sort_simp, t)
    # Normalise generated fact labels so f1/f2/h3 renames look identical
    t = re.sub(r"\b[fhg]\d+\b", "fN", t)
    return t

def _trim_block_for_prompt(text: str, max_chars: int = 800) -> str:
    """Keep prompt sizes sane by trimming long blocks."""
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    head = t[: max_chars // 2].rstrip()
    tail = t[- max_chars // 2 :].lstrip()
    return head + "\n…\n" + tail

def _why_from_errors(errors: List[str], block_type: str) -> str:
    """Return a targeted failure message for the LLM based on the Isabelle error text.

    A specific 'why' gives the LLM a concrete signal about *what kind* of fix is
    needed rather than the generic fallback, improving repair quality.
    """
    joined = " ".join(errors).lower()
    if "type" in joined and ("mismatch" in joined or "error" in joined or "clash" in joined):
        return "Type mismatch or clash — check that all variables and facts have compatible types."
    if "failed to apply" in joined or "tactic failed" in joined or "try this" in joined:
        return "The tactic failed to apply — it does not match the current proof state; try a different method."
    if "unknown fact" in joined or "undefined" in joined or "undeclared" in joined or "no such" in joined:
        return "Unknown fact or identifier — only reference names that are visible in PROOF_CONTEXT."
    if "unification" in joined:
        return "Unification failed — the goal shape does not match the rule being applied; consider using 'rule' or 'erule' with a more specific lemma."
    if "no subgoals" in joined:
        return "No subgoals remain — remove the extra tactic step."
    if "constructor" in joined and "clash" in joined:
        return "Constructor clash — check the datatype structure matches the pattern being matched."
    if "locally fixed" in joined or "fixed variable" in joined:
        return "Locally fixed variable used incorrectly — avoid introducing new variables not present in the goal."
    if "sorry" in joined:
        return "A 'sorry' placeholder remains — replace every sorry with a real proof step."
    return f"Previous {block_type}-block attempt did not solve the goal; try a structurally different approach (different induction variable, different lemmas, or a calculational proof)."


def _extract_try_this_suggestions(err_texts: List[str]) -> List[str]:
    """Parse every 'Try this: <tactic>' line from Isabelle error/output messages.

    When sledgehammer or solve_direct finds a proof, Isabelle prints a line like:
        Try this: by (simp add: append_assoc) (0.5ms)
    Extracting and trying these directly is faster and more reliable than
    asking the LLM to generate an equivalent tactic from scratch.
    """
    suggestions: List[str] = []
    seen: Set[str] = set()
    for err in err_texts:
        for m in _TRY_THIS_RE.finditer(err):
            tactic = m.group(1).strip()
            # Strip any trailing timing annotation the regex may have missed
            tactic = re.sub(r"\s*\(\d+(?:\.\d+)?(?:ms|s)\)\s*$", "", tactic).strip()
            if tactic and tactic not in seen:
                seen.add(tactic)
                suggestions.append(tactic)
    return suggestions


def _apply_try_this_to_block(
    suggestion: str,
    block_lines: List[str],
    lines: List[str],
    start: int,
    end: int,
) -> Optional[str]:
    """Substitute a 'Try this' tactic into the block, replacing the last tactic line.

    The suggestion (e.g. 'simp add: foo') is normalised to 'by (simp add: foo)'
    and replaces the last 'by ...' or 'apply ...' line in the block — the line
    most likely to be the one that failed.  Returns the full patched text, or
    None if no tactic line was found to replace.
    """
    if not block_lines:
        return None
    # Normalise to a `by X` form
    if suggestion.startswith("by ") or suggestion.startswith("apply "):
        replacement_by = suggestion
    else:
        # Wrap multi-word tactics in parens for safety
        replacement_by = (
            "by (" + suggestion + ")" if " " in suggestion else "by " + suggestion
        )
    _tac_line_re = re.compile(r"^\s*(?:by\b|apply\b)")
    for i in range(len(block_lines) - 1, -1, -1):
        if _tac_line_re.match(block_lines[i]):
            indent = block_lines[i][: len(block_lines[i]) - len(block_lines[i].lstrip())]
            new_block_lines = block_lines[:i] + [indent + replacement_by] + block_lines[i + 1:]
            patched_lines = lines[:start] + new_block_lines + lines[end:]
            return "\n".join(patched_lines)
    return None


def _extract_induction_hyps(state_block: str) -> List[str]:
    """Extract induction hypothesis lines from an Isabelle proof state block.

    Matches patterns like:
        Cons.IH : rev (rev xs) = xs
        IH      : P n
    These facts are already in scope and can be used directly in 'using' clauses.
    """
    lines = (state_block or "").splitlines()
    ihs: List[str] = []
    for line in lines:
        stripped = line.strip()
        if re.search(r"\b\w+\.IH\b", stripped):
            ihs.append(stripped)
        elif re.match(r"IH\s*:", stripped):
            ihs.append(stripped)
    return ihs


def _proactive_sledgehammer_suggestions(
    isabelle, session: str, full_text: str,
    block_start_line: int, block_end_line: int,
    *, timeout_s: int = 12,
) -> List[str]:
    """Run Sledgehammer on the first sorry inside the block and return 'Try this:' suggestions.

    Replaces the first bare 'sorry' in lines [block_start_line, block_end_line) with
    'by sledgehammer', submits the theory to Isabelle, and collects any ATP-found
    suggestions.  This is a deterministic pre-pass: if Sledgehammer solves the
    subgoal we save the entire LLM budget for that hole.
    """
    lines = full_text.splitlines()
    sorry_idx: Optional[int] = None
    for i in range(block_start_line, min(block_end_line, len(lines))):
        if lines[i].strip() == "sorry":
            sorry_idx = i
            break
    if sorry_idx is None:
        return []
    indent = lines[sorry_idx][: len(lines[sorry_idx]) - len(lines[sorry_idx].lstrip())]
    modified = "\n".join(
        lines[:sorry_idx] + [f"{indent}by sledgehammer"] + lines[sorry_idx + 1:]
    )
    try:
        _, errs = _quick_state_and_errors(isabelle, session, modified, timeout_s=timeout_s)
        return _extract_try_this_suggestions(errs)
    except Exception:
        return []


def _maybe_fix_arbitrary(full_text: str, errors: List[str]) -> Optional[str]:
    """Detect 'locally fixed variable X' Isabelle errors and patch the induction.

    When a proof uses 'proof (induction xs)' but the goal involves a second
    variable (e.g. ys) that was fixed by a surrounding 'fix' or outer context,
    Isabelle complains 'locally fixed variable ys'.  The fix is to add
    'arbitrary: ys' to the induction method.

    Returns the patched text if a fix was applied, else None.
    """
    joined = " ".join(errors)
    m = re.search(r"locally fixed variable[s]?\s+(\w+)", joined, re.IGNORECASE)
    if not m:
        return None
    var = m.group(1)

    # Matches: proof (induction <vars>) with optional existing arbitrary clause
    induct_re = re.compile(
        r"(proof\s*\(\s*induction(?:\s+[\w']+)+)((?:\s+arbitrary\s*:\s*[\w'\s]+)?)\s*\)"
    )

    def _add_var(match: re.Match) -> str:
        head = match.group(1)
        arb = (match.group(2) or "").strip()
        if arb:
            if re.search(r"\b" + re.escape(var) + r"\b", arb):
                return match.group(0)  # already present
            return f"{head} {arb} {var})"
        return f"{head} arbitrary: {var})"

    fixed = induct_re.sub(_add_var, full_text, count=1)
    return fixed if fixed != full_text else None


def _is_tactic_line(s: str) -> bool:
    return bool(_TACTIC_LINE.search(s)) and not bool(_STRUCTURAL_LINE.match(s))

def _extract_proof_context(full_text: str, block_start_line: int) -> str:
    """
    Extract the lemma header and all proof content before the block.
    Returns everything from the lemma line up to (but not including) the block.
    """
    lines = full_text.splitlines()
    
    # Find the lemma/theorem header
    lemma_line = -1
    for i in range(min(block_start_line, len(lines) - 1), -1, -1):
        if re.match(r"^\s*(?:lemma|theorem|corollary|proposition)\b", lines[i]):
            lemma_line = i
            break
    
    if lemma_line < 0:
        # No lemma found, return a small window before the block
        start = max(0, block_start_line - 10)
        return "\n".join(lines[start:block_start_line]).strip()
    
    # Return from lemma header to just before the block
    context_lines = lines[lemma_line:block_start_line]
    return "\n".join(context_lines).strip()

# ========== LLM Generation ==========
def _generate_simple(
    prompt: str,
    model: Optional[str] = None,
    *,
    timeout_s: Optional[int] = None,
    temperature: Optional[float] = None,
) -> str:
    m = model or DEFAULT_MODEL
    timeout = timeout_s or OLLAMA_TIMEOUT_S
    if m.startswith("hf:"):
        return _hf_generate(prompt, m[3:], timeout, temperature=temperature)
    elif m.startswith("gemini:"):
        return _gemini_generate(prompt, m[7:], timeout, temperature=temperature)
    elif m.startswith("ollama:"):
        m = m[7:]
    return _ollama_generate(prompt, m, timeout, temperature=temperature)

def _ollama_generate(
    prompt: str, model: str, timeout_s: int, *, temperature: Optional[float] = None
) -> str:
    temp_val = temperature if temperature is not None else OLLAMA_TEMP
    payload = {
        "model": model, "prompt": prompt,
        "options": {"temperature": temp_val, "top_p": OLLAMA_TOP_P, "num_predict": OLLAMA_NUM_PREDICT},
        "stream": False,
    }
    timeout = (10.0, max(30.0, float(timeout_s)))
    resp = _SESSION.post(f"{OLLAMA_HOST.rstrip('/')}/api/generate", json=payload, timeout=timeout)
    resp.raise_for_status()
    return _sanitize_llm_block(resp.json().get("response", "").strip())

def _hf_generate(prompt: str, model_id: str, timeout_s: int, *, temperature: Optional[float] = None) -> str:
    token = os.getenv("HUGGINGFACE_API_TOKEN")
    if not token:
        raise RuntimeError("HUGGINGFACE_API_TOKEN is not set")
    temp_val = temperature if temperature is not None else OLLAMA_TEMP
    payload = {"inputs": prompt, "parameters": {"temperature": temp_val, "top_p": OLLAMA_TOP_P, "max_new_tokens": OLLAMA_NUM_PREDICT, "return_full_text": False}, "options": {"wait_for_model": True}}
    resp = _SESSION.post(f"https://api-inference.huggingface.co/models/{model_id}", headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list) and data:
        result = data[0].get("generated_text", "")
    elif isinstance(data, dict):
        result = data.get("generated_text", "") or (data["choices"][0].get("text", "") if "choices" in data and data["choices"] else "")
    else:
        result = str(data)
    return _sanitize_llm_block(result.strip())

def _gemini_generate(prompt: str, model_id: str, timeout_s: int, *, temperature: Optional[float] = None) -> str:
    # Honour the per-goal LLM call cap defined in planner.skeleton to prevent
    # repair cascades from burning the whole day's quota on one unprovable goal.
    try:
        from planner.skeleton import _check_llm_budget
        _check_llm_budget()
    except ImportError:
        pass
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    gen_cfg: Dict[str, Any] = {"maxOutputTokens": OLLAMA_NUM_PREDICT}
    if temperature is not None:
        gen_cfg["temperature"] = temperature
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
    resp = _SESSION.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}", json=payload, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    result = ""
    try:
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                result = parts[0].get("text", "")
    except Exception:
        result = str(data)
    return _sanitize_llm_block(result.strip())

# ========== Repair Operations (Data Classes) ==========
@dataclass(frozen=True)
class InsertBeforeHole:
    line: str

@dataclass(frozen=True)
class ReplaceInSnippet:
    find: str
    replace: str

@dataclass(frozen=True)
class InsertHaveBlock:
    label: str
    statement: str
    after_line_matching: str
    body_hint: str

RepairOp = Tuple[str, object]

@dataclass
class _RepairMemory:
    rounds: int = 0
    # Keep full failed blocks (same block_type) we tried this session
    prev_blocks: List[str] = field(default_factory=list)
    # Fingerprints to dedup within a session
    prev_fps: Set[str] = field(default_factory=set)

# --- Prior-block store shared across repairs of the *same hole* ---------------
# Maps block_type -> list of failed blocks (latest first, length-capped)
_MAX_PREV_BLOCKS = int(os.getenv("REPAIR_MAX_PREV_BLOCKS", "4"))

# ========== Repair Operations (Parsing & Application) ==========

def _propose_block_repair(
    *,
    goal: str,
    errors: List[str],
    ce_hints: Dict[str, List[str]],
    proof_context: str,
    block_type: str,
    block_text: str,
    model: Optional[str],
    timeout_s: int,
    why: str = "Previous attempt failed; propose a different block-level change.",
    prior_failed_blocks: Optional[str] = None,
    temperature: Optional[float] = None,
    ih_hints: Optional[List[str]] = None,
) -> str:
    ce = ce_hints.get("bindings", []) + ce_hints.get("def_hints", [])
    ih_str = "\n".join(ih_hints) if ih_hints else "(none)"
    fmt_kwargs = dict(
        goal=goal,
        errors="\n".join(f"- {e}" for e in errors) or "(none)",
        ce_hints="\n".join(ce) or "(none)",
        ih_hints=ih_str,
        proof_context=(proof_context or "").strip(),
        block_text=block_text.rstrip(),
        why=why,
        prior_failed_blocks=(prior_failed_blocks or "(none)"),
    )
    if block_type == "have-show":
        prompt = _LOCAL_SYSTEM + "\n\n" + _LOCAL_USER.format(**fmt_kwargs)
    else:
        prompt = _BLOCK_SYSTEM + "\n\n" + _BLOCK_USER.format(**fmt_kwargs)
    try:
        return _sanitize_llm_block(
            _generate_simple(prompt, model=model, timeout_s=timeout_s, temperature=temperature)
        )
    except Exception:
        return ""

def propose_rule_based_repairs(goal_text: str, state_block: str, header: str, facts: List[str]) -> List[RepairOp]:
    """
    Declarative, data-driven fallback:
    - If REPAIR_RULES_JSON is set to a JSON file, load rules and emit ops that match.
    - Otherwise return [] (i.e., no ad-hoc heuristics).
    Rule schema (list):
      {
        "when": {
          "goal_contains_any": ["@", "map"],
          "goal_regex": "length\\s",
          "facts_contains_any": ["append_assoc"],
          "state_contains_any": ["Let "],
          "header_startswith": "proof (induction",
          "header_regex": "proof \\(induction.*\\)"
        },
        "op": { "insert_before_hole": "apply (simp add: append_assoc)" }
      }
      or
      {
        "when": { "header_startswith": "proof (induction", "not_header_contains": ["arbitrary:"] },
        "op": { "replace_in_snippet": { "find": "proof (induction xs)", "replace": "proof (induction xs arbitrary: ys)" } }
      }
    """
    path = _REPAIR_RULES_JSON
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            rules = json.load(f)
    except Exception:
        return []
    def _match(rule) -> Optional[RepairOp]:
        cond = rule.get("when", {}) or {}
        op   = rule.get("op", {}) or {}
        g, st, hd = goal_text or "", state_block or "", header or ""
        fs = facts or []
        import re as _re
        def contains_any(text, keys): return any(k in text for k in keys)
        def not_contains(text, keys): return not any(k in text for k in keys)
        # boolean guards (all must pass if present)
        checks = [
            ("goal_contains_any", lambda v: contains_any(g, v)),
            ("state_contains_any", lambda v: contains_any(st, v)),
            ("facts_contains_any", lambda v: any(x in fs for x in v)),
            ("goal_regex",        lambda v: bool(_re.search(v, g))),
            ("header_startswith", lambda v: hd.startswith(v)),
            ("header_regex",      lambda v: bool(_re.search(v, hd))),
            ("not_header_contains", lambda v: not_contains(hd, v)),
        ]
        for key, pred in checks:
            if key in cond:
                val = cond[key]
                if isinstance(val, list) and not val: 
                    continue
                if not pred(val):
                    return None
        # build op
        if "insert_before_hole" in op and isinstance(op["insert_before_hole"], str):
            return ("insert_before_hole", InsertBeforeHole(op["insert_before_hole"].strip()))
        if "replace_in_snippet" in op and isinstance(op["replace_in_snippet"], dict):
            fnd = (op["replace_in_snippet"].get("find") or "").strip()
            rep = (op["replace_in_snippet"].get("replace") or "").strip()
            if fnd and rep:
                return ("replace_in_snippet", ReplaceInSnippet(fnd, rep))
        if "insert_have_block" in op and isinstance(op["insert_have_block"], dict):
            v = op["insert_have_block"]; lab=v.get("label","H"); stmt=v.get("statement",""); aft=v.get("after_line_matching","then show ?thesis"); hint=v.get("body_hint","apply simp")
            if stmt.strip() and aft.strip():
                return ("insert_have_block", InsertHaveBlock(lab.strip(), stmt.strip(), aft.strip(), hint.strip()))
        return None
    out: List[RepairOp] = []
    for r in rules if isinstance(rules, list) else []:
        rop = _match(r)
        if rop: out.append(rop)
        if len(out) >= 3:
            break
    return out

# ========== Region Analysis ==========
def _enclosing_case_block(lines: List[str], hole_line: int) -> Tuple[int, int]:
    i = hole_line
    while i >= 0 and not _CASE_LINE_RE.match(lines[i]):
        i -= 1
    if i < 0:
        return (-1, -1)
    j = hole_line
    while j < len(lines) and not (_NEXT_OR_QED_RE.match(lines[j])):
        j += 1
    return (i, j)

def _enclosing_subproof(lines: List[str], hole_line: int) -> Tuple[int, int]:
    i = hole_line
    while i >= 0 and not _PROOF_RE.match(lines[i]):
        i -= 1
    if i < 0:
        return (-1, -1)
    depth, j = 1, i + 1
    while j < len(lines) and depth > 0:
        if _PROOF_RE.match(lines[j]):
            depth += 1
        elif _QED_RE.match(lines[j]):
            depth -= 1
        j += 1
    return (i, j if j > i else -1)

def _enclosing_have_show_block(lines: List[str], hole_line: int) -> Tuple[int, int]:
    if not lines:
        return (-1, -1)

    i = _clamp_line_index(lines, hole_line)

    head_re  = re.compile(r"^\s*(have|show|obtain)\b")
    # IMPORTANT: do NOT include `proof` here — subproofs belong to the block.
    fence_re = re.compile(
        r"^\s*(?:have|show|obtain|thus|hence|then|also|moreover|ultimately|finally|case\b|next\b|qed\b)\b"
    )

    # climb to the enclosing have/show head, but stop if we hit a block fence
    while i >= 0 and not head_re.match(lines[i]):
        if re.match(r"^\s*(?:case\b|next\b|qed\b)\b", lines[i]):
            return (-1, -1)
        i -= 1

    if i < 0 or not head_re.match(lines[i]):
        return (-1, -1)

    # NEW: if the head line itself has an inline "by …", keep only that line
    if _INLINE_BY_TAIL.search(lines[i] or ""):
        return (i, i + 1)

    # Track nested subproofs correctly from the head line outward.
    depth = 0
    j = i + 1
    while j < len(lines):
        L = lines[j]

        # Base-level one-liner endings
        if depth == 0:
            # stop immediately after a base-level "sorry"
            if (L or "").strip() == "sorry":
                j = j + 1
                break
            # do not include any fence token at base depth
            if fence_re.match(L or ""):
                break

        # Subproof bookkeeping
        if _PROOF_RE.match(L or ""):
            depth += 1
        elif _QED_RE.match(L or ""):
            depth = max(0, depth - 1)

        j += 1

    return (i, j if j > i else -1)

def _enclosing_whole_proof(lines: List[str]) -> Tuple[int, int]:
    last_qed = -1
    for i, line in enumerate(lines):
        if _QED_RE.match(line):
            last_qed = i
    if last_qed < 0:
        return (-1, -1)
    for i in range(last_qed, -1, -1):
        if _PROOF_RE.match(lines[i]):
            return (i, last_qed + 1)
    return (-1, -1)

# ========== Wrapper Stripping ==========
def _strip_wrapper_to_case_block(proposed: str, original_case_block: str) -> str:
    if not _WRAPPED_THEOREM_HEAD.match(proposed):
        return proposed
    case_name = None
    m = re.search(r"(?m)^\s*case\s*\((\w+)", original_case_block or "")
    if m:
        case_name = m.group(1)
    else:
        m = re.search(r"(?m)^\s*case\s+(\w+)", original_case_block or "")
        if m:
            case_name = m.group(1)
    lines = proposed.splitlines()
    start = None
    for i, L in enumerate(lines):
        if not _CASE_LINE_RE.match(L):
            continue
        if case_name is None or re.match(rf"^\s*case\s*\({re.escape(case_name)}\b", L) or re.match(rf"^\s*case\s+{re.escape(case_name)}\b", L):
            start = i
            break
    if start is None:
        return proposed
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _NEXT_OR_QED_RE.match(lines[j]):
            end = j
            break
    return "\n".join(lines[start:end]).rstrip()

def _strip_wrapper_to_have_show(proposed: str, original_block: str) -> str:
    # Keep only the single have/show/obtain micro-block, including any nested
    # `proof … qed` it contains, but STOP at the first base-level `sorry`
    # or base-level inline `by …`.
    lines = proposed.splitlines()
    if not lines:
        return proposed

    head_re  = re.compile(r"^\s*(have|show|obtain)\b")
    fence_re = re.compile(
        r"^\s*(?:have|show|obtain|thus|hence|then|also|moreover|ultimately|finally|case\b|next\b|qed\b)\b"
    )

    # find the first have/show/obtain head
    head_idx = next((i for i, L in enumerate(lines) if head_re.match(L)), -1)
    if head_idx == -1:
        return proposed

    out: List[str] = [lines[head_idx]]
    depth = 0

    for L in lines[head_idx + 1:]:
        # Base-level one-line endings
        if depth == 0:
            # Keep a base-level "sorry", but stop right after it
            if (L or "").strip() == "sorry":
                out.append(L)
                break
            # Do not include any new head/fence (then/also/moreover/…/case/next/qed)
            if fence_re.match(L or ""):
                break

        # Track nested subproofs (kept inside the micro-block)
        if _PROOF_RE.match(L or ""):
            depth += 1
        elif _QED_RE.match(L or ""):
            depth = max(0, depth - 1)

        out.append(L)

    # Trim any trailing whitespace lines we may have kept
    while out and out[-1].strip() == "":
        out.pop()

    # Final guard: if the last kept line is an inline 'by …' on the head, trim to that line only
    if len(out) == 1 and _INLINE_BY_TAIL.search(out[0] or ""):
        return out[0].rstrip()
    return "\n".join(out).rstrip()

def _strip_wrapper_to_subproof(proposed: str) -> str:
    if not _WRAPPED_THEOREM_HEAD.match(proposed):
        return proposed
    lines = proposed.splitlines()
    start = None
    for i, L in enumerate(lines):
        if _PROOF_RE.match(L):
            start = i
            break
    if start is None:
        return proposed
    depth, j = 1, start + 1
    while j < len(lines) and depth > 0:
        if _PROOF_RE.match(lines[j]):
            depth += 1
        elif _QED_RE.match(lines[j]):
            depth -= 1
        j += 1
    return "\n".join(lines[start:j if depth == 0 else len(lines)]).rstrip()

# ========== Safe Sorry Insertion ==========
def _find_enclosing_head(block_lines: List[str], from_idx: int) -> Optional[int]:
    for i in range(from_idx, -1, -1):
        if _HEAD_CMD_RE.match(block_lines[i] or ""):
            return i
    return None

def _apply_sequence_bounds(block_lines: List[str], idx: int) -> Tuple[int, int]:
    s = idx
    while s > 0 and _is_tactic_line(block_lines[s-1]):
        s -= 1
    e = idx + 1
    while e < len(block_lines) and _is_tactic_line(block_lines[e]):
        e += 1
    return s, e

def _replace_failing_tactics_with_sorry(block_text: str, *, full_text_lines: List[str], start_line: int, 
                                       end_line: int, isabelle, session: str, trace: bool = False) -> str:
    block_lines = block_text.splitlines()
    if not block_lines:
        return block_text    
    def build_doc(with_block_lines: List[str]) -> str:
        s0, e0 = max(0, start_line - 1), max(max(0, start_line - 1), min(end_line - 1, len(full_text_lines)))
        return "\n".join(full_text_lines[:s0] + with_block_lines + full_text_lines[e0:])
    
    while True:
        doc = build_doc(block_lines)
        _, errs = _quick_state_and_errors(isabelle, session, doc)
        err_in_block = sorted(set(l for l in _extract_error_lines(errs) if start_line <= l < end_line))
        thy = build_theory(doc.splitlines(), add_print_state=False, end_with=None)
        ok, _ = finished_ok(_run_theory_with_timeout(isabelle, session, thy, timeout_s=_ISA_VERIFY_TIMEOUT_S))
        
        if not err_in_block:
            break
        
        failing_idx = err_in_block[0] - start_line
        cand = None
        if 0 <= failing_idx < len(block_lines) and _is_tactic_line(block_lines[failing_idx]):
            cand = failing_idx
        else:
            for i in range(min(failing_idx, len(block_lines) - 1), -1, -1):
                if _is_tactic_line(block_lines[i]):
                    cand = i
                    break
            if cand is None:
                for i in range(max(0, failing_idx + 1), len(block_lines)):
                    if _is_tactic_line(block_lines[i]):
                        cand = i
                        break
        
        if cand is None:
            break

        # --- Diagnostics before modifying the block ---
        # Run Quickcheck/Nitpick on the exact failing tactic line, so we capture
        # a counterexample on the subgoal that is about to fail.
        # try:
        #     diag_txt = _run_nitpick_at_line(
        #         isabelle, session, full_text_lines,
        #         inject_before_1based=start_line + cand
        #     )
        #     if diag_txt:
        #         _log("repair", "nitpick (pre-sorry)", diag_txt, trace=trace)
        # except Exception:
        #     pass

        indent = block_lines[cand][:len(block_lines[cand]) - len(block_lines[cand].lstrip())]
        if block_lines[cand].lstrip().startswith("apply"):
            head_idx = _find_enclosing_head(block_lines, cand)
            if head_idx is not None:
                head_indent = block_lines[head_idx][:len(block_lines[head_idx]) - len(block_lines[head_idx].lstrip())]
                seq_s, seq_e = _apply_sequence_bounds(block_lines, cand)
                block_lines[seq_s:seq_e] = [f"{head_indent}proof -", f"{head_indent}  sorry", f"{head_indent}qed"]
            else:
                break
        else:
            block_lines[cand] = f"{indent}sorry"
    
    return "\n".join(block_lines)

def try_cegis_repairs(*, full_text: str, hole_span: Tuple[int, int], goal_text: str, model: Optional[str], 
                     isabelle, session: str, repair_budget_s: float = 15.0, max_ops_to_try: int = 3, 
                     beam_k: int = 1, allow_whole_fallback: bool = False, trace: bool = False, 
                     resume_stage: int = 0) -> Tuple[str, bool, str]:
    t0 = time.monotonic()
    left = lambda: max(0.0, repair_budget_s - (time.monotonic() - t0))
    current_text = full_text
    state0 = _print_state_before_hole(isabelle, session, current_text, hole_span, trace=trace)
    _log("repair", "State block", state0, trace=trace)

    if allow_whole_fallback and trace:
        print("[repair] (deprecated) allow_whole_fallback=True is ignored; driver handles regeneration.")

    prior_store: Dict[str, List[str]] = {}

    # Improvement 2 — Deterministic `arbitrary:` fix:
    # If Isabelle already reports 'locally fixed variable X', we know exactly
    # what to add.  Try it before spending any LLM budget.
    if resume_stage <= 1 and left() > 4.0:
        _, _pre_errs = _quick_state_and_errors(isabelle, session, current_text, timeout_s=6)
        _pre_err_texts = _normalize_error_texts(_pre_errs)
        _arb_fix = _maybe_fix_arbitrary(current_text, _pre_err_texts)
        if _arb_fix and _arb_fix != current_text:
            if trace:
                print("[repair] Trying deterministic arbitrary: fix…")
            _arb_thy = build_theory(_arb_fix.splitlines(), add_print_state=False, end_with=None)
            _arb_ok, _ = finished_ok(
                _run_theory_with_timeout(isabelle, session, _arb_thy, timeout_s=_ISA_VERIFY_TIMEOUT_S)
            )
            if _arb_ok:
                if trace:
                    print("[repair] arbitrary: fix verified — done!")
                return _arb_fix, True, "stage=0 arbitrary-fix"
            # Partial: keep the improved text so subsequent stages benefit
            if trace:
                print("[repair] arbitrary: fix applied but not verified; continuing with improved text")
            current_text = _arb_fix
            lines_after_arb = current_text.splitlines()
            # Recalculate hole_span (character offsets shift after the text change)
            _new_hl = _find_first_hole(lines_after_arb)
            if _new_hl is not None:
                _off = sum(len(_l) + 1 for _l in lines_after_arb[:_new_hl])
                hole_span = (_off, _off + len("sorry"))
            state0 = _print_state_before_hole(isabelle, session, current_text, hole_span, trace=trace)

    # Stage 1: have/show/obtain micro-block
    hole_line, _, lines = _hole_line_bounds(current_text, hole_span)
    anchor_line, anchor_reason = _earliest_failure_anchor(isabelle, session, current_text, default_line_0=hole_line)
    focus_line = _clamp_line_index(lines, anchor_line)
    if trace and anchor_line != hole_line:
        print(f"[repair] Retargeting from hole line {hole_line + 1} to earliest-failure line {anchor_line + 1} ({anchor_reason})")
    
    hs_s, hs_e = _enclosing_have_show_block(lines, focus_line)
    if resume_stage <= 1 and hs_s >= 0 and left() > 5.0:
        if trace:
            print("[repair] Trying have/show block repair…")
        current_text = _repair_block(current_text, lines, hs_s, hs_e, goal_text, state0, 
                                     isabelle, session, model, left, trace, "have-show", 
                                     stage=1, prior_store=prior_store)
        if current_text != full_text:
            thy = build_theory(current_text.splitlines(), add_print_state=False, end_with=None)
            ok, _ = finished_ok(_run_theory_with_timeout(isabelle, session, thy, timeout_s=_ISA_VERIFY_TIMEOUT_S))
            if ok:
                return current_text, True, "stage=1 block:have-show"
            # Stage 1 made partial progress but didn't fully solve the goal.
            # Cascade: update state and continue to Stage 2 with the improved text
            # rather than bailing out early.
            if trace:
                print("[repair] Stage 1 partial progress — cascading to Stage 2")
            lines = current_text.splitlines()
            # Re-locate the hole in the updated text so Stage 2 targets the right region
            _new_hl = _find_first_hole(lines)
            if _new_hl is not None:
                _char_off = sum(len(_l) + 1 for _l in lines[:_new_hl])
                hole_span = (_char_off, _char_off + len(lines[_new_hl]))
            anchor_line, _ = _earliest_failure_anchor(
                isabelle, session, current_text, default_line_0=(_new_hl or hole_line)
            )
            focus_line = _clamp_line_index(lines, anchor_line)
            state0 = _print_state_before_hole(isabelle, session, current_text, hole_span, trace=trace)
        else:
            lines = current_text.splitlines()
            state0 = _print_state_before_hole(isabelle, session, current_text, hole_span, trace=trace)

    # Stage 2a: Case-block
    cs, ce = _enclosing_case_block(lines, focus_line)
    if resume_stage <= 2 and cs >= 0 and left() > 5.0:
        if trace:
            print("[repair] Trying case-block repair…")
        current_text = _repair_block(current_text, lines, cs, ce, goal_text, state0, isabelle, session, 
                                     model, left, trace, "case", stage=2, prior_store=prior_store)
        if current_text != full_text:
            thy = build_theory(current_text.splitlines(), add_print_state=False, end_with=None)
            ok, _ = finished_ok(_run_theory_with_timeout(isabelle, session, thy, timeout_s=_ISA_VERIFY_TIMEOUT_S))
            if ok:
                return current_text, True, "stage=2 block:case"
            # Cascade to Stage 2b with updated text
            if trace:
                print("[repair] Stage 2a partial progress — cascading to Stage 2b")
            lines = current_text.splitlines()
            _new_hl2 = _find_first_hole(lines)
            if _new_hl2 is not None:
                _char_off2 = sum(len(_l) + 1 for _l in lines[:_new_hl2])
                hole_span = (_char_off2, _char_off2 + len(lines[_new_hl2]))
            anchor_line, _ = _earliest_failure_anchor(
                isabelle, session, current_text, default_line_0=(_new_hl2 or hole_line)
            )
            focus_line = _clamp_line_index(lines, anchor_line)
            state0 = _print_state_before_hole(isabelle, session, current_text, hole_span, trace=trace)

    # Stage 2b: Subproof
    ps, pe = _enclosing_subproof(lines, focus_line)
    if resume_stage <= 2 and ps >= 0 and left() > 3.0:
        if trace:
            print("[repair] Trying subproof repair…")
        current_text = _repair_block(current_text, lines, ps, pe, goal_text, state0, isabelle, session, 
                                     model, left, trace, "subproof", stage=2, prior_store=prior_store)
        if current_text != full_text:
            thy = build_theory(current_text.splitlines(), add_print_state=False, end_with=None)
            ok, _ = finished_ok(_run_theory_with_timeout(isabelle, session, thy, timeout_s=_ISA_VERIFY_TIMEOUT_S))
            if ok:
                return current_text, True, "stage=2 block:subproof"
            # FIX: Return False for unverified changes
            return current_text, False, "stage=2 partial-progress"
    
    if current_text != full_text:
        return current_text, False, f"stage={resume_stage} partial-progress"
    return full_text, False, f"stage={resume_stage} cegis-nohelp"

def _repair_block(current_text: str, lines: List[str], start: int, end: int, goal_text: str, 
                 state0: str, isabelle, session: str, model: Optional[str], left, trace: bool, 
                 block_type: str, stage: int, *, prior_store: Optional[Dict[str, List[str]]] = None) -> str:
    _, errs = _quick_state_and_errors(isabelle, session, current_text)
    err_texts = _normalize_error_texts(errs)
    ce = get_counterexample_hints_for_repair(isabelle, session, state0, timeout_s=10)
    block = "\n".join(lines[start:end])

    # Extract proof context and induction hypotheses from proof state
    proof_context = _extract_proof_context(current_text, start)
    ih_hints = _extract_induction_hyps(state0)

    _log("repair", f"{block_type}-block (input)", block, trace=trace)
    _log("repair", "proof_context (LLM input)", proof_context, trace=trace)
    _log("repair", "errors (LLM input)", "\n".join(err_texts) or "(none)", trace=trace)
    ce_list = ce.get("bindings", []) + ce.get("def_hints", []) if isinstance(ce, dict) else []
    _log("repair", "counterexamples (LLM input)", "\n".join(ce_list) or "(none)", trace=trace)
    if ih_hints and trace:
        print(f"[repair] IH hints: {ih_hints}")
    rounds = 3 if left() >= 18.0 else 2 if left() >= 10.0 else 1
    mem = _RepairMemory()

    # Improvement 1 — Proactive Sledgehammer:
    # Before any LLM call, replace the first sorry in the block with
    # 'by sledgehammer' and run Isabelle.  If the ATP finds a proof it prints
    # 'Try this: by (simp add: ...)' which we collect here — often skipping the
    # entire LLM budget for this hole.
    if left() > 8.0:
        _sledge_timeout = min(12, int(left() * 0.25))
        _sledge_suggs = _proactive_sledgehammer_suggestions(
            isabelle, session, current_text, start, end, timeout_s=_sledge_timeout
        )
        if _sledge_suggs and trace:
            print(f"[repair] Proactive Sledgehammer found {len(_sledge_suggs)} suggestion(s): {_sledge_suggs[:2]}")
        # Prepend ATP suggestions before any LLM-sourced ones
        _try_suggestions = _sledge_suggs + _extract_try_this_suggestions(err_texts)
    else:
        _try_suggestions = _extract_try_this_suggestions(err_texts)

    block_lines_for_tt = block.splitlines()
    for _suggestion in _try_suggestions[:2]:
        if left() <= 3.0:
            break
        _patched = _apply_try_this_to_block(_suggestion, block_lines_for_tt, lines, start, end)
        if _patched is None or _patched == current_text:
            continue
        if trace:
            print("[repair] Trying 'Try this' suggestion: " + repr(_suggestion))
        _thy = build_theory(_patched.splitlines(), add_print_state=False, end_with=None)
        _ok, _ = finished_ok(_run_theory_with_timeout(isabelle, session, _thy, timeout_s=_ISA_VERIFY_TIMEOUT_S))
        if _ok:
            if trace:
                print("[repair] 'Try this' suggestion verified — skipping LLM")
            return _patched
        # Record as failed so the LLM loop won't repeat it
        _fp = _fingerprint_block(_suggestion)
        if _fp and _fp not in mem.prev_fps:
            mem.prev_fps.add(_fp)
            mem.prev_blocks.insert(0, _suggestion)

    # Improvement 3 — Alternating temperatures:
    # Cycle through [default, 0.7] so successive rounds are more diverse.
    # Using a fixed schedule avoids doubling call count while still escaping
    # local modes that a single temperature would repeat.
    _REPAIR_TEMPS = [None, 0.7]

    # Build proposals in a few rounds; track failures and surface them to the LLM
    for rr in range(rounds):
        if left() <= 3.0:
            break
        mem.rounds = rr + 1
        why = _why_from_errors(err_texts, block_type)
        timeout = int(min(60, max(8, left() * (0.55 / max(1, rounds - rr)))))
        _temp = _REPAIR_TEMPS[rr % len(_REPAIR_TEMPS)]  # alternate each round

        # Build prior failed blocks text (trim + separators)
        prior_blocks_for_type = list(prior_store.get(block_type, [])) if isinstance(prior_store, dict) else []
        seed_list = [block] + mem.prev_blocks + prior_blocks_for_type

        # De-dup while preserving order (by fingerprint)
        seen: Set[str] = set()
        uniq: List[str] = []
        for b in seed_list:
            fpb = _fingerprint_block(b)
            if fpb and fpb not in seen:
                seen.add(fpb); uniq.append(b)
        seed_list = uniq

        if seed_list:
            fails_txt = ("\n---\n".join(_trim_block_for_prompt(b) for b in seed_list[:_MAX_PREV_BLOCKS])) or "(none)"
            _log("repair", "prior_block_failures (LLM input)", fails_txt, trace=trace)
        else:
            fails_txt = "(none)"

        try:
            blk = _propose_block_repair(
                goal=goal_text, errors=err_texts, ce_hints=ce,
                proof_context=proof_context, block_type=block_type,
                block_text=block, model=model, timeout_s=timeout, why=why,
                prior_failed_blocks=fails_txt,
                temperature=_temp,
                ih_hints=ih_hints,
            )
        except Exception:
            blk = ""
        
        if not _is_effective_block(blk):
            continue
        
        # STRICT DEDUP: If this block matches ANY prior failure, skip it immediately
        fp_new = _fingerprint_block(blk)
        all_prior_fps = set([_fingerprint_block(b) for b in (mem.prev_blocks + prior_blocks_for_type)])
        
        if fp_new in all_prior_fps:
            if trace:
                print(f"[repair] Skipping duplicate block (fingerprint: {fp_new[:8]}...)")
            continue  # Don't even try to verify, just skip
        
        before = blk
        if block_type == "case":
            blk = _strip_wrapper_to_case_block(blk, block)
        elif block_type == "have-show":
            blk = _strip_wrapper_to_have_show(blk, block)
        elif block_type == "subproof":
            blk = _strip_wrapper_to_subproof(blk)              
        if blk.strip() == block.strip():
            continue
        
        blk_with_sorry = _replace_failing_tactics_with_sorry(blk, full_text_lines=lines, start_line=start + 1, 
                                                             end_line=end + 1, isabelle=isabelle, 
                                                             session=session, trace=trace)
        _log("repair", f"{block_type}-block (output)", blk_with_sorry, trace=trace)
        
        # Record this failed candidate into local and shared stores (so next round tries differ)
        fp = _fingerprint_block(blk_with_sorry)
        if fp and fp not in mem.prev_fps:
            mem.prev_fps.add(fp)
            mem.prev_blocks.insert(0, blk_with_sorry)
            mem.prev_blocks = mem.prev_blocks[:_MAX_PREV_BLOCKS]
            if isinstance(prior_store, dict):
                lst = prior_store.setdefault(block_type, [])
                # De-dup in shared store too
                if fp not in [_fingerprint_block(x) for x in lst]:
                    lst.insert(0, blk_with_sorry)
                    del lst[_MAX_PREV_BLOCKS:]        
        
        # FIX: Properly replace the block by splitting into lines
        new_block_lines = blk_with_sorry.splitlines()
        patched_lines = lines[:start] + new_block_lines + lines[end:]
        patched = "\n".join(patched_lines)
        
        thy = build_theory(patched.splitlines(), add_print_state=False, end_with=None)
        ok, _ = finished_ok(_run_theory_with_timeout(isabelle, session, thy, timeout_s=_ISA_VERIFY_TIMEOUT_S))
        
        if ok:
            return patched
        
        # Update for next iteration - recalculate indices based on new block size
        current_text = patched
        lines = patched_lines  # Use the already-split lines
        # Adjust end index: new_end = start + len(new_block_lines)
        end = start + len(new_block_lines)
        # Update proof context for next round too
        proof_context = _extract_proof_context(current_text, start)
    
    return current_text

# ---------- Public helper: whole-proof regeneration with prior-failure banlist ----------
def regenerate_whole_proof(*, full_text: str, goal_text: str, model: Optional[str],
                           isabelle, session: str, budget_s: float = 20.0,
                           trace: bool = False,
                           prior_outline_text: Optional[str] = None,
                           prior_outline_texts: Optional[List[str]] = None,
                          ) -> Tuple[str, bool, str]:
    """
    Re-generate the last proof..qed block (or from the lemma head to EOF if no qed yet),
    feeding all previously failed outlines as a ban list so the LLM avoids repeating
    any of them — not just the immediately preceding one.

    `prior_outline_texts` is the preferred argument (a list of all failed outlines so far).
    `prior_outline_text` is kept for backwards compatibility and is merged into the list.
    """
    lines = full_text.splitlines()
    ws, we = _enclosing_whole_proof(lines)
    if ws < 0 or we <= ws:
        # Fallback: from first lemma/theorem head to EOF
        start = None
        for i, L in enumerate(lines):
            if re.match(r"^\s*(?:lemma|theorem|corollary)\b", L):
                start = i
                break
        if start is None:
            return full_text, False, "whole:region-not-found"
        ws, we = start, len(lines)

    # Simple local timer for the block repair
    t0 = time.monotonic()
    left = lambda: max(0.0, budget_s - (time.monotonic() - t0))
    # Use empty/quick state — the block prompt already carries enough context
    state0 = ""
    # Seed ban-list with ALL previously failed outlines so Stage 3 never repeats any of them
    all_priors: List[str] = list(prior_outline_texts or [])
    if prior_outline_text and prior_outline_text not in all_priors:
        all_priors.append(prior_outline_text)
    prior_store: Dict[str, List[str]] = {}
    if all_priors:
        prior_store["whole"] = all_priors
    patched = _repair_block(full_text, lines, ws, we, goal_text, state0, isabelle, session,
                            model, left, trace, "whole", stage=3, prior_store=prior_store)
    if patched != full_text:
        # _repair_block only returns a different text if it verified successfully
        return patched, True, "regen:whole-proof"
    return full_text, False, "regen:no-change"