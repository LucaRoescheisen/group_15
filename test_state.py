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

# Test the exact goal that prove_goal would use
goal = r"length (xsa @ ysa) \<le> length xsa + length ysa"
steps = [f'lemma "{goal}"']
fin = "by simp"

thy = build_theory(steps + [fin], add_print_state=False, end_with=None)
print("Theory:")
print(thy)
print("---")

resps = run_theory(isa, session, thy)
ok, d = finished_ok(resps)
print(f"ok={ok}")
print(f"nodes ok: {d.get('ok')}")
if d.get('nodes'):
    for node in d['nodes']:
        print(f"  node ok={node.get('status', {}).get('ok')}")
        for msg in node.get('messages', []):
            print(f"  msg kind={msg.get('kind')!r}: {msg.get('message', '')[:200]}")

proc.kill()
