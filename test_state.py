import os, sys, json
os.environ['ISABELLE_INST_DIR'] = r'C:\Program Files\Isabelle2025-2'
os.environ['PYTHONUTF8'] = '1'

from prover.isabelle_api import start_isabelle_server, get_isabelle_client, build_theory, run_theory, session_start, finished_ok, _decode_body_to_dict, _get_field
from prover.config import ISABELLE_SESSION
import time

server_info, proc = start_isabelle_server(name='test')
isa = get_isabelle_client(server_info)
session = session_start(isa, session=ISABELLE_SESSION)
time.sleep(2)

# Simple proof that should succeed
thy = build_theory(['lemma "length (xs @ ys) = length xs + length ys"', 'by simp'], add_print_state=False, end_with=None)
resps = run_theory(isa, session, thy)
print(f"Got {len(resps)} responses")
ok, d = finished_ok(resps)
print(f"finished_ok: ok={ok}, d={d}")

# Examine the FINISHED response body
for r in resps:
    rtype = str(getattr(r, 'response_type', ''))
    if 'FINISHED' in rtype.upper():
        body = _get_field(r, ("response_body", "body", "message", "payload"))
        print(f"Body type: {type(body).__name__}")
        print(f"Body ok attr: {getattr(body, 'ok', 'N/A')}")

        # Test json.loads on body
        try:
            result = json.loads(body)
            print(f"json.loads succeeded: {type(result)}")
        except Exception as e:
            print(f"json.loads failed: {e}")

        # Test _decode_body_to_dict
        decoded = _decode_body_to_dict(body)
        print(f"_decode_body_to_dict: {type(decoded)} = {decoded}")

proc.kill()
