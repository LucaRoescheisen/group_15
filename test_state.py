import os, sys, json
os.environ['ISABELLE_INST_DIR'] = r'C:\Program Files\Isabelle2025-2'
os.environ['PYTHONUTF8'] = '1'

from prover.isabelle_api import start_isabelle_server, get_isabelle_client, build_theory, run_theory, session_start
from prover.config import ISABELLE_SESSION
from planner.goals import _build_ml_prolog, _inject_var_extraction, _extract_print_state_from_responses, _print_state_before_hole
import time

server_info, proc = start_isabelle_server(name='test')
isa = get_isabelle_client(server_info)
session = session_start(isa, session=ISABELLE_SESSION)
print(f"Session: {session!r}")
time.sleep(2)

full_text = 'lemma "length (xs @ ys) = length xs + length ys"\nproof -\n  have f1: "length (xs @ ys) ≤ length xs + length ys"\n    sorry\n  have f2: "length xs + length ys ≤ length (xs @ ys)"\n    sorry\n  show ?thesis\n    sorry\nqed\n'

sorry_pos = full_text.find('sorry')

# Test _print_state_before_hole directly
state = _print_state_before_hole(isa, session, full_text, (sorry_pos, sorry_pos+5), trace=True)
print(f"\nState block length: {len(state)}")
print(f"State block:\n{state[:500]}")

proc.kill()
