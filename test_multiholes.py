"""
Direct test for multi-hole filling.
Bypasses LLM outline generation and uses a known-correct induction outline.
"""
import os, sys
os.environ['ISABELLE_INST_DIR'] = r'C:\Program Files\Isabelle2025-2'
os.environ['PYTHONUTF8'] = '1'

from prover.isabelle_api import start_isabelle_server, get_isabelle_client, session_start
from prover.config import ISABELLE_SESSION
from planner.skeleton import find_sorry_spans
from planner.driver import _fill_one_hole, _verify_full_proof
from planner.goals import _verify_full_proof
import time

print("Starting Isabelle server...")
server_info, proc = start_isabelle_server(name='test_multi')
isa = get_isabelle_client(server_info)
session = session_start(isa, session=ISABELLE_SESSION)
time.sleep(2)
print("Isabelle ready.\n")

# ── Test 1: two-hole induction proof for map f (xs @ ys) = map f xs @ map f ys ──
outline1 = '''\
lemma "map f (xs @ ys) = map f xs @ map f ys"
proof (induction xs)
  case Nil
    sorry
  case (Cons x xs)
    sorry
qed'''

print("=" * 60)
print("TEST 1: map f (xs @ ys) = map f xs @ map f ys")
print("Outline:\n" + outline1)
print()

spans = find_sorry_spans(outline1)
print(f"Found {len(spans)} sorry hole(s): {spans}")

full = outline1
model = "qwen2.5-coder:7b"

for i, span in enumerate(find_sorry_spans(full)):
    print(f"\n--- Filling hole {i+1} at span {span} ---")
    s, e = span
    print(f"  Context: ...{repr(full[max(0,s-40):e+10])}...")
    new_text, ok, script = _fill_one_hole(
        isa, session, full, span,
        goal_text="map f (xs @ ys) = map f xs @ map f ys",
        model=model,
        per_hole_timeout=60,
        trace=True,
    )
    print(f"  ok={ok}, script={script!r}")
    if ok:
        full = new_text
        print(f"  ✅ Hole {i+1} filled!")
    else:
        print(f"  ❌ Hole {i+1} not filled.")

print("\nFinal proof text:")
print(full)
remaining = find_sorry_spans(full)
print(f"\nRemaining sorrys: {len(remaining)}")
if not remaining:
    verified = _verify_full_proof(isa, session, full)
    print(f"Final verification: {'✅ PASS' if verified else '❌ FAIL'}")

# ── Test 2: two-hole structured proof via have/show ──
print("\n" + "=" * 60)
outline2 = '''\
lemma "length (xs @ ys) = length xs + length ys"
proof -
  have f1: "length (xs @ ys) = length xs + length ys"
    sorry
  show ?thesis
    using f1 by simp
qed'''

print("TEST 2: length append (single have hole)")
print("Outline:\n" + outline2)
spans2 = find_sorry_spans(outline2)
print(f"Found {len(spans2)} sorry hole(s)")

full2 = outline2
for i, span in enumerate(find_sorry_spans(full2)):
    print(f"\n--- Filling hole {i+1} ---")
    new_text, ok, script = _fill_one_hole(
        isa, session, full2, span,
        goal_text="length (xs @ ys) = length xs + length ys",
        model=model,
        per_hole_timeout=60,
        trace=True,
    )
    print(f"  ok={ok}, script={script!r}")
    if ok:
        full2 = new_text
        print(f"  ✅ Hole {i+1} filled!")

print("\nFinal proof text:")
print(full2)
if not find_sorry_spans(full2):
    verified = _verify_full_proof(isa, session, full2)
    print(f"Final verification: {'✅ PASS' if verified else '❌ FAIL'}")

proc.kill()
print("\nDone.")
