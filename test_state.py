import os, sys
os.environ['ISABELLE_INST_DIR'] = r'C:\Program Files\Isabelle2025-2'
os.environ['PYTHONUTF8'] = '1'

from prover.isabelle_api import start_isabelle_server, get_isabelle_client, build_theory, run_theory, session_start
from prover.config import ISABELLE_SESSION
from planner.goals import _print_state_before_hole, _build_ml_prolog, _inject_var_extraction
import time

server_info, proc = start_isabelle_server(name='test')
isa = get_isabelle_client(server_info)
session = session_start(isa, session=ISABELLE_SESSION)
print(f"Session: {session!r}")
time.sleep(2)

full_text = 'lemma "length (xs @ ys) = length xs + length ys"\nproof -\n  have f1: "length (xs @ ys) ≤ length xs + length ys"\n    sorry\n  have f2: "length xs + length ys ≤ length (xs @ ys)"\n    sorry\n  show ?thesis\n    sorry\nqed\n'

print("full_text:")
print(full_text)

sorry_pos = full_text.find('sorry')
print('sorry at:', sorry_pos)

# Test what theory is sent to Isabelle
s = sorry_pos
lines = full_text[:s].rstrip().splitlines()
lemma_start = next((i for i, ln in enumerate(lines) if ln.strip().startswith("lemma ")), -1)
proof_lines = lines[lemma_start:]
print("proof_lines:", proof_lines)

injected = _inject_var_extraction(proof_lines)
thy = build_theory(_build_ml_prolog() + injected, add_print_state=True, end_with="oops")
print("\nTheory sent to Isabelle:")
for i, line in enumerate(thy.splitlines()[:30]):
    print(f"  {i:3d}: {line}")
print("...")

state = _print_state_before_hole(isa, session, full_text, (sorry_pos, sorry_pos+5), trace=True)
print('\nState block result length:', len(state))
print('State block result:', repr(state[:500] if state else '(empty)'))

proc.kill()
