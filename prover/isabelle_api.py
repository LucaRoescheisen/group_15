# prover/isabelle_api.py 
from __future__ import annotations

import os, json, tempfile, textwrap, re, asyncio
from typing import List, Tuple, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

# Re-export these (cli.py and experiments.py import them from here)
from isabelle_client import start_isabelle_server, get_isabelle_client, IsabelleResponse

# ------------------ Config (kept light and backwards-compatible) ------------------
try:
    from .config import EXTRA_IMPORTS  # list[str]
except Exception:
    EXTRA_IMPORTS: List[str] = []

# Optional per-call timeout (seconds) for Isabelle 'use_theories'
try:
    from .config import ISABELLE_USE_THEORIES_TIMEOUT_S  # int
except Exception:
    try:
        ISABELLE_USE_THEORIES_TIMEOUT_S = int(os.getenv("ISABELLE_USE_THEORIES_TIMEOUT_S", "").strip() or 0)
    except Exception:
        ISABELLE_USE_THEORIES_TIMEOUT_S = 60  # 0 = disabled

# ------------------ Small helpers & constants ------------------
FOOTER = "end\n"
_TIMEOUT_KWARGS = ("timeout", "timeout_s", "timeout_sec", "request_timeout")  # best-effort spellings
_SUBGOALS_RE = re.compile(r"(\d+)\s+subgoals?")

_use_calls = 0
_use_timeouts = 0
_last_call_timed_out = False


def _header(imports: Optional[List[str]] = None) -> str:
    imps = ["Main"] + list(imports or []) + list(EXTRA_IMPORTS or [])
    return f"theory Scratch\nimports {' '.join(imps)}\nbegin\n"


def _get_field(obj: Any, names: Tuple[str, ...]) -> Any:
    # dict-like
    if isinstance(obj, dict):
        for n in names:
            if n in obj:
                return obj[n]
    # attribute-style
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def _normalize_type(rt: Any) -> str:
    """Return a normalized uppercase type name ('FINISHED'/'NOTE'/...) across variants."""
    try:
        if hasattr(rt, "name"):               # Enum.name -> 'FINISHED'
            return str(rt.name).strip().upper()
        if hasattr(rt, "value"):              # Enum.value -> 'FINISHED'
            v = getattr(rt, "value")
            return (v if isinstance(v, str) else str(v)).strip().upper()
        s = str(rt).strip()
        su = s.upper()
        if "FINISHED" in su:
            return "FINISHED"
        if "NOTE" in su:
            return "NOTE"
        if su.endswith(".OK") or su == "OK" or "OK'" in su:
            return "OK"
        return su
    except Exception:
        return ""


def _decode_body_to_dict(body: Any) -> Optional[Dict[str, Any]]:
    """Body may be dict/JSON string/bytes/Pydantic model; return dict or None."""
    if body is None:
        return None
    if isinstance(body, (bytes, bytearray)):
        try:
            body = body.decode("utf-8", "replace")
        except Exception:
            body = str(body)
    if isinstance(body, dict):
        return body
    # Handle Pydantic v2 models (e.g. UseTheoriesResults from isabelle_client >=1.x)
    if hasattr(body, "model_dump"):
        try:
            return body.model_dump()
        except Exception:
            pass
    # Handle Pydantic v1 / dataclasses / plain objects with __dict__
    if hasattr(body, "__dict__") and not isinstance(body, type):
        try:
            return vars(body)
        except Exception:
            pass
    try:
        return json.loads(body)
    except Exception:
        return None


# ------------------ Public utils ------------------
def _write_tmp_theory(theory_text: str) -> Tuple[str, str]:
    """
    Legacy helper (not used by this module). Creates a temp dir and writes Scratch.thy.
    Returns (tmpdir_path, file_path). Note: the temp directory lifetime is not managed here.
    """
    tmpdir = tempfile.TemporaryDirectory()
    p = os.path.join(tmpdir.name, "Scratch.thy")
    with open(p, "w", encoding="utf-8") as f:
        f.write(theory_text)
    # DO NOT change the return shape for compatibility
    return tmpdir.name, p


def parse_n_subgoals(msg: str) -> Optional[int]:
    """Heuristic extractor for the number of subgoals from a print_state block."""
    m = _SUBGOALS_RE.search(msg or "")
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def build_theory(steps: List[str], add_print_state: bool, end_with: Optional[str]) -> str:
    body = [steps[0]] + ["  " + s for s in steps[1:]]
    if add_print_state:
        body.append("  print_state")
    if end_with:
        body.append("  " + end_with)
    return textwrap.dedent(_header() + "\n".join(body) + "\n\n" + FOOTER)


def _use_theories_call(isabelle, *, session_id: str, master_dir: str, timeout_s: Optional[int] = None) -> List[IsabelleResponse]:
    """Internal: best-effort pass through native timeout kwargs (if supported)."""
    if timeout_s is not None and int(timeout_s or 0) > 0:
        # Try native timeout kwarg spellings first (best-effort). Some clients ignore these,
        # so the caller still enforces a wall-clock timeout via Future.result(...).
        for kw in _TIMEOUT_KWARGS:
            try:
                return list(
                    isabelle.use_theories(
                        theories=["Scratch"], session_id=session_id, master_dir=master_dir, **{kw: int(timeout_s)}
                    )
                )
            except TypeError:
                continue
            except Exception:
                return []
    return list(isabelle.use_theories(theories=["Scratch"], session_id=session_id, master_dir=master_dir))


def run_theory(
    isabelle,
    session_id: str,
    theory_text: str,
    timeout_s: Optional[int] = None,
) -> List[IsabelleResponse]:
    """Run a small throwaway theory through Isabelle with a wall-clock timeout.

    IMPORTANT: Isabelle-side timeouts are not always authoritative for our pipeline.
    This wrapper enforces a wall-clock timeout (thread + Future timeout fallback)
    and records whether the *last* call timed out so callers can avoid false positives.

    - timeout_s: seconds; if None, defaults to ISABELLE_USE_THEORIES_TIMEOUT_S.
      Use 0/<=0 to disable.

    Returns the collected IsabelleResponse list; on timeout returns an empty list.
    """
    global _use_calls, _last_call_timed_out
    _use_calls += 1
    _last_call_timed_out = False

    # Optional override: if SCRATCH_MASTER_DIR is set, write Scratch.thy there
    # instead of a tempdir. This is required when the inline lemma imports a
    # local theory file (e.g. `imports MiniF2F_Base`) so Isabelle's theory
    # loader can resolve it relative to the master_dir. Tempdir is cleaned up
    # only when we created it.
    _override_dir = os.environ.get("SCRATCH_MASTER_DIR") or ""
    tmpdir = None
    if _override_dir and os.path.isdir(_override_dir):
        master_dir_real = _override_dir
    else:
        tmpdir = tempfile.TemporaryDirectory()
        master_dir_real = tmpdir.name
    try:
        p = os.path.join(master_dir_real, "Scratch.thy")
        with open(p, "w", encoding="utf-8") as f:
            f.write(theory_text)

        # On Windows, Isabelle runs inside Cygwin via the wrapper bat.
        # The server needs Cygwin-style paths (/cygdrive/c/...).
        master_dir = master_dir_real
        if os.name == "nt":
            import re as _re
            s = master_dir.replace("\\", "/")
            m = _re.match(r"^([A-Za-z]):(.*)", s)
            if m:
                master_dir = f"/cygdrive/{m.group(1).lower()}{m.group(2)}"

        # Resolve wall-clock timeout (seconds)
        if timeout_s is None:
            timeout_s = int(ISABELLE_USE_THEORIES_TIMEOUT_S or 0)
        else:
            try:
                timeout_s = int(timeout_s)
            except Exception:
                timeout_s = 0
        if timeout_s > 0:
            # Always enforce a wall-clock timeout (even if native timeouts exist but are ignored).
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_use_theories_call, isabelle, session_id=session_id, master_dir=master_dir, timeout_s=timeout_s)
                try:
                    return fut.result(timeout=timeout_s)
                except FuturesTimeout:
                    global _use_timeouts
                    _use_timeouts += 1
                    _last_call_timed_out = True
                    return []

        # No timeout requested → direct call
        return list(isabelle.use_theories(theories=["Scratch"], session_id=session_id, master_dir=master_dir))
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()
        else:
            # Override dir is reused; just remove the Scratch.thy we wrote.
            try:
                os.remove(p)
            except Exception:
                pass


def last_call_timed_out() -> bool:
    """Whether the most recent run_theory() call hit the wall-clock timeout."""
    return bool(_last_call_timed_out)


def finished_ok(resps: List[IsabelleResponse]) -> Tuple[bool, Dict[str, Any]]:
    """
    Return success if **any** FINISHED block reports ok=true (or result='ok').
    Robust across client variants:
      - response type can be Enum or str
      - response body may be bytes, JSON string, or dict
      - dict-like or attribute-style access
    """
    # If our wall-clock timeout fired, treat as NOT proved (prevents false positives).
    if last_call_timed_out():
        return False, {"timeout": True}

    any_ok = False
    last_obj: Dict[str, Any] = {}

    for r in (resps or []):
        if _normalize_type(_get_field(r, ("response_type", "type", "kind", "tag", "name"))) != "FINISHED":
            continue
        obj = _decode_body_to_dict(_get_field(r, ("response_body", "body", "message", "payload")))
        if not isinstance(obj, dict):
            continue
        last_obj = obj  # track last FINISHED
        # Some client variants can produce partial/unfinished results; keep the check strict.
        if bool(obj.get("ok", False)) or str(obj.get("result", "")).lower() == "ok":
            # If the FINISHED payload itself indicates a timeout, do not accept.
            if str(obj.get("timeout", "")).lower() in ("1", "true", "yes"):
                continue
            any_ok = True

    return any_ok, (last_obj or {})


def last_print_state_block(resps: List[IsabelleResponse]) -> str:
    """
    Return a print_state-like goal block as a plain string.
    Backwards-compatible:
      1) Prefer the last NOTE/writeln 'goal' block if present (legacy behaviour).
      2) Otherwise, fall back to FINISHED JSON 'nodes[*].messages[*]' where many
         Isabelle client builds now place the pretty-printed goal text.
    """
    txt = ""
    # 1) Legacy NOTE/writeln path
    for r in (resps or []):
        if _normalize_type(_get_field(r, ("response_type", "type", "kind", "tag", "name"))) != "NOTE":
            continue
        body = _get_field(r, ("response_body", "body", "message", "payload"))
        obj = _decode_body_to_dict(body)
        if not isinstance(obj, dict):
            continue
        if obj.get("kind") == "writeln":
            msg = str(obj.get("message", ""))
            if ("subgoal" in msg) or ("goal (" in msg) or ("goal\n" in msg):
                txt = msg
    if txt:
        return txt
    # 2) FINISHED JSON fallback (authoritative on some builds)
    for r in (resps or []):
        if _normalize_type(_get_field(r, ("response_type", "type", "kind", "tag", "name"))) != "FINISHED":
            continue
        obj = _decode_body_to_dict(_get_field(r, ("response_body", "body", "message", "payload")))
        if not isinstance(obj, dict):
            continue
        for node in (obj.get("nodes") or []):
            for m in (node.get("messages") or []):
                if str(m.get("kind", "")).lower() != "writeln":
                    continue
                msg = str(m.get("message", "") or "")
                # Prefer explicit goal/subgoal blocks; also accept our ML markers
                if ("subgoal" in msg) or ("goal (" in msg) or ("goal\n" in msg) \
                   or msg.startswith("[LLM_SUBGOAL]"):
                    txt = msg or txt
    return txt


def use_calls_count() -> int:
    return _use_calls


def use_timeouts_count() -> int:
    return int(_use_timeouts)


def session_start(isabelle, session: str = "HOL") -> str:
    """Start an Isabelle session and return the session_id string.

    Handles both old isabelle_client (<1.x) that returned a plain string
    and new versions (>=1.1.1) that return a list of IsabelleResponse objects.
    """
    result = isabelle.session_start(session=session)
    # New API: returns a list of responses
    if isinstance(result, (list, tuple)):
        for r in result:
            # Check FINISHED response with session_id
            body = getattr(r, "response_body", None)
            if body is not None:
                # Pydantic model with session_id attribute
                sid = getattr(body, "session_id", None)
                if sid:
                    return str(sid)
                # dict-like body
                if isinstance(body, dict) and "session_id" in body:
                    return str(body["session_id"])
                # Try model_dump
                if hasattr(body, "model_dump"):
                    try:
                        d = body.model_dump()
                        if "session_id" in d:
                            return str(d["session_id"])
                    except Exception:
                        pass
        raise ValueError(f"Could not extract session_id from session_start response: {result}")
    # Old API: returns the session_id string directly
    return str(result)


__all__ = [
    # re-exports
    "start_isabelle_server", "get_isabelle_client", "IsabelleResponse",
    # config-driven helpers
    "_header", "FOOTER", "parse_n_subgoals", "build_theory", "run_theory",
    "finished_ok", "last_print_state_block", "use_calls_count", "use_timeouts_count",
    "last_call_timed_out",
    "graceful_terminate",
    "session_start",
]

# Cross-runtime shutdown helper (works for multiprocessing.Process and subprocess.Popen)
def graceful_terminate(proc, timeout_s: int = 3) -> None:
    """
    Terminate an Isabelle server process robustly across runtimes.
    Tries terminate→wait(timeout)→join(timeout)→kill, ignoring errors.
    """
    if proc is None:
        return
    try:
        proc.terminate()
        # subprocess.Popen has .wait/.kill; multiprocessing.Process has .join/.kill (3.7+)
        if hasattr(proc, "wait"):
            try:
                proc.wait(timeout=timeout_s)
            except Exception:
                try:
                    if hasattr(proc, "kill"):
                        proc.kill()
                    proc.wait(timeout=timeout_s)
                except Exception:
                    pass
        elif hasattr(proc, "join"):
            try:
                proc.join(timeout=timeout_s)
            except Exception:
                pass
            try:
                if hasattr(proc, "kill") and getattr(proc, "is_alive", lambda: False)():
                    proc.kill()
            except Exception:
                pass
    finally:
        # Make sure transports/pipes are closed before the loop is closed
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                loop.call_soon_threadsafe(lambda: None)
        except RuntimeError:
            pass