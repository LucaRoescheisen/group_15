#!/usr/bin/env python3
"""
sledge_only.py — Standalone Sledgehammer-only runner via Isabelle server protocol.

Flow:
1) Start the Isabelle server (uses ISABELLE_INST_DIR on Windows for Cygwin bat).
2) Open a HOL session.
3) For each goal, submit a theory with `sledgehammer [...]` + `sorry` to the server.
4) Parse NOTE/FINISHED messages for "Try this: by ..." suggestions.
5) Re-verify the first valid suggestion by re-submitting without `sorry`.
"""

from __future__ import annotations
import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import warnings
from pathlib import Path
from typing import List, Optional, Tuple

# Suppress the noisy asyncio transport warning on Windows/Python 3.14 shutdown
warnings.filterwarnings("ignore", category=ResourceWarning)

# On Windows, set ISABELLE_INST_DIR so isabelle_client finds the bundled Cygwin bat.
_INST = os.environ.get("ISABELLE_INST_DIR", r"C:\Program Files\Isabelle2025-2")
os.environ.setdefault("ISABELLE_INST_DIR", _INST)

from isabelle_client import start_isabelle_server, get_isabelle_client  # noqa: E402

# ---------- Unicode → Isabelle ----------
UNICODE_MAP = {
    "⟹": r"\<Longrightarrow>", "⇒": r"\<Longrightarrow>",
    "⟶": r"\<longrightarrow>", "→": r"\<longrightarrow>",
    "⟷": r"\<longleftrightarrow>", "↔": r"\<longleftrightarrow>",
    "¬": r"\<not>", "∧": r"\<and>", "∨": r"\<or>",
    "∀": r"\<forall>", "∃": r"\<exists>", "⋀": r"\<And>",
    "≤": r"\<le>", "≥": r"\<ge>", "≠": r"\<noteq>",
    "⊆": r"\<subseteq>", "⊇": r"\<supseteq>", "⊂": r"\<subset>", "⊃": r"\<supset>",
    "∈": r"\<in>", "∉": r"\<notin>",
    "∪": r"\<union>", "∩": r"\<inter>", "∖": r"\<setminus>",
    "⋃": r"\<Union>", "⋂": r"\<Inter>",
    "λ": r"\<lambda>",
}
UNICODE_RE = re.compile("|".join(map(re.escape, sorted(UNICODE_MAP.keys(), key=len, reverse=True))))

def to_isabelle_symbols(s: str) -> str:
    return UNICODE_RE.sub(lambda m: UNICODE_MAP[m.group(0)], s)

# ---------- Theory text builders ----------
def sanitize_imports(imports: List[str]) -> List[str]:
    imps = list(dict.fromkeys(imports))
    if "Main" not in imps:
        imps.insert(0, "Main")
    if "List" in imps and "Main" in imps:
        imps = [x for x in imps if x != "List"]
    return imps

def thy_probe_text(imports: List[str], goal: str, sh_timeout: int, provers: Optional[str]) -> str:
    imps = " ".join(sanitize_imports(imports))
    g = to_isabelle_symbols(goal)
    opts = [f"timeout = {sh_timeout}", "verbose"]
    if provers:
        opts.append(f"provers = {provers}")
    sh = "  sledgehammer [" + ", ".join(opts) + "]"
    return (
        f"theory Scratch\n  imports {imps}\nbegin\n\n"
        f'lemma goal: "{g}"\n'
        f"{sh}\n"
        f"  sorry\nend\n"
    )

def thy_verify_text(imports: List[str], goal: str, by_text: str) -> str:
    imps = " ".join(sanitize_imports(imports))
    g = to_isabelle_symbols(goal)
    return (
        f"theory Scratch\n  imports {imps}\nbegin\n\n"
        f'lemma goal: "{g}"\n'
        f"  {by_text}\nend\n"
    )

# ---------- Response parsing ----------
TIMING_TAIL = re.compile(r"\s*\(\s*\d+(?:\.\d+)?\s*(?:ms|s)\s*\)?\s*$")
TRY_THIS_RE = re.compile(r"(?i)\btry this:\s*(.*)")
BY_LINE_RE = re.compile(r"\b(by\s*\([^)]+\)|by\s+\w[\w\s]*)")

def _decode_body(body) -> dict:
    if body is None:
        return {}
    if isinstance(body, dict):
        return body
    if isinstance(body, (bytes, bytearray)):
        try:
            body = body.decode("utf-8", "replace")
        except Exception:
            body = str(body)
    if isinstance(body, str):
        try:
            return json.loads(body)
        except Exception:
            return {"raw": body}
    # Pydantic model or other object with model_dump
    if hasattr(body, "model_dump"):
        try:
            return body.model_dump()
        except Exception:
            pass
    # fallback: try __dict__
    try:
        return vars(body)
    except Exception:
        return {}

def _response_type(r) -> str:
    rt = getattr(r, "response_type", None)
    if rt is None:
        return ""
    if hasattr(rt, "name"):
        return rt.name.upper()
    return str(rt).upper()

def _get_field(obj, key: str):
    """Get field from dict or object attribute."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)

def _iter_node_messages(node) -> List[str]:
    """Extract message strings from a node (dict or Pydantic object)."""
    out: List[str] = []
    # node.messages can be a list of dicts or objects
    messages = _get_field(node, "messages") or []
    for m in messages:
        msg = (_get_field(m, "message") or _get_field(m, "text") or "")
        if msg:
            out.append(str(msg))
    return out

def _all_messages(responses) -> List[str]:
    msgs: List[str] = []
    for r in (responses or []):
        body_obj = getattr(r, "response_body", None)
        body = _decode_body(body_obj)
        # Top-level message field
        for key in ("message", "text", "content"):
            v = body.get(key)
            if v:
                msgs.append(str(v))
        # Nested nodes (FINISHED use_theories payload)
        nodes_raw = body.get("nodes") or _get_field(body_obj, "nodes") or []
        for node in nodes_raw:
            msgs.extend(_iter_node_messages(node))
            # node may be a dict with nested node_messages
            if isinstance(node, dict):
                for m in node.get("messages", []):
                    msg = m.get("message") or m.get("text") or ""
                    if msg:
                        msgs.append(str(msg))
        # raw fallback
        raw = body.get("raw")
        if raw:
            msgs.append(str(raw))
    return msgs

def extract_suggestions(responses) -> List[str]:
    out: List[str] = []
    all_text = "\n".join(_all_messages(responses))
    lines = all_text.splitlines()
    for i, ln in enumerate(lines):
        m = TRY_THIS_RE.search(ln)
        if m:
            s = m.group(1).strip()
            # naive continuation
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt.startswith("by ") or (s and not s.endswith(".") and nxt and not nxt.endswith(":")):
                    s = (s + " " + nxt).strip()
            s = TIMING_TAIL.sub("", s).rstrip(".").strip()
            if s and ("by " in s or s.startswith("by")) and s not in out:
                out.append(s)
    # fallback: any "by (...)" line
    for ln in lines:
        m = BY_LINE_RE.search(ln)
        if m:
            s = TIMING_TAIL.sub("", m.group(1)).strip()
            if s not in out:
                out.append(s)
    return out

def finished_ok(responses) -> bool:
    for r in (responses or []):
        rt = _response_type(r)
        if "FINISHED" not in rt:
            continue
        body_obj = getattr(r, "response_body", None)
        # Try attribute access first (Pydantic model)
        ok_val = _get_field(body_obj, "ok")
        if ok_val is None:
            body = _decode_body(body_obj)
            ok_val = body.get("ok")
            timeout_val = str(body.get("timeout", "")).lower()
        else:
            timeout_val = str(_get_field(body_obj, "timeout") or "").lower()
        if bool(ok_val) and timeout_val not in ("1", "true", "yes"):
            return True
    return False

# ---------- Core prover ----------
def _to_cygwin(p: str) -> str:
    """Convert Windows path to Cygwin path for the Isabelle server."""
    s = p.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):(.*)", s)
    if m:
        return f"/cygdrive/{m.group(1).lower()}{m.group(2)}"
    return s

def _run_theory(isabelle, session_id: str, theory_text: str, timeout_s: int):
    """Write Scratch.thy to a temp dir and call use_theories. Returns responses."""
    with tempfile.TemporaryDirectory(prefix="sledge_") as tmp:
        thy_path = os.path.join(tmp, "Scratch.thy")
        with open(thy_path, "w", encoding="utf-8") as f:
            f.write(theory_text)
        # Isabelle server runs in Cygwin — pass Cygwin path
        master_dir = _to_cygwin(tmp)
        try:
            responses = list(isabelle.use_theories(
                theories=["Scratch"],
                session_id=session_id,
                master_dir=master_dir,
            ))
        except Exception as e:
            print(f"  [warning] use_theories: {e}", flush=True)
            responses = []
        return responses

def prove_with_sledgehammer(
    isabelle,
    session_id: str,
    goal: str,
    imports: List[str],
    sh_timeout: int,
    goal_timeout: int,
    provers: Optional[str],
) -> Tuple[bool, Optional[str], List[str]]:
    # Phase 1: run sledgehammer
    probe_thy = thy_probe_text(imports, goal, sh_timeout, provers)
    resps = _run_theory(isabelle, session_id, probe_thy, goal_timeout)
    msgs = _all_messages(resps)

    suggestions = extract_suggestions(resps)

    # Phase 2: verify each suggestion
    for by in suggestions:
        verify_thy = thy_verify_text(imports, goal, by)
        vresps = _run_theory(isabelle, session_id, verify_thy, goal_timeout)
        if finished_ok(vresps):
            return True, by, msgs

    return False, None, msgs

# ---------- CLI ----------
def read_goals(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Sledgehammer-only baseline (Isabelle server protocol)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--goal", type=str)
    src.add_argument("--file", type=str)
    ap.add_argument("--imports", nargs="+", default=["Main"])
    ap.add_argument("--sledge-timeout", type=int, default=30)
    ap.add_argument("--goal-timeout", type=int, default=120)
    ap.add_argument("--provers", type=str, default=None)
    ap.add_argument("--print-logs", action="store_true")
    ap.add_argument("--log-lines", type=int, default=20)
    ap.add_argument("--isabelle-inst-dir", type=str,
                    default=os.environ.get("ISABELLE_INST_DIR", r"C:\Program Files\Isabelle2025-2"))
    return ap.parse_args()

def print_log_snippet(msgs: List[str], n: int) -> None:
    lines = [l for m in msgs for l in m.splitlines() if l.strip()]
    if not lines:
        print("  (no messages)")
        return
    for ln in lines[:n]:
        print(f"  {ln}")
    if len(lines) > n:
        print(f"  ... ({len(lines) - n} more lines)")

def main() -> None:
    args = parse_args()
    os.environ["ISABELLE_INST_DIR"] = args.isabelle_inst_dir

    goals = [args.goal] if args.goal else read_goals(args.file)
    print("=== Sledgehammer-only baseline (server protocol) ===")
    print(f"Goals: {len(goals)} | imports: {' '.join(sanitize_imports(args.imports))}")
    print(f"sledge-timeout: {args.sledge_timeout}s | goal-timeout: {args.goal_timeout}s")
    print(f"ISABELLE_INST_DIR: {args.isabelle_inst_dir}")

    print("\nStarting Isabelle server...", flush=True)
    try:
        server_info, proc = start_isabelle_server(name="isabelle", log_file="isabelle_server.log")
    except Exception as e:
        print(f"ERROR: Failed to start Isabelle server: {e}")
        sys.exit(1)
    print(f"Server: {server_info.strip()}")

    isabelle = get_isabelle_client(server_info)
    try:
        session_resps = isabelle.session_start(session="HOL")
        # Extract session_id from the FINISHED response
        session_id = None
        for r in session_resps:
            rt = _response_type(r)
            if "FINISHED" in rt:
                body = getattr(r, "response_body", None)
                if hasattr(body, "session_id"):
                    session_id = body.session_id
                    break
                d = _decode_body(body)
                if "session_id" in d:
                    session_id = d["session_id"]
                    break
        if not session_id:
            print(f"ERROR: Could not extract session_id from: {session_resps}")
            try:
                proc.terminate()
            except Exception:
                pass
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to start HOL session: {e}")
        try:
            proc.terminate()
        except Exception:
            pass
        sys.exit(1)
    print(f"Session ID: {session_id}\n", flush=True)

    ok = 0
    times: List[float] = []
    try:
        for i, g in enumerate(goals, 1):
            print(f"[{i}/{len(goals)}] {g}", flush=True)
            t0 = time.time()
            success, method, msgs = prove_with_sledgehammer(
                isabelle, session_id, g,
                args.imports, args.sledge_timeout, args.goal_timeout, args.provers
            )
            dt = time.time() - t0
            times.append(dt)
            if success:
                ok += 1
                print(f"  -> OK    ({dt:.1f}s)  {method}")
            else:
                print(f"  -> FAIL  ({dt:.1f}s)")
                if args.print_logs:
                    print_log_snippet(msgs, args.log_lines)
            sys.stdout.flush()
    finally:
        try:
            isabelle.shutdown()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass

    mid = sorted(times)[len(times) // 2] if times else 0.0
    avg = sum(times) / len(times) if times else 0.0
    print("\n=== Summary ===")
    print(f"Success: {ok}/{len(goals)} ({ok / max(1, len(goals)) * 100:.1f}%)")
    print(f"Median time: {mid:.1f}s | Average time: {avg:.1f}s")

if __name__ == "__main__":
    main()
