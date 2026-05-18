"""
check_failures.py — run Nitpick/Quickcheck on every failing goal to determine
theorem vs non-theorem status. No changes to repo code needed; reads output only.
"""
import os, sys, tempfile, warnings
warnings.filterwarnings("ignore", category=ResourceWarning)

os.environ.setdefault("ISABELLE_INST_DIR", r"C:\Program Files\Isabelle2025-2")
os.environ.setdefault("PYTHONUTF8", "1")

sys.path.insert(0, os.path.dirname(__file__))
from prover.isabelle_api import start_isabelle_server, get_isabelle_client, session_start, run_theory, finished_ok, last_print_state_block

GOALS = [
    # (label, goal_statement, notes)
    ("lists.txt #1",
     'map f (filter p xs) = filter (\\<lambda>x. p x) (map f xs)',
     "Requires induction over xs"),
    ("nat.txt #1", '(0::nat) + n = n',          "Basic arithmetic, induction"),
    ("nat.txt #2", 'n + (0::nat) = n',           "Basic arithmetic, induction"),
    ("nat.txt #3", '(n::nat) \\<le> n',          "Reflexivity, induction"),
    ("nat.txt #4", '(n::nat) + m = m + n',       "Commutativity, induction"),
    ("nat.txt #5", 'n + (m + (k::nat)) = (n + m) + k', "Associativity, induction"),
    ("easy #58",   'take n xs = take n (xs @ ys) \\<longleftrightarrow> n \\<le> length xs',
     "Biconditional about take/append"),
    ("easy #64",
     'zip (map f xs) (map g ys) = map (\\<lambda>p. (f (fst p), g (snd p))) (zip xs ys)',
     "zip/map interaction"),
    ("easy #80",
     'sum_list (map (\\<lambda>_. (k::nat)) xs) = k * length xs',
     "Requires induction over xs"),
    ("easy #98",   'map_option id o = o',        "Fast fail — o is composition operator"),
]

HEADER = "theory Scratch\nimports Main\nbegin\n\n"
FOOTER = "\nend\n"

def theory_for(goal: str) -> str:
    return (
        HEADER
        + f'lemma check: "{goal}"\n'
        + '  nitpick [timeout = 15, verbose]\n'
        + '  quickcheck [timeout = 10]\n'
        + '  oops\n'
        + FOOTER
    )

def extract_verdict(resps) -> str:
    """Pull nitpick/quickcheck messages from the response."""
    msgs = []
    for r in (resps or []):
        body = getattr(r, "response_body", None)
        if body is None:
            continue
        nodes = None
        if hasattr(body, "nodes"):
            nodes = body.nodes
        elif isinstance(body, dict):
            nodes = body.get("nodes", [])
        for node in (nodes or []):
            ms = getattr(node, "messages", None) or (node.get("messages", []) if isinstance(node, dict) else [])
            for m in (ms or []):
                kind = getattr(m, "kind", None) or (m.get("kind","") if isinstance(m, dict) else "")
                msg  = getattr(m, "message", None) or (m.get("message","") if isinstance(m, dict) else "")
                if any(k in str(msg).lower() for k in ("nitpick", "quickcheck", "counterexample", "no counterexample", "potentially spurious")):
                    msgs.append(str(msg).strip())
    return "\n  ".join(msgs) if msgs else "(no nitpick/quickcheck output captured)"


def main():
    print("Starting Isabelle server...")
    server_info, proc = start_isabelle_server(name="checker", log_file="checker.log")
    isabelle = get_isabelle_client(server_info)
    session_id = session_start(isabelle, session="HOL")
    print(f"Session: {session_id}\n")
    print("=" * 70)

    try:
        for label, goal, notes in GOALS:
            print(f"\n[{label}]  {notes}")
            print(f"  Goal: {goal[:80]}")
            thy = theory_for(goal)
            resps = run_theory(isabelle, session_id, thy, timeout_s=40)
            ok, _ = finished_ok(resps)
            verdict = extract_verdict(resps)
            status = "PROVED (unexpected!)" if ok else "not proved (expected)"
            print(f"  Isabelle status : {status}")
            print(f"  Nitpick/QC      : {verdict}")
            print()
    finally:
        try:
            isabelle.shutdown()
        except Exception:
            pass
        try:
            proc.terminate(); proc.wait(timeout=3)
        except Exception:
            pass

    print("=" * 70)
    print("Done.")

if __name__ == "__main__":
    main()
