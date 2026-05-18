import os, sys, json
os.environ['ISABELLE_INST_DIR'] = r'C:\Program Files\Isabelle2025-2'
os.environ['PYTHONUTF8'] = '1'

from prover.isabelle_api import start_isabelle_server, get_isabelle_client, build_theory, run_theory, session_start, last_print_state_block
from prover.config import ISABELLE_SESSION
from planner.goals import _build_ml_prolog, _inject_var_extraction
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

resps = run_theory(isa, session, thy)
print(f"Got {len(resps)} responses")

# Dump the FINISHED response's nodes/messages fully
for i, r in enumerate(resps):
    rtype = str(getattr(r, 'response_type', ''))
    body = getattr(r, 'response_body', None)
    if 'FINISHED' in rtype.upper():
        print(f"\nFINISHED response[{i}]:")
        if hasattr(body, 'model_dump'):
            d = body.model_dump()
        elif hasattr(body, '__dict__'):
            d = vars(body)
        else:
            d = {}

        nodes = d.get('nodes', [])
        print(f"  nodes count: {len(nodes)}")
        for ni, node in enumerate(nodes):
            print(f"  node[{ni}]: {node.get('node_name', '')}")
            messages = node.get('messages', [])
            print(f"    messages count: {len(messages)}")
            for mi, msg in enumerate(messages[:20]):
                kind = msg.get('kind', '?')
                text = str(msg.get('message', ''))
                print(f"    msg[{mi}] kind={kind!r}: {text[:300]}")
    elif 'NOTE' in rtype.upper():
        # Check note body more carefully
        print(f"\nNOTE response[{i}]:")
        if body:
            try:
                if hasattr(body, 'model_dump'):
                    bd = body.model_dump()
                elif hasattr(body, '__dict__'):
                    bd = vars(body)
                else:
                    bd = {}
                for k,v in bd.items():
                    print(f"  {k}: {str(v)[:200]}")
            except Exception as e:
                print(f"  error: {e}")

proc.kill()
