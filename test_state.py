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

# Dump all NOTE responses fully
for i, r in enumerate(resps):
    rtype = str(getattr(r, 'response_type', ''))
    body = getattr(r, 'response_body', None)
    print(f"\nresp[{i}] type={rtype!r}")
    if isinstance(body, bytes):
        body = body.decode('utf-8', errors='replace')
    if isinstance(body, str) and body.strip().startswith('{'):
        try:
            d = json.loads(body)
            print(f"  parsed JSON keys: {list(d.keys())}")
            if 'nodes' in d:
                for n in d['nodes']:
                    for m in n.get('messages', []):
                        print(f"  MSG kind={m.get('kind')!r}: {str(m.get('message',''))[:200]}")
        except:
            print(f"  raw body: {body[:300]}")
    elif body:
        # Pydantic model
        print(f"  body type: {type(body).__name__}")
        try:
            d = body.model_dump() if hasattr(body, 'model_dump') else vars(body)
            for k, v in d.items():
                sv = str(v)[:150]
                print(f"    {k}: {sv}")
        except:
            print(f"  body repr: {repr(body)[:300]}")

print("\n\nlast_print_state_block:", repr(last_print_state_block(resps) or '(empty)'))

proc.kill()
