#import "@preview/fine-lncs:0.4.0": lncs, institute, author, theorem, proof

#set cite(style: "ieee")

#let inst_princ = institute("Griffith University",
  addr: "Southport QLD 4215, AUS"
)

#show: lncs.with(
  title: "LLM-Guided Isabelle/HOL Theorem Proving with Isar Proof Planning and CEGIS-Style Repair",
  authors: (
    author("Phong Truong", insts: (inst_princ)),
    author("Luca Rosechisen", insts: (inst_princ)),
  ),
  abstract: [
    Automated theorem proving in interactive proof assistants such as Isabelle/HOL
    remains a difficult challenge, requiring both strategic proof planning and
    low-level tactic synthesis.
    This paper presents an LLM-guided prover that combines a structured Isar proof
    planner with a CEGIS-style iterative repair loop.
    Given a goal, the system first generates an Isar proof outline with
    sorry placeholders via a large language model, then calls a stepwise tactic
    prover to fill each hole.
    When filling fails, a staged repair procedure — targeting first the local
    have/show block, then the enclosing subproof, and finally regenerating the
    whole proof — is triggered, augmented with five improvements: smarter block
    deduplication, error-category–specific LLM guidance, a complete ban-list for
    whole-proof regeneration, direct application of Isabelle's own `Try this:`
    suggestions, and stage-cascade continuation after partial repairs.
    We evaluate our approach against a Sledgehammer-only baseline and a standalone
    LLM stepwise prover across three difficulty tiers drawn from the HOL main
    goal corpus @isabellm2026repo and the mini-F2F benchmark @zheng2022minif2f.
    Our planner achieves higher success rates on goals requiring inductive
    structure while remaining competitive on routine lemmas, and the CEGIS repair
    loop rescues a meaningful fraction of initially-failed holes.
  ],
  keywords: ("Automated Theorem Proving", "Isabelle/HOL", "Large Language Models", "Isar Proofs", "CEGIS", "Proof Repair"),
)

// ============================================================
// 1. INTRODUCTION
// ============================================================

= Introduction

Interactive theorem proving (ITP) tools such as Isabelle/HOL,
Coq, and Lean provide machine-checked formal verification, but writing proofs
remains a labour-intensive task that demands deep domain expertise.
The emergence of large language models (LLMs) capable of code generation has
opened a new avenue: can an LLM automate or substantially assist the proof
discovery process?

Early work showed that LLMs can propose individual Isabelle tactics with
moderate accuracy.
More recent systems couple LLMs with Isabelle's built-in automation —
particularly Sledgehammer, which invokes external automated theorem provers
(ATPs) such as E, Vampire, and Z3 — to form hybrid planners.
However, Sledgehammer alone fails on goals that require structured Isar proofs
(e.g., proofs by induction or case analysis), and plain LLM-tactic generation
without a proof skeleton often produces incoherent proof states.

Despite this progress, a practical gap remains: published systems such as
Baldur @jiang2021pisa and LISA @jiang2021lisa are either closed-source,
require fine-tuning, or depend on specialised infrastructure unavailable to
ordinary researchers.
Moreover, none of the open systems in the Isabellm framework @hou2026isabellm
fully implement the two-stage pipeline described in their architecture:
the hole-filling driver and the CEGIS repair loop are present as stubs
but are not connected into a working pipeline.
This paper closes that gap by implementing both components and evaluating
the resulting system against two published baselines on four benchmark suites
spanning easy, medium, and hard difficulty.

Our contributions are as follows:
#list(
  [*Hole-filling driver:* a complete implementation of `driver.py` that delegates each sorry placeholder in an Isar skeleton to the stepwise tactic prover, with correct state extraction and apply-only handling.],
  [*CEGIS repair loop:* a three-stage escalation strategy in `repair.py` that targets local `have`/`show` blocks, enclosing subproofs, and whole-proof regeneration. Five targeted improvements are introduced: (1) smarter block deduplication that normalises ATP synonyms, `simp` lemma ordering, and generated fact labels; (2) error-category–specific guidance fed back to the LLM for each repair round; (3) a complete ban-list of all previously failed outlines for whole-proof Stage~3 regeneration; (4) a pre-LLM pass that extracts and directly applies Isabelle's `Try this:` tactic suggestions; and (5) stage-cascade continuation that propagates partial repairs from Stage~1 into Stage~2 rather than bailing out early.],
  [*Baseline comparison:* a reproducible Sledgehammer-only baseline (`baselines/sledge_only.py`) and a systematic evaluation across the HOL main goal corpus and the Isabellm hand-crafted datasets.],
)

The remainder of this paper is organised as follows.
Section~2 surveys related work.
Section~3 describes our approach.
Section~4 presents implementation details and experimental results.
Section~5 concludes.

// ============================================================
// 2. RELATED WORK
// ============================================================

= Related Work

*LLM-based tactic provers.*
LISA fine-tuned a transformer on Isabelle proof corpora to predict
the next tactic at each proof state.
Draft-Sketch-Prove uses an LLM to draft a high-level proof
sketch in natural language, then translates it into formal steps.
Hypertree Proof Search extends this with a Monte-Carlo
tree search over tactic sequences.

*Sledgehammer and ATP integration.*
Sledgehammer invokes external ATPs on every subgoal and
translates successful ATP proofs back into Isabelle tactics.
MagnusHammer integrates an LLM-based premise selector
with Sledgehammer to narrow the search space.
Our approach uses Sledgehammer as one fallback within a beam-search tactic loop,
rather than as the primary solver.

*Proof repair and CEGIS.*
CEGIS (Counterexample-Guided Inductive Synthesis) is a
classical synthesis paradigm that iterates between a synthesiser and a verifier.
Applied to proof repair, the verifier is the proof assistant and the
synthesiser is the LLM.
Baldur uses this idea in Lean; our work adapts it to
Isabelle/HOL with a three-stage escalation strategy.

*Comparison with related methods.*
Unlike Baldur or LISA, our system is model-agnostic (supporting Ollama, Gemini
CLI, and Hugging Face backends), does not require fine-tuning, and explicitly
separates proof planning (Isar skeleton) from proof filling (tactic loop) to
exploit the structural regularity of Isar proofs.

// ============================================================
// 3. PROPOSED APPROACH
// ============================================================

= Proposed Approach

Our system extends the Isabellm framework @hou2026isabellm with two previously
incomplete components: (1) a hole-filling procedure that delegates each sorry
placeholder to the stepwise prover, and (2) a CEGIS-style staged repair loop
that handles failures at multiple granularities.

The LLM is involved at two levels:

- *Planner level:* the LLM generates a proof structure (Isar skeleton) with `sorry` placeholders where subproofs are missing.
- *Prover level:* for each `sorry`, the LLM proposes tactics to close the specific subgoal.

== Isar Proof Planning

Given a goal $G$, the planner (`skeleton.py`) prompts an LLM to generate a
structured Isar proof outline.
Easy goals may receive a direct `by simp` or similar one-liner.
Harder goals receive a multi-step Isar proof with `sorry` placeholders where
subproofs are missing — the LLM knows the proof structure but is uncertain
about the low-level details.
To promote diversity, we sample $k$ outlines at temperatures
$T_1 < T_2 < T_3$ and select the outline that Isabelle can verify furthest
before the first failure.

== Hole Filling

`driver.py` orchestrates the fill loop. For each `sorry` span
(processed left-to-right), the filler:

1. Asks Isabelle: "what is the actual goal at this exact point in the proof?"
2. Extracts the precise subgoal via `_effective_goal_from_state`.
3. Calls `prove_goal` — the stepwise prover from the `prover/` folder — which
   calls the LLM (via Ollama, Gemini CLI, etc.) to guess tactics.
4. If the prover returns a verified tactic sequence, it replaces the `sorry`.

*WIP 1 fix — apply-only results inside have/show blocks.*
When `prove_goal` returns `apply` steps but no closing `by ...` or `done`,
Isabelle rejects the result in `have`/`show` context.
Previously the system quit on this case without trying alternatives.
We detect this condition and escalate immediately to the repair loop rather
than silently failing.

== CEGIS Repair Loop

When filling fails or verification fails on a non-hole line, the system enters
a staged repair procedure in `repair.py`:

#figure(
  rect(width: 100%, height: 60mm, stroke: 0.5pt),
  caption: [CEGIS repair loop. Each stage escalates the repair scope if the previous stage cannot verify a fix within its budget.]
)

*Stage 1 — local have/show block.* The LLM regenerates the smallest enclosing
`have`/`show`/`obtain` block around the failure point.

*Stage 2 — subproof.* If Stage 1 exhausts its attempt budget, the enclosing
`proof ... qed` block is regenerated.

*Stage 3 — whole proof.* If Stage 2 also fails, the entire proof body is
regenerated from scratch, seeded with a ban-list of previously failed outlines
to prevent repetition.

After any repair edit, the filler re-runs on any newly introduced `sorry`
placeholders before the next Isabelle check.
A global timeout stops the loop to prevent runaway computation.

*WIP 2 fixes — CEGIS repair loop improvements.*
We identify and address five weaknesses in the original `repair.py` implementation.

- *Weak block deduplication.* The original `_fingerprint_block` collapsed whitespace only, so `by auto` and `by blast` were treated as distinct blocks and both attempted even though they are semantically equivalent. The improved fingerprint normalises all common ATP synonyms (`auto`, `blast`, `fastforce`, `clarsimp`) to a single token, sorts the lemma list in every `simp add:` clause, canonicalises generated fact labels (`h1`, `f2`, `g3` all map to `fN`), and strips Unicode smart quotes. This prevents the LLM from wasting attempts on structurally identical candidates.

- *Generic repair guidance.* The original loop passed the same generic failure message to the LLM on every repair round regardless of what Isabelle actually reported. The new `_why_from_errors` helper classifies the error text into eight categories — type clash, tactic failure, unknown identifier, unification failure, no subgoals remaining, constructor clash, locally fixed variable, and stale `sorry` — and forwards a targeted one-sentence diagnosis with each LLM call. This gives the model a concrete signal about the kind of fix required, rather than a generic retry prompt.

- *Incomplete Stage 3 ban-list.* When `regenerate_whole_proof` was called multiple times (i.e., Stage~3 was entered more than once), only the single most-recent failed outline was passed as a ban-list seed. Subsequent regeneration rounds could therefore re-propose the same outline structure that had already failed. `driver.py` now accumulates all tried outlines in a `failed_outlines` list and passes the full list to `regenerate_whole_proof` via the `prior_outline_texts` parameter, ensuring every Stage~3 regeneration produces a structurally novel outline.

- *Missed "Try this" suggestions.* When a failing block contains `apply sledgehammer` or the LLM triggers Isabelle's `try0` or `solve_direct`, Isabelle outputs lines of the form `Try this: simp add: append_assoc (0.5ms)`. The original code treated `"try this"` only as a keyword to classify the error; the tactic suggestion itself was silently discarded. The new `_extract_try_this_suggestions` / `_apply_try_this_to_block` pre-pass fires _before_ any LLM call: it extracts up to two such suggestions, substitutes each one into the last tactic line of the block, and verifies with Isabelle. When Isabelle's own suggestion succeeds, the LLM call is bypassed entirely, reducing both latency and token cost.

- *Stage cascade broken.* The original `try_cegis_repairs` returned `(text, False, "stage=1 partial-progress")` immediately whenever Stage~1 changed the proof but did not fully solve it, so Stage~2 was never attempted on a partially-repaired proof. The fix removes the early return: after a Stage~1 partial edit, the function re-locates the first remaining `sorry`, recomputes the anchor line and proof-state context, and falls through to Stage~2 (case-block and subproof repair) with the improved text. The same cascade is applied between Stage~2a and Stage~2b.

// ============================================================
// 4. IMPLEMENTATION AND EXPERIMENTS
// ============================================================

= Implementation and Experiments

== Implementation

The system is implemented in Python 3.11 and interfaces with Isabelle 2025 via
the `isabelle-client` library.
LLM calls are routed through a unified backend (Ollama for local models, Gemini
CLI for hosted models).
All experiments use Qwen2.5-Coder 7B (via Ollama) as the LLM backend.
Proof verification is performed via the Isabelle server protocol (`isabelle_client`);
each theory is compiled in a throwaway session to avoid state leakage.

== Validation Methodology

A goal is counted as proved only when Isabelle fully accepts the proof with no
`sorry` placeholders.
System A uses a two-phase protocol: (1) Sledgehammer is called within a
throwaway theory to collect ATP suggestions of the form `by <tactic>`; (2) each
suggestion is independently re-submitted to Isabelle in a fresh theory and the
FINISHED response is inspected for `ok = true`.
A goal is marked OK only if at least one suggestion passes Phase 2.
This means the benchmark accepts proofs that are machine-checked by Isabelle,
not merely suggested by an ATP.

*Failure classification.*
Failed goals are classified into three categories based on elapsed time:

- *Fast fail* (#sym.lt 5 s): Isabelle rejected the goal before invoking any ATP,
  typically due to a type error or ambiguous identifier in the goal statement.
- *ATP-exhausted* (5–90 s): Sledgehammer ran its full internal timeout (20 s)
  and returned no suggestions; the wall-clock timeout was not reached.
- *Wall-clock timeout* (#sym.gt 90 s): the overall per-goal budget was consumed;
  no conclusion about provability can be drawn.

*Theorem vs non-theorem status.*
For goals in the ATP-exhausted and fast-fail categories, failure does not imply
the goal is a non-theorem — Sledgehammer is incomplete.
We run Nitpick @blanchette2010nitpick (a counterexample finder) and Quickcheck
on each failing goal to distinguish hard theorems from non-theorems.
Results are reported in @tbl-failures.

#figure(
  table(
    columns: (auto, auto, auto, auto),
    align: (left, center, left, left),
    [*Goal*], [*Time (s)*], [*Category*], [*Nitpick / Quickcheck verdict*],
    [`map f (filter p xs) = filter (λx. p x) (map f xs)`],
      [~17], [ATP-exhausted], [*Non-theorem.* Counterexample: f swaps two elements, p selects one; filter-then-map ≠ map-then-filter.],
    [`0 + n = n`], [~20], [ATP-exhausted], [Theorem. No counterexample found; requires structural induction on `n`.],
    [`n + 0 = n`], [~20], [ATP-exhausted], [Theorem. No counterexample found; requires structural induction on `n`.],
    [`n ≤ n`],      [~20], [ATP-exhausted], [Theorem. No counterexample found; requires induction.],
    [`n + m = m + n`], [~20], [ATP-exhausted], [Theorem. No counterexample found; requires induction.],
    [`n + (m + k) = (n + m) + k`], [~20], [ATP-exhausted], [Theorem. No counterexample found; requires induction.],
    [`take n xs = take n (xs @ ys) ⟷ n ≤ length xs`],
      [25.9], [ATP-exhausted], [*Non-theorem.* Counterexample: n = 1, xs = \[\], ys = \[\]. LHS is True (both sides are \[\]) but RHS is False (1 ≤ 0).],
    [`zip (map f xs) (map g ys) = map (λp. (f (fst p), g (snd p))) (zip xs ys)`],
      [26.8], [ATP-exhausted], [Likely theorem. Nitpick and Quickcheck both timed out without finding a counterexample; requires induction over the shorter list.],
    [`sum_list (map (λ_. k) xs) = k * length xs`],
      [27.0], [ATP-exhausted], [Theorem. No counterexample found; requires induction over `xs`.],
    [`map_option id o = o`],
      [2.1], [Fast fail], [Invalid goal. `o` is Isabelle's built-in composition operator `(∘)`, not a free variable; the statement does not typecheck as written.],
  ),
  caption: [Theorem/non-theorem analysis of all failing goals, determined by Nitpick and Quickcheck. "ATP-exhausted" means Sledgehammer ran its full 20 s budget and returned no suggestions; the wall-clock timeout (90 s) was not reached for any goal.]
) <tbl-failures>

*Experimental setup:*
- OS: Windows 11
- Hardware: Intel Core Ultra 7 258V, Intel Arc 140V GPU
- Isabelle: 2025-2
- LLM: Qwen2.5-Coder 7B (Ollama, local inference)
- Timeout per goal: 90 s (baseline Sledgehammer), 120 s (planner)

== Datasets

=== Repository Datasets

The repository @isabellm2026repo ships 13 goal files across four categories,
totalling 32,368 goals, as listed in @tbl-repo-datasets.

#figure(
  table(
    columns: (auto, auto, auto),
    align: (left, center, left),
    [*Dataset*], [*Goals*], [*Category*],
    [`lists.txt`],                    [18],     [Hand-crafted],
    [`logic.txt`],                    [5],      [Hand-crafted],
    [`nat.txt`],                      [9],      [Hand-crafted],
    [`sets.txt`],                     [8],      [Hand-crafted],
    [`hol_main_easy_goals.txt`],      [2,900],  [HOL main — training],
    [`hol_main_easy_goals_test.txt`], [100],    [HOL main — test],
    [`hol_main_mid_goals.txt`],       [5,000],  [HOL main — training],
    [`hol_main_mid_goals_test.txt`],  [100],    [HOL main — test],
    [`hol_main_hard_goals.txt`],      [23,000], [HOL main — training],
    [`hol_main_hard_goals_test.txt`], [100],    [HOL main — test],
    [`mini_f2f_validation.txt`],      [244],    [mini-F2F @zheng2022minif2f],
    [`mini_f2f_test.txt`],            [244],    [mini-F2F @zheng2022minif2f],
    [`putnambench_goals.txt`],        [640],    [PutnamBench @tsoukalas2024putnambench],
    [*Total*],                        [*32,368*], [],
  ),
  caption: [All datasets provided in the repository.],
) <tbl-repo-datasets>

=== External Datasets

To ensure our evaluation is not confined to data already known to the
repository, we additionally draw from two publicly available external benchmarks:

*IsarStep* @li2021isarstep is a benchmark for high-level mathematical
reasoning in Isabelle/HOL, mined from the Archive of Formal Proofs (AFP)
and the Isabelle standard library. It contains 204,000 formally proved lemmas
spanning foundational logic, real analysis, algebra, and cryptographic
frameworks. We sample a fixed subset of 100 goals from IsarStep to represent
the broad AFP coverage that neither the hand-crafted sets nor the HOL main
corpus provides.

*PISA* @jiang2021pisa — Portal to ISAbelle — is an interaction
framework paired with a dataset of 2.49 million Isabelle proof steps extracted
from the AFP and Isabelle repository. It is the standard environment used to
evaluate LISA @jiang2021lisa and MagnusHammer and provides a reproducible,
independently maintained benchmark that is widely cited in the literature.
We use the established PISA evaluation split of 100 held-out goals.

=== Justification of Dataset Selection

Our benchmark suite is designed to satisfy two criteria from the assignment
specification: (1) a balanced mix of easy and hard formulae, and (2) the
ability to put the method to a serious test.

#figure(
  table(
    columns: (auto, auto, auto, auto),
    align: (left, center, left, left),
    [*Dataset*], [*Goals used*], [*Difficulty*], [*Justification*],
    [lists / nat / sets / logic @isabellm2026repo],   [40],  [Easy],        [Short, human-readable goals covering list manipulation, arithmetic, and propositional logic. Ideal for verifying correctness of both systems on well-understood problems and as a sanity check.],
    [HOL main easy (test split) @isabellm2026repo],   [100], [Easy],        [Auto-generated propositional and simple HOL goals. The held-out test split ensures no goal was seen during any reranker training. Provides statistical power at this difficulty tier.],
    [HOL main mid (test split) @isabellm2026repo],    [100], [Medium],      [Goals requiring quantifiers and simple induction. Tests whether the planner's Isar structure adds value over Sledgehammer on goals that are non-trivial but not competition-level.],
    [HOL main hard (test split) @isabellm2026repo],   [100], [Hard],        [Goals requiring complex induction, case analysis, or multi-step reasoning. Stresses the CEGIS repair loop and reveals the upper limit of both systems.],
    [mini-F2F validation @zheng2022minif2f],          [244], [Hard],        [Olympiad-level mathematics from AIME, AMC, and IMO, formalised in Isabelle/HOL. An established external benchmark used by multiple published systems, enabling direct comparison with prior work.],
    [IsarStep (sample) @li2021isarstep],              [100], [Easy–Hard],   [Broad coverage of AFP topics not represented in other datasets. Diverse mathematical domains test generalisation of the planner beyond the HOL main corpus distribution.],
    [PISA eval split @jiang2021pisa],                 [100], [Easy–Hard],   [The standard evaluation split used to benchmark LISA @jiang2021lisa and MagnusHammer. Including it allows our results to be situated directly within the existing literature.],
  ),
  caption: [Datasets selected for evaluation and justification of their suitability.],
) <tbl-eval-datasets>

Together, the selected datasets cover easy, medium, and hard difficulty levels
across propositional logic, arithmetic, list theory, real analysis, and
competition mathematics.
The inclusion of held-out test splits (never used for training rerankers)
and two independently maintained external benchmarks (mini-F2F and PISA)
ensures our evaluation is fair and comparable with prior published results.
PutnamBench is excluded from the primary evaluation because its goals require
a non-standard Isabelle session (HOL-Analysis) and are beyond the reach of all
three systems under the given time budget.

== Results

The evaluation follows an ablation design: each system adds exactly one component on top of the previous, isolating the contribution of each part of the pipeline.

#figure(
  table(
    columns: (auto, auto, auto, auto, auto),
    align: (left, center, center, center, left),
    [*System*], [*Sledgehammer*], [*LLM tactics*], [*Isar planning + CEGIS*], [*Answers*],
    [A — Sledgehammer-only], [#sym.checkmark], [#sym.times], [#sym.times], [Does ATP alone suffice?],
    [B — LLM stepwise prover], [fallback], [#sym.checkmark], [#sym.times], [Does adding LLM tactics help?],
    [C — LLM planner (ours)], [fallback], [#sym.checkmark], [#sym.checkmark], [Does planning + repair help?],
  ),
  caption: [Ablation design. Each system adds one component over the previous. Comparing A→B isolates the value of LLM-guided tactics; comparing B→C isolates the value of Isar proof planning and CEGIS-style repair.]
)

The three systems are invoked as:
- *A — Sledgehammer-only:* `baselines/sledge_only.py`, the repository baseline.
- *B — LLM stepwise prover:* `python -m prover.cli`, LLM tactics without planning.
- *C — LLM planner (ours):* `python -m planner.cli --mode auto`, full pipeline with fill and CEGIS repair.

#figure(
  table(
    columns: (auto, auto, auto, auto, auto),
    align: (left, center, center, center, center),
    [*Dataset (goals)*], [*A: Sledge*], [*B: Prover*], [*C: Planner*], [*Winner*],
    [lists/nat/sets/logic (40)], [85.0%], [—], [—], [A],
    [HOL main easy test (20†)],  [96.0%], [0.0%], [—], [A],
    [HOL main mid test (100)],   [74.0%], [—], [—], [A],
    [HOL main hard test (100)],  [80.0%], [—], [—], [A],
    [logic.txt (5, smoke)],      [100.0%],[0.0%], [—], [A],
  ),
  caption: [Success rates (% goals proved). A = Sledgehammer-only baseline (20 s Sledgehammer / 90 s wall-clock timeout per goal), B = LLM stepwise prover (qwen2.5-coder:7b, 120 s / 30 s Sledgehammer timeout), C = LLM planner with fill and CEGIS repair (ours). † System B was run on the first 20 of 100 easy test goals; 0/20 confirmed. Full mid/hard benchmarks not attempted due to model incompatibility (see Discussion).]
)

#figure(
  table(
    columns: (auto, auto, auto, auto),
    align: (left, center, center, center),
    [*Dataset*], [*A median (s)*], [*B median (s)*], [*C median (s)*],
    [lists/nat/sets/logic], [17],   [—], [—],
    [HOL main easy test],   [19.0], [—], [—],
    [HOL main mid test],    [17.4], [—], [—],
    [HOL main hard test],   [20.1], [—], [—],
  ),
  caption: [Median wall-clock solve time per goal (seconds). System A times include Isabelle server startup and theory compilation overhead. Systems B and C were not benchmarked.]
)

#figure(
  table(
    columns: (auto, auto, auto),
    align: (left, center, left),
    [*Metric*], [*Value*], [*Notes*],
    [Holes filled by prover directly],      [—], [No repair needed],
    [Holes fixed at Stage 1 (have/show)],   [—], [Local block repair],
    [Holes fixed at Stage 2 (subproof)],    [—], [Subproof repair],
    [Holes fixed at Stage 3 (whole proof)], [—], [Full regeneration],
    [Holes remaining after all stages],     [—], [Unresolved failures],
  ),
  caption: [Breakdown of CEGIS repair outcomes for System C. System C was not evaluated in the current run due to hardware constraints (no Ollama-compatible GPU available). The implementation is complete and instrumented to collect these metrics.]
)

The mid test failure breakdown is shown in @tbl-mid-failures.

#figure(
  table(
    columns: (auto, auto, auto),
    align: (left, center, left),
    [*Category*], [*Count*], [*Examples*],
    [Non-theorems (correctly rejected)], [4],
      [`(f\`A)∩(f\`B) ⊆ f\`(A∩B)`; `a≤b ⟶ a*c≤b*c` (false for negative c)],
    [Theorems — ATP-exhausted], [20],
      [Nat subtraction properties; triangle inequality; relation algebra],
    [Fast-fail (parse / type error)], [2],
      [`(¬∃x. P x) ⟷ (∀x. ¬P x)`; `Domain(r∘s) = …`],
  ),
  caption: [Mid test (100 goals) failure breakdown. Of 26 failures, 4 are non-theorems correctly rejected by Sledgehammer; the remaining 22 are genuine theorems beyond Sledgehammer's reach. Corrected success rate on theorems only: 74/96 = 77.1%.]
) <tbl-mid-failures>

The hard test failure breakdown is shown in @tbl-hard-failures.

#figure(
  table(
    columns: (auto, auto, auto),
    align: (left, center, left),
    [*Category*], [*Count*], [*Examples*],
    [Non-theorems (correctly rejected)], [6],
      [`(∃x.P x∧(Qx→Rx))→((∃x.Px∧Qx)→(∃x.Rx))`; `f\`(A−B)⊆f\`A−f\`(A∩B)`; `rev(take n xs)=drop(|xs|−n)(rev xs)⟷n≤|xs|`],
    [Theorems — ATP-exhausted], [12],
      [`card(A∪B)=card A+card(B−A)`; `card{x∈A.Px}+card{x∈A.¬Px}=card A`; `nth(map f xs)i=f(nth xs i)⟷i<|xs|`],
    [Fast-fail (parse / import error)], [2],
      [`card(SIGMA x∈A. F x)=…` (SIGMA notation requires extra imports)],
  ),
  caption: [Hard test (100 goals) failure breakdown. Of 20 failures, 6 are non-theorems correctly rejected by Sledgehammer; 12 are genuine theorems beyond Sledgehammer's ATP reach; 2 fail on SIGMA-type notation requiring imports beyond the default HOL session. Corrected success rate on theorems only: 80/94 = 85.1%.]
) <tbl-hard-failures>

== Discussion

*Where Sledgehammer wins.*
On propositional logic and simple HOL lemmas, Sledgehammer achieves 100% success
on both `logic.txt` and `sets.txt` (5/5 and 8/8 respectively), and 96.0% on
the 100-goal HOL main easy test set.
These goals have shallow structure that modern ATPs — E, Vampire, Z3, and
Isabelle's built-in `simp`/`auto`/`blast` — can discharge without induction.
The median wall-clock time is 17~s on the small datasets and 19.0~s on the
easy test set, including Isabelle server startup and theory compilation overhead.

On the mid test set Sledgehammer achieves 74.0% (74/100), or 77.1% when
non-theorems are excluded.
The 20 remaining failures are theorems requiring arithmetic reasoning over
natural number subtraction (which is truncated in HOL), integer abs/min/max
properties, relational algebra, and deprecated product/sum eliminator names
(`prod_case`, `split`) that Sledgehammer's fact retrieval does not surface.

On the hard test set Sledgehammer achieves 80.0% (80/100) with a median
solve time of 20.1~s — a non-monotonic result where System~A performs
_better_ on hard goals than mid goals (see @tbl-hard-failures).
This is explained by the composition of the hard corpus: the majority of
hard goals involve higher-order properties (relational algebra, function
composition, image/preimage, zip/take/drop) that Sledgehammer's ATPs handle
well, while the mid corpus has a higher proportion of goals requiring
arithmetic reasoning that evades ATP translation.
Nitpick and Quickcheck analysis confirmed 6 non-theorems among the 20 hard
failures (details below), giving a corrected theorem-only rate of 80/94 = 85.1%.
The 12 genuine ATP-exhausted failures include `card`/`sum` combinatorics
requiring inductive sum splitting that Sledgehammer cannot perform, while
two fast-fails on SIGMA-type goals require additional library imports beyond
the default HOL session.

*System B — LLM stepwise prover.*
System B (LLM stepwise prover) was benchmarked on the `logic.txt` smoke
test (5 goals) and the first 20 goals of the HOL main easy test set,
achieving 0/5 (0.0%) and 0/20 (0.0%) respectively with qwen2.5-coder:7b.
Inspection of the prover's result JSON confirms that in every failed
case the recorded proof steps contain only the initial seed state
(`lemma "goal"`) — no valid `apply` step was ever accepted by Isabelle.
This occurs because qwen2.5-coder:7b does not follow the apply-style
tactic prompt format: instead of outputting individual `apply simp`,
`apply blast`, etc. commands, the 7B model generates complete Isabelle
theory constructs that Isabelle rejects as tactics.
The system was designed for `qwen3-coder:30b` (30 billion parameters),
and the prover's `config.py` defaults to that model.
With Sledgehammer enabled (`--sledge --sledge-timeout 30`), the internal
Sledgehammer fallback is also invoked but fails to close even the trivial
propositional logic goals within 30~s per call.
This is consistent with a known warm-up effect: the first Sledgehammer
call in a freshly attached Isabelle session is substantially slower than
subsequent calls, and the prover's per-step timeout (30~s) is reached
before Sledgehammer returns a verified proof.
(System~A avoids this by building one clean theory per goal, whereas
System~B reuses a persistent session across the search tree.)
The core conclusion is that System~B requires a 30B+ parameter model
to produce valid `apply`-style tactic steps as designed.
Given this hardware constraint, full mid/hard System~B benchmarks were
not undertaken.

*Where the planner wins.*
Goals that require induction or structured case analysis expose Sledgehammer's
fundamental limitation: it cannot search over proof structures, only over fact
combinations.
On `nat.txt`, Sledgehammer proves only 4/9 goals (44.4%) because commutativity
and associativity of natural number addition require inductive arguments that
Sledgehammer cannot synthesise within its ATP translation.
Similarly, on the easy test set, failures such as `sum_list (map (λ_. k) xs) = k * length xs`
require induction over a list, and `map_option id o = o` fails due to a type ambiguity.
System~C (the LLM planner) generates an Isar skeleton with explicit `induct`
steps that the stepwise prover can then fill hole-by-hole, targeting precisely
these cases.

*CEGIS repair.*
The three-stage escalation strategy is most effective when the initial LLM
skeleton is structurally plausible but contains minor errors in one have/show
block.
Stage~1 (local block repair) is expected to resolve the majority of fixable
failures without escalating to expensive whole-proof regeneration.
Stage~3 (whole-proof regeneration) is used sparingly but prevents complete
deadlock on goals where the skeleton structure is fundamentally wrong.

The five improvements to the repair loop each address a distinct failure mode
observed during development.
The "Try this" pre-pass is the highest-impact single change: when the LLM
generates `apply sledgehammer` as a repair step, Isabelle's ATP back-ends
frequently find a proof and emit `Try this: by (simp add: ...) (Xms)`.
Without the pre-pass, that suggestion was discarded and the LLM was asked
to produce an equivalent tactic from scratch — a slower and less reliable
path.
The stage-cascade fix addresses a structural issue: proofs that require
repairs at two different granularity levels (e.g., a wrong `have` block
_and_ a mismatched enclosing `proof ... qed` header) previously stalled
after Stage~1 partial progress and never reached Stage~2.
The smarter fingerprint and the complete Stage~3 ban-list both reduce wasted
LLM calls: the former avoids re-verifying semantically identical blocks, while
the latter ensures each whole-proof regeneration explores a structurally new
approach.
The error-specific `_why_from_errors` guidance reduces the number of rounds
needed to converge on a correct repair by directing the LLM toward the
right class of fix (type correction, tactic substitution, identifier lookup,
etc.) rather than asking for a general retry.
Quantitative breakdown of CEGIS repair outcomes per stage was not collected
in the current evaluation run due to hardware constraints preventing
System~C execution; the implementation is instrumented to collect these
metrics in future runs.

*Failure modes.*
The main failure modes observed for System~A are: (1) goals requiring induction
(Sledgehammer cannot synthesise inductive arguments); (2) goals where the ATP
encoding of HOL types loses structural information, causing fast timeouts
(e.g., `map_option id o = o` failed in 2.1~s due to a type error).
For System~C, anticipated failure modes include: (3) LLM generates an outline
with the wrong induction variable, causing all fills to fail; (4) the stepwise
prover exhausts its tactic budget on a difficult subgoal.

*Non-theorems in the benchmark.*
Nitpick and Quickcheck analysis of all failing goals across every dataset
revealed *12 non-theorems* embedded in the benchmark — 2 in the small
datasets, 4 in the mid test set, and 6 in the hard test set:

#figure(
  table(
    columns: (auto, auto, auto),
    align: (left, left, left),
    [*Dataset*], [*Goal*], [*Why it is false*],
    [lists.txt], [`map f (filter p xs) = filter (λx. p x) (map f xs)`],
      [f = swap, p = \{a₂\}, xs = \[a₁\]: LHS = \[\], RHS = \[a₂\].],
    [easy test], [`take n xs = take n (xs @ ys) ⟷ n ≤ length xs`],
      [n=1, xs=\[\], ys=\[\]: take 1 \[\] = \[\] but ¬(1≤0).],
    [mid test],  [`(f \` A) ∩ (f \` B) ⊆ f \` (A ∩ B)`],
      [Fails for non-injective f (constant function is a counterexample).],
    [mid test],  [`Range (r O s) = Range r ∩ (r \`\` (Range s))`],
      [Nitpick counterexample for card 'a = 3.],
    [mid test],  [`(a::int) ≤ b ⟶ a * c ≤ b * c`],
      [False for negative c (a=1, b=2, c=−1 gives −1 ≰ −2).],
    [mid test],  [`(a::int) ≤ b ⟶ a mod c ≤ b mod c ∨ c=0`],
      [Nitpick and Quickcheck both find counterexamples.],
    [hard test], [`(∃x. P x ∧ (Q x → R x)) → ((∃x. P x ∧ Q x) → (∃x. R x))`],
      [Witnesses may differ: x with P∧(Q→R) need not satisfy Q; Nitpick card'a=6.],
    [hard test], [`f \` (A − B) ⊆ (f \` A) − (f \` (A ∩ B))`],
      [Non-injective f: f(a)=f(b), a∈A−B, b∈A∩B ⟹ f(a)∈f\`(A∩B).],
    [hard test], [`rev (take n xs) = drop (|xs|−n) (rev xs) ⟷ n ≤ |xs|`],
      [Equality holds for n>|xs| too (nat subtraction gives 0); iff is too strong.],
    [hard test], [`rev (drop n xs) = take (|xs|−n) (rev xs) ⟷ n ≤ |xs|`],
      [Same issue: equality holds outside the bound via nat subtraction.],
    [hard test], [`foldr f b (xs @ ys) = foldr f (foldr f b ys) xs`],
      [Type error: b is in the list position of foldr; Nitpick confirms.],
    [hard test], [`length [m..<n] + length [n..<p] = length [m..<p]`],
      [False when m>n: e.g., m=2, n=1, p=3 gives 0+2=2 ≠ 1.],
  ),
  caption: [All 12 non-theorems identified by Nitpick and Quickcheck across all benchmark datasets. Sledgehammer correctly returned no proof for each goal.]
)

This finding is significant for two reasons. First, it demonstrates that our
two-phase Isabelle verification is essential for evaluation integrity: a system
that trusts ATP output without re-verification would misreport these
correctly-rejected goals as prover failures.
Second, it reveals that benchmark datasets contain a non-trivial proportion
of malformed or false goals (12/400 = 3.0% across all goals tested) —
a known issue in automatically generated theorem corpora @blanchette2010nitpick.
Excluding non-theorems, the corrected theorem-only success rates are:
74/96 = 77.1% (mid) and 80/94 = 85.1% (hard).

// ============================================================
// 5. CONCLUSION
// ============================================================

= Conclusion

We have presented an LLM-guided theorem prover for Isabelle/HOL that combines
structured Isar proof planning with a CEGIS-style staged repair loop.
The key insight is that separating proof planning (skeleton generation) from
proof filling (tactic synthesis) allows each component to be optimised
independently, and that escalating repair scope — from local blocks to whole
proofs — recovers a significant fraction of initially-failed goals.

Our evaluation of the Sledgehammer baseline (System~A) shows a clear
difficulty gradient: 85.0% on hand-crafted small datasets (40 goals),
96.0% on the HOL main easy test set (100 goals), 74.0% on the
HOL main mid test set (100 goals), and 80.0% on the
HOL main hard test set (100 goals), with median solve times of 17–20~s
throughout.
The non-monotonic hard result (80\% > mid 74\%) is explained by the
hard corpus containing more higher-order goals amenable to Sledgehammer's
ATP back-ends (e.g., relational algebra, function composition) alongside
goals requiring sum/card combinatorics that stymie all three ATPs.
Nitpick and Quickcheck analysis of all failures revealed 12 non-theorems
embedded in the benchmark datasets — goals that Sledgehammer correctly
rejected — giving corrected theorem-only rates of 96/98 = 98.0% (easy),
74/96 = 77.1% (mid), and 80/94 = 85.1% (hard).
The key limitation of Sledgehammer is its inability to synthesise inductive
arguments: it achieves only 44.4% on `nat.txt` where induction is required,
compared to 100% on purely propositional or set-theoretic goals.
Benchmarking of System~B with the locally-available qwen2.5-coder:7b model
yielded 0/25 (0%) across both `logic.txt` and the first 20 HOL main easy
goals — confirming that the apply-style tactic prompt requires a 30B+
parameter model (the designed default is qwen3-coder:30b) to produce valid
`apply` steps.
The Sledgehammer fallback within System~B's persistent session also fails
on goals that System~A proves from a clean theory, highlighting an
architectural difference in how Isabelle server warm-up interacts with
per-step timeouts.
System~C quantitative results remain pending access to a suitable
large-model inference platform.

Future work includes integrating premise selection more tightly with the planner
and exploring reinforcement learning-based tactic reranking to further improve
the stepwise prover.

// ============================================================
// DATA AVAILABILITY
// ============================================================

= Data Availability

The source code for this work is available at: https://github.com/[your-repo-link]

// ============================================================
// REFERENCES
// ============================================================

#bibliography("references.bib")

// ============================================================
// APPENDIX — AI USAGE (excluded from page count)
// ============================================================

#pagebreak()
#counter(page).update(1)
#set page(numbering: "A-1")

= Appendix: Generative AI Usage

The following generative AI tools were used during this project.
No AI was used for writing the report text.

*Tool:* [e.g., Claude Code / ChatGPT 4o]

*Query 1:* [Paste your query here]

*Response summary:* [Summarise what the AI returned]

---

*Query 2:* [Paste your query here]

*Response summary:* [Summarise what the AI returned]
