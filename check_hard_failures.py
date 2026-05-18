"""
check_hard_failures.py — Nitpick/Quickcheck on all 20 hard-test failures.
Hard test: hol_main_hard_goals_test.txt  (80/100 passed, 20 failed)
"""
import os, sys, warnings
warnings.filterwarnings("ignore", category=ResourceWarning)

os.environ.setdefault("ISABELLE_INST_DIR", r"C:\Program Files\Isabelle2025-2")
os.environ.setdefault("PYTHONUTF8", "1")
sys.path.insert(0, os.path.dirname(__file__))

from prover.isabelle_api import start_isabelle_server, get_isabelle_client, session_start, run_theory, finished_ok

# (label, goal_string, elapsed_s, notes)
GOALS = [
    ("hard #4",
     "(\\<exists>x. P x \\<and> (Q x \\<longrightarrow> R x)) \\<longrightarrow> ((\\<exists>x. P x \\<and> Q x) \\<longrightarrow> (\\<exists>x. R x))",
     6.3,
     "fast-fail — SUSPECT non-theorem: x satisfying P∧(Q→R) may differ from x satisfying P∧Q"),

    ("hard #9",
     "finite A \\<Longrightarrow> card (A \\<union> B) = card A + card (B - A)",
     32.3,
     "ATP-exhausted — requires finite B or B⊆A condition"),

    ("hard #12",
     "finite A \\<Longrightarrow> card {x\\<in>A. P x \\<and> Q x} + card {x\\<in>A. P x \\<and> \\<not> Q x} = card {x\\<in>A. P x}",
     30.5,
     "ATP-exhausted — partition of {x∈A. P x}"),

    ("hard #13",
     "finite A \\<Longrightarrow> card {x\\<in>A. P x} + card {x\\<in>A. \\<not> P x} = card A",
     31.2,
     "ATP-exhausted — full partition of A"),

    ("hard #17",
     "finite A \\<Longrightarrow> sum (\\<lambda>x. (if x\\<in>B then (1::nat) else 0)) A = card (A \\<inter> B)",
     66.9,
     "ATP-exhausted — characteristic function sum"),

    ("hard #18",
     "finite A \\<Longrightarrow> sum (\\<lambda>x. (if P x then (1::nat) else 0)) A = card {x\\<in>A. P x}",
     29.3,
     "ATP-exhausted — predicate indicator sum"),

    ("hard #25",
     "finite A \\<Longrightarrow> finite B \\<Longrightarrow> card { (x,y)\\<in>A\\<times>B. P x \\<and> Q y } \\<le> card A * card B",
     29.2,
     "ATP-exhausted — subset of product"),

    ("hard #26",
     "finite A \\<Longrightarrow> finite B \\<Longrightarrow> card ((A - C) \\<times> (B - D)) = (card A - card (A \\<inter> C)) * (card B - card (B \\<inter> D))",
     27.0,
     "ATP-exhausted — nat subtraction on card"),

    ("hard #29",
     "finite A \\<Longrightarrow> card (A \\<union> B) + card (A \\<inter> B) = card A + card B",
     25.6,
     "ATP-exhausted — inclusion-exclusion (requires finite B)"),

    ("hard #30",
     "finite A \\<Longrightarrow> card (SIGMA x\\<in>A. F x) = sum (\\<lambda>x. card (F x)) A",
     2.1,
     "fast-fail — SIGMA notation, possibly parse/type error without extra imports"),

    ("hard #31",
     "finite I \\<Longrightarrow> (\\<forall>i\\<in>I. finite (F i)) \\<Longrightarrow> (\\<forall>i\\<in>I. \\<forall>j\\<in>I. i\\<noteq>j \\<longrightarrow> F i \\<inter> F j = {}) \\<Longrightarrow> card (\\<Union>i\\<in>I. F i) = (\\<Sum>i\\<in>I. card (F i))",
     29.4,
     "ATP-exhausted — disjoint union cardinality"),

    ("hard #33",
     "finite A \\<Longrightarrow> inj_on f A \\<Longrightarrow> card (SIGMA x\\<in>A. {f x}) = card A",
     2.1,
     "fast-fail — SIGMA notation, possibly parse/type error"),

    ("hard #41",
     "f ` (A - B) \\<subseteq> (f ` A) - (f ` (A \\<inter> B))",
     27.8,
     "SUSPECT non-theorem — false for non-injective f: f(a)=f(b) with a∈A-B, b∈A∩B"),

    ("hard #73",
     "rev (take n xs) = drop (length xs - n) (rev xs) \\<longleftrightarrow> n \\<le> length xs",
     28.9,
     "SUSPECT non-theorem — iff too strong: equality holds for n>length xs too (nat subtraction gives 0)"),

    ("hard #74",
     "rev (drop n xs) = take (length xs - n) (rev xs) \\<longleftrightarrow> n \\<le> length xs",
     26.7,
     "SUSPECT non-theorem — same issue as #73"),

    ("hard #75",
     "nth (map f xs) i = f (nth xs i) \\<longleftrightarrow> i < length xs",
     27.6,
     "SUSPECT non-theorem — equality may hold for i≥length xs too (both return arbitrary values)"),

    ("hard #76",
     "nth (xs @ ys) i = nth xs i \\<longleftrightarrow> i < length xs",
     27.1,
     "SUSPECT non-theorem — iff direction questionable for out-of-bounds"),

    ("hard #83",
     "xs \\<noteq> [] \\<Longrightarrow> nth (rev xs) i = nth xs (length xs - Suc i) \\<longleftrightarrow> i < length xs",
     28.1,
     "SUSPECT non-theorem — iff too strong for out-of-bounds"),

    ("hard #90",
     "foldr f b (xs @ ys) = foldr f (foldr f b ys) xs",
     26.2,
     "ATP-exhausted — possibly type error: b in list position of foldr"),

    ("hard #94",
     "length [m..<n] + length [n..<p] = length [m..<p]",
     25.7,
     "SUSPECT non-theorem — false when m>n or n>p (nat subtraction: length [a..<b] = b-a)"),
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
                msg = str(getattr(m, "message", None) or (m.get("message", "") if isinstance(m, dict) else ""))
                if any(k in msg.lower() for k in ("nitpick", "quickcheck", "counterexample", "no counterexample", "ran out", "error")):
                    msgs.append(msg.strip().splitlines()[0])
    return " | ".join(msgs) if msgs else "(no output)"


def main():
    print("Starting Isabelle server...")
    server_info, proc = start_isabelle_server(name="hard_checker", log_file="hard_checker.log")
    isabelle = get_isabelle_client(server_info)
    sid = session_start(isabelle, session="HOL")
    print(f"Session: {sid}\n{'=' * 70}\n")

    try:
        fast_fail, atp_exhausted, non_theorem, theorem, inconclusive = [], [], [], [], []

        for label, goal, t, notes in GOALS:
            print(f"[{label}] {notes}")
            thy = theory_for(goal)
            resps = run_theory(isabelle, sid, thy, timeout_s=45)
            verdict = extract_verdict(resps)
            print(f"  verdict: {verdict}\n")

            entry = (label, goal, t, notes, verdict)
            v = verdict.lower()
            if "counterexample" in v and "no counterexample" not in v:
                non_theorem.append(entry)
            elif "no counterexample" in v:
                theorem.append(entry)
            elif t < 5.0:
                fast_fail.append(entry)
            elif "ran out" in v:
                inconclusive.append(entry)
            else:
                atp_exhausted.append(entry)

    finally:
        try:
            isabelle.shutdown()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            pass

    print("\n" + "=" * 70)
    print("=== HARD TEST FAILURE ANALYSIS ===")
    print(f"\nNon-theorems ({len(non_theorem)}):")
    for e in non_theorem:
        print(f"  {e[0]}: {e[1][:70]}")
    print(f"\nTheorems / no counterexample found ({len(theorem)}):")
    for e in theorem:
        print(f"  {e[0]}: {e[1][:70]}")
    print(f"\nFast-fail / parse error ({len(fast_fail)}):")
    for e in fast_fail:
        print(f"  {e[0]}: {e[1][:70]}")
    print(f"\nInconclusive / ran out ({len(inconclusive)}):")
    for e in inconclusive:
        print(f"  {e[0]}: {e[1][:70]}")
    print(f"\nATP-exhausted (likely true theorems) ({len(atp_exhausted)}):")
    for e in atp_exhausted:
        print(f"  {e[0]}: {e[1][:70]}")
    print(f"\nTotal: {len(GOALS)} | Breakdown: non-theorems={len(non_theorem)}, ATP-exhausted={len(atp_exhausted)}, fast-fail={len(fast_fail)}, no-counterexample={len(theorem)}, inconclusive={len(inconclusive)}")


if __name__ == "__main__":
    main()
