"""
check_mid_failures.py — Nitpick/Quickcheck on all 26 mid-test failures.
"""
import os, sys, warnings
warnings.filterwarnings("ignore", category=ResourceWarning)

os.environ.setdefault("ISABELLE_INST_DIR", r"C:\Program Files\Isabelle2025-2")
os.environ.setdefault("PYTHONUTF8", "1")
sys.path.insert(0, os.path.dirname(__file__))

from prover.isabelle_api import start_isabelle_server, get_isabelle_client, session_start, run_theory, finished_ok

GOALS = [
    # (label, goal, time_s, notes)
    ("mid #6",  "(\\<not> \\<exists>x. P x) \\<longleftrightarrow> (\\<forall>x. \\<not> P x)", 2.1, "fast-fail — De Morgan for quantifiers"),
    ("mid #8",  "(\\<exists>x. P x) \\<longrightarrow> (\\<forall>y. P y \\<longrightarrow> (\\<exists>z. P z))", 2.1, "fast-fail — trivially true"),
    ("mid #9",  "(\\<exists>x. P x) \\<longrightarrow> (\\<exists>y. P y \\<and> (\\<exists>z. P z))", 2.1, "fast-fail — trivially true"),
    ("mid #24", "finite A \\<Longrightarrow> card (A \\<union> B) + card (A \\<inter> B) = card A + card B", 26.6, "ATP-exhausted"),
    ("mid #34", "(f ` A) \\<inter> (f ` B) \\<subseteq> f ` (A \\<inter> B)", 26.2, "SUSPECT non-theorem — false for non-injective f"),
    ("mid #36", "sym r \\<Longrightarrow> sym (r\\<inverse>)", 2.1, "fast-fail"),
    ("mid #39", "trans r \\<Longrightarrow> trans (r\\<inverse>)", 2.1, "fast-fail"),
    ("mid #40", "refl r \\<Longrightarrow> refl (r\\<inverse>)", 2.1, "fast-fail"),
    ("mid #41", "antisym r \\<Longrightarrow> antisym (r\\<inverse>)", 2.1, "fast-fail"),
    ("mid #44", "Domain (r O s) = Domain s \\<inter> (s -` (Domain r))", 2.1, "fast-fail"),
    ("mid #45", "Range (r O s) = Range r \\<inter> (r `` (Range s))", 26.2, "ATP-exhausted"),
    ("mid #66", "(m + n) - n = (m::nat)", 18.7, "ATP-exhausted — nat subtraction"),
    ("mid #69", "n \\<le> m \\<longrightarrow> n \\<le> m + (k::nat)", 28.5, "ATP-exhausted"),
    ("mid #70", "n \\<le> m \\<longrightarrow> n \\<le> m * k + (m::nat)", 27.5, "ATP-exhausted"),
    ("mid #71", "(0::nat) < n \\<longrightarrow> m div n * n \\<le> m", 24.4, "ATP-exhausted"),
    ("mid #72", "n \\<le> m \\<longrightarrow> m - n + n = (m::nat)", 26.5, "ATP-exhausted"),
    ("mid #73", "n \\<le> m \\<longrightarrow> m - (m - n) = (n::nat)", 26.5, "ATP-exhausted"),
    ("mid #75", "abs (a * b) = abs (a::int) * abs b", 17.0, "ATP-exhausted"),
    ("mid #76", "abs (a + b) \\<le> abs (a::int) + abs b", 25.4, "ATP-exhausted — triangle inequality"),
    ("mid #77", "abs (-(a::int)) = abs a", 20.4, "ATP-exhausted"),
    ("mid #78", "min a b + max a b = a + (b::int)", 22.9, "ATP-exhausted"),
    ("mid #83", "(a::int) \\<le> b \\<longrightarrow> a * c \\<le> b * c", 28.9, "SUSPECT non-theorem — false for negative c"),
    ("mid #85", "(a::int) \\<le> b \\<longrightarrow> a mod c \\<le> b mod c \\<or> c=0", 27.7, "SUSPECT non-theorem"),
    ("mid #96", "prod_case f (a,b) = f a b", 28.7, "ATP-exhausted — deprecated name"),
    ("mid #99", "split f (a,b) = f a b", 27.2, "ATP-exhausted — deprecated name"),
    ("mid #100","(case (a,b) of (x,y) \\<Rightarrow> P x y) = P a b", 2.2, "fast-fail"),
]

HEADER = "theory Scratch\nimports Main\nbegin\n\n"
FOOTER = "\nend\n"

def theory_for(goal: str) -> str:
    return (
        HEADER
        + f'lemma check: "{goal}"\n'
        + '  nitpick [timeout = 15]\n'
        + '  quickcheck [timeout = 10]\n'
        + '  oops\n'
        + FOOTER
    )

def extract_verdict(resps) -> str:
    msgs = []
    for r in (resps or []):
        body = getattr(r, "response_body", None)
        if body is None:
            continue
        nodes = getattr(body, "nodes", None) or (body.get("nodes", []) if isinstance(body, dict) else [])
        for node in (nodes or []):
            ms = getattr(node, "messages", None) or (node.get("messages", []) if isinstance(node, dict) else [])
            for m in (ms or []):
                msg = str(getattr(m, "message", None) or (m.get("message","") if isinstance(m, dict) else ""))
                if any(k in msg.lower() for k in ("nitpick", "quickcheck", "counterexample", "no counterexample", "ran out")):
                    # Keep only first line for brevity
                    msgs.append(msg.strip().splitlines()[0])
    return " | ".join(msgs) if msgs else "(no output)"

def main():
    print("Starting Isabelle server...")
    server_info, proc = start_isabelle_server(name="mid_checker", log_file="mid_checker.log")
    isabelle = get_isabelle_client(server_info)
    sid = session_start(isabelle, session="HOL")
    print(f"Session: {sid}\n{'='*70}\n")
    try:
        fast_fail, atp_exhausted, non_theorem, theorem, inconclusive = [], [], [], [], []
        for label, goal, t, notes in GOALS:
            print(f"[{label}] {notes}")
            thy = theory_for(goal)
            resps = run_theory(isabelle, sid, thy, timeout_s=40)
            verdict = extract_verdict(resps)
            print(f"  {verdict}\n")
            # Classify
            entry = (label, goal, t, notes, verdict)
            if "counterexample" in verdict.lower() and "no counterexample" not in verdict.lower():
                non_theorem.append(entry)
            elif "no counterexample" in verdict.lower():
                theorem.append(entry)
            elif t < 5:
                fast_fail.append(entry)
            elif "ran out" in verdict.lower():
                inconclusive.append(entry)
            else:
                atp_exhausted.append(entry)
    finally:
        try: isabelle.shutdown()
        except Exception: pass
        try: proc.terminate(); proc.wait(timeout=3)
        except Exception: pass

    print("\n" + "="*70)
    print("=== SUMMARY ===")
    print(f"\nNon-theorems ({len(non_theorem)}):")
    for e in non_theorem: print(f"  {e[0]}: {e[1][:60]}")
    print(f"\nTheorems (no counterexample) ({len(theorem)}):")
    for e in theorem: print(f"  {e[0]}: {e[1][:60]}")
    print(f"\nFast-fail ({len(fast_fail)}):")
    for e in fast_fail: print(f"  {e[0]}: {e[1][:60]}")
    print(f"\nInconclusive ({len(inconclusive)}):")
    for e in inconclusive: print(f"  {e[0]}: {e[1][:60]}")
    print(f"\nOther ATP-exhausted ({len(atp_exhausted)}):")
    for e in atp_exhausted: print(f"  {e[0]}: {e[1][:60]}")

if __name__ == "__main__":
    main()
