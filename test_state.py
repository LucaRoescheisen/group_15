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

# Test WITH outer parens — this is what _effective_goal_from_state returns
goal_with_parens = r"(length (xsa @ ysa) \<le> length xsa + length ysa)"
steps = [f'lemma "{goal_with_parens}"']
fin = "by simp"

thy = build_theory(steps + [fin], add_print_state=False, end_with=None)
print("Theory WITH outer parens:")
print(thy)
resps = run_theory(isa, session, thy)
ok, d = finished_ok(resps)
print(f"ok={ok}, nodes_ok={d.get('ok')}")
for node in d.get('nodes', []):
    for msg in node.get('messages', []):
        print(f"  msg kind={msg.get('kind')!r}: {msg.get('message','')[:200]}")

print()

# Also test the actual planner flow: run prove_goal directly
from prover.isabelle_api import session_start
from prover.prover import prove_goal

result = prove_goal(
    isa, session, goal_with_parens,
    model_name_or_ensemble="qwen2.5-coder:7b",
    beam_w=3, max_depth=2, hint_lemmas=6, timeout=30,
    use_sledge=True, sledge_timeout=10, sledge_every=1,
    trace=True, use_color=False,
)
print(f"\nprove_goal result: success={result.get('success')}, steps={result.get('steps')}")

proc.kill()
