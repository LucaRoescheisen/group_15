import os, sys
os.environ['ISABELLE_INST_DIR'] = r'C:\Program Files\Isabelle2025-2'
os.environ['PYTHONUTF8'] = '1'

from prover.isabelle_api import start_isabelle_server, get_isabelle_client, build_theory, run_theory, last_print_state_block
from planner.repair_inputs import _print_state_before_hole
import time

server_info, proc = start_isabelle_server(name='test')
isa = get_isabelle_client(server_info)
time.sleep(2)

full_text = 'lemma "length (xs @ ys) = length xs + length ys"\nproof -\n  have f1: "length (xs @ ys) ≤ length xs + length ys"\n    sorry\n  have f2: "length xs + length ys ≤ length (xs @ ys)"\n    sorry\n  show ?thesis\n    sorry\nqed\n'

print("full_text:")
print(full_text)

sorry_pos = full_text.find('sorry')
print('sorry at:', sorry_pos)

state = _print_state_before_hole(isa, '', full_text, (sorry_pos, sorry_pos+5), trace=True)
print('State block result:', repr(state[:300] if state else '(empty)'))

proc.kill()
