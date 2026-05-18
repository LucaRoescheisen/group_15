import os, sys
os.environ['ISABELLE_INST_DIR'] = r'C:\Program Files\Isabelle2025-2'
os.environ['PYTHONUTF8'] = '1'

from prover.isabelle_api import start_isabelle_server, get_isabelle_client, build_theory, run_theory, session_start, finished_ok
from prover.config import ISABELLE_SESSION
import time

server_info, proc = start_isabelle_server(name='test')
isa = get_isabelle_client(server_info)
session = session_start(isa, session=ISABELLE_SESSION)
time.sleep(2)

# Simulate the full proof after filling f1's sorry with by simp
# The hole_span replaces the 'sorry' text with '\n  by simp\n'
full_proof_after_fill = '''lemma "length (xs @ ys) = length xs + length ys"
proof -
  have f1: "length (xs @ ys) = length xs + length ys"

  by simp
  show ?thesis
    sorry
qed
'''

thy = build_theory(full_proof_after_fill.splitlines(), add_print_state=False, end_with=None)
print("Full proof theory after fill:")
print(thy[:500])
print("...")

resps = run_theory(isa, session, thy)
ok, d = finished_ok(resps)
print(f"\nfull proof ok={ok}, nodes_ok={d.get('ok')}")
for node in d.get('nodes', []):
    print(f"  node status ok={node.get('status', {}).get('ok')}")
    for msg in node.get('messages', []):
        print(f"  msg kind={msg.get('kind')!r}: {msg.get('message','')[:200]}")

proc.kill()
