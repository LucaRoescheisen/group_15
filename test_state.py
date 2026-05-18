import os, sys
os.environ['ISABELLE_INST_DIR'] = r'C:\Program Files\Isabelle2025-2'
os.environ['PYTHONUTF8'] = '1'

from prover.isabelle_api import start_isabelle_server, get_isabelle_client, build_theory, run_theory, session_start
from prover.config import ISABELLE_SESSION
from planner.goals import _print_state_before_hole, _build_ml_prolog, _inject_var_extraction, _extract_print_state_from_responses
import time

server_info, proc = start_isabelle_server(name='test')
isa = get_isabelle_client(server_info)
session = session_start(isa, session=ISABELLE_SESSION)
print(f"Session: {session!r}")
time.sleep(2)

full_text = 'lemma "length (xs @ ys) = length xs + length ys"\nproof -\n  have f1: "length (xs @ ys) ≤ length xs + length ys"\n    sorry\n  have f2: "length xs + length ys ≤ length (xs @ ys)"\n    sorry\n  show ?thesis\n    sorry\nqed\n'

sorry_pos = full_text.find('sorry')
s = sorry_pos
lines = full_text[:s].rstrip().splitlines()
lemma_start = next((i for i, ln in enumerate(lines) if ln.strip().startswith("lemma ")), -1)
proof_lines = lines[lemma_start:]

injected = _inject_var_extraction(proof_lines)
thy = build_theory(_build_ml_prolog() + injected, add_print_state=True, end_with="oops")
print("FULL THEORY:")
print(thy)
print("--- END THEORY ---")

resps = run_theory(isa, session, thy)
print(f"\nGot {len(resps)} responses")
for i, r in enumerate(resps[:10]):
    print(f"  resp[{i}]: type={getattr(r, 'response_type', '?')!r}")
    body = getattr(r, 'response_body', None)
    if body:
        print(f"    body[50]: {str(body)[:200]}")

state = _extract_print_state_from_responses(resps)
print(f"\nExtracted state (len={len(state)}): {state[:300]!r}")

proc.kill()
