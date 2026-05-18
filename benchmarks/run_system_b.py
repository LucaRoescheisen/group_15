"""
benchmarks/run_system_b.py — System B benchmark runner
=======================================================
Runs the LLM stepwise prover (prover.prover.prove_goal) on a dataset file
without modifying any original repo code.

Usage (Windows PowerShell):
    $env:ISABELLE_INST_DIR = "C:\\Program Files\\Isabelle2025-2"
    $env:PYTHONUTF8      = "1"
    $env:OLLAMA_MODEL    = "qwen2.5-coder:7b"

    & .venv\\Scripts\\python.exe benchmarks/run_system_b.py `
        --file datasets/hol_main_easy_goals_test.txt `
        --timeout 120 `
        --sledge --sledge-timeout 20

Usage (Linux/macOS):
    OLLAMA_MODEL=qwen2.5-coder:7b \\
    python benchmarks/run_system_b.py \\
        --file datasets/hol_main_easy_goals_test.txt \\
        --timeout 120 \\
        --sledge --sledge-timeout 20

    Note: ensure `isabelle` is on your PATH (e.g. export PATH=$PATH:/path/to/Isabelle2025-2/bin)

Results are printed to stdout and saved to benchmarks/results/.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
import warnings
warnings.filterwarnings("ignore", category=ResourceWarning)

# ── make repo root importable ──────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# On Windows, isabelle_client needs ISABELLE_INST_DIR to locate the Cygwin
# Isabelle launcher.  On Linux/macOS it uses `isabelle` from PATH directly,
# so we only set the default here when actually running on Windows.
if sys.platform == "win32":
    os.environ.setdefault("ISABELLE_INST_DIR", r"C:\Program Files\Isabelle2025-2")
os.environ.setdefault("PYTHONUTF8", "1")

# ── repo imports (read-only — no files modified) ───────────────────────────
from prover.isabelle_api import (
    start_isabelle_server, get_isabelle_client, session_start,
)
from prover.prover import prove_goal
from prover import config as CFG


# ── helpers ────────────────────────────────────────────────────────────────
_LEMMA_RE = re.compile(r'lemma\s+"(.+)"', re.IGNORECASE)

def read_goals(path: str) -> list[str]:
    goals: list[str] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("lemma "):
                m = _LEMMA_RE.search(line)
                goals.append(m.group(1) if m else line[len("lemma "):].strip().strip('"'))
            else:
                goals.append(line.strip('"'))
    return goals


def _prove_one(isabelle, session_id, goal, args) -> dict:
    return prove_goal(
        isabelle, session_id, goal,
        model_name_or_ensemble=CFG.MODEL,
        beam_w=args.beam,
        max_depth=args.max_depth,
        hint_lemmas=args.hint_lemmas,
        timeout=args.timeout,
        models=None,
        save_dir=None,
        use_sledge=args.sledge,
        sledge_timeout=args.sledge_timeout,
        sledge_every=args.sledge_every,
        trace=args.trace,
        use_color=False,
        use_qc=False,
        qc_timeout=2,
        qc_every=1,
        use_np=False,
        np_timeout=5,
        np_every=2,
        facts_limit=CFG.FACTS_LIMIT,
        do_minimize=False,
        minimize_timeout=8,
        do_variants=False,
        variant_timeout=6,
        variant_tries=24,
        enable_reranker=False,
    )


# ── main ───────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="System B benchmark — LLM stepwise prover",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--file", required=True, help="Goal dataset file")
    ap.add_argument("--model", default=None, help="Ollama model (overrides OLLAMA_MODEL env)")
    ap.add_argument("--timeout", type=int, default=120, help="Wall-clock seconds per goal")
    ap.add_argument("--beam", type=int, default=4, help="Beam width for tactic search")
    ap.add_argument("--max-depth", type=int, default=8, help="Max proof depth")
    ap.add_argument("--hint-lemmas", type=int, default=6, help="Hint lemmas per step")
    ap.add_argument("--sledge", action="store_true", help="Enable Sledgehammer fallback")
    ap.add_argument("--sledge-timeout", type=int, default=20, help="Sledgehammer timeout (s)")
    ap.add_argument("--sledge-every", type=int, default=2, help="Call Sledgehammer every N steps")
    ap.add_argument("--trace", action="store_true", help="Verbose tactic trace")
    ap.add_argument("--limit", type=int, default=None, help="Only run first N goals")
    args = ap.parse_args()

    if args.model:
        CFG.MODEL = args.model

    dataset_name = os.path.basename(args.file)
    goals = read_goals(args.file)
    if args.limit:
        goals = goals[:args.limit]
    n = len(goals)

    print(f"=== {dataset_name} ({n} goals) ===")
    print(f"=== System B — LLM stepwise prover ===")
    print(f"Model: {CFG.MODEL} | beam={args.beam} depth={args.max_depth} timeout={args.timeout}s sledge={args.sledge}")
    print()

    # ── start Isabelle ─────────────────────────────────────────────────────
    server_info, proc = start_isabelle_server(name="sys_b", log_file="benchmarks/sys_b_server.log")
    isabelle = get_isabelle_client(server_info)
    sid = session_start(isabelle, session="HOL")
    print(f"Isabelle session: {sid}\n")

    results = []
    passed = 0
    times = []

    try:
        for i, goal in enumerate(goals, 1):
            print(f"[{i}/{n}] {goal[:90]}")
            t0 = time.perf_counter()
            try:
                res = _prove_one(isabelle, sid, goal, args)
            except Exception as e:
                res = {"success": False, "timeout": False, "depth": -1, "steps": [], "error": str(e)}
            elapsed = time.perf_counter() - t0

            success  = bool(res.get("success", False))
            timed_out = bool(res.get("timeout", False))
            steps    = res.get("steps") or []
            tactic   = " | ".join(str(s) for s in steps if str(s).strip()) if steps else ""

            if success:
                passed += 1
                times.append(elapsed)
                status = "OK"
            elif timed_out:
                status = "TIMEOUT"
            else:
                status = "FAIL"

            print(f"  -> {status:<8} ({elapsed:.1f}s)  {tactic[:80]}")

            results.append({
                "index": i,
                "goal": goal,
                "success": success,
                "timeout": timed_out,
                "elapsed": round(elapsed, 2),
                "depth": res.get("depth", -1),
                "steps": [str(s) for s in steps],
            })

    finally:
        try: isabelle.shutdown()
        except Exception: pass
        try: proc.terminate(); proc.wait(timeout=3)
        except Exception: pass

    # ── summary ────────────────────────────────────────────────────────────
    timeouts = sum(1 for r in results if r["timeout"])
    failures = sum(1 for r in results if not r["success"] and not r["timeout"])
    median_t = round(statistics.median(times), 1) if times else 0.0
    avg_t    = round(statistics.mean(times),   1) if times else 0.0

    print(f"\n=== Summary ===")
    print(f"Success:  {passed}/{n} ({passed * 100.0 / n:.1f}%)")
    print(f"Timeouts: {timeouts}/{n}")
    print(f"Failures: {failures}/{n}")
    print(f"Median time (successes): {median_t}s | Average: {avg_t}s")

    # ── save results ───────────────────────────────────────────────────────
    os.makedirs(os.path.join(REPO_ROOT, "benchmarks", "results"), exist_ok=True)
    out_stem = dataset_name.replace(".txt", "")
    out_path = os.path.join(REPO_ROOT, "benchmarks", "results", f"system_b_{out_stem}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "system": "B",
            "dataset": dataset_name,
            "model": CFG.MODEL,
            "params": vars(args),
            "summary": {
                "n": n, "passed": passed, "timeouts": timeouts, "failures": failures,
                "rate": round(passed * 100.0 / n, 1),
                "median_s": median_t, "avg_s": avg_t,
            },
            "goals": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to: {out_path}")


if __name__ == "__main__":
    main()
