#import "@preview/fine-lncs:0.4.0": lncs, institute, author, theorem, proof

#set cite(style: "ieee")

#let inst_princ = institute("Griffith University",
  addr: "Southport QLD 4215, AUS"
)

// ── Shared node style for pipeline figure ──
#let pnode(w: 100%, body) = rect(
  stroke: 0.6pt, inset: (x: 8pt, y: 5pt),
  radius: 2pt, fill: luma(248), width: w, body
)

#show: lncs.with(
  title: "LLM-Guided Isabelle/HOL Theorem Proving with Isar Proof Planning and CEGIS-Style Repair",
  authors: (
    author("Sam Li", insts: (inst_princ)),
    author("Luca Roescheisen", insts: (inst_princ)),
  ),
  abstract: [
    Automated theorem proving in interactive proof assistants such as Isabelle/HOL
    requires both strategic proof planning and low-level tactic synthesis — two
    tasks that are individually difficult and jointly challenging for any single model.
    This paper presents a complete LLM-guided prover built on top of the Isabellm
    framework @hou2026isabellm, extending two previously incomplete components:
    a hole-filling driver that delegates each sorry placeholder in an Isar skeleton
    to a stepwise tactic prover, and a CEGIS-style three-stage repair loop.
    The stepwise prover combines LLM beam search with Sledgehammer, Quickcheck, Nitpick,
    an ML tactic reranker, and two-stage TF-IDF premise selection backed by a
    theory-aware context window (Micro RAG).
    The planner samples diverse Isar outlines, selects the one Isabelle can verify
    furthest, and fills holes top-down; when filling fails, a staged repair procedure
    targets successively larger regions — from a single have/show block up to
    the entire proof — augmented with five targeted improvements.
    We evaluate System~A (Sledgehammer-only baseline) across four benchmark suites
    spanning 340 goals and three difficulty levels, achieving 85--96\% on easy goals
    and confirming 12 embedded non-theorems via Nitpick and Quickcheck.
    We further show that System~B (LLM stepwise prover), after correcting a
    Pydantic v2 deserialization bug that masked all verification results, achieves
    100\% on propositional logic goals matching System~A.
    The key lesson is that the system integration layer — correct state extraction
    and verification round-tripping through the Isabelle server protocol — is as
    critical as the LLM or ATP component, and that a 7B parameter model can drive
    Sledgehammer-backed proof search effectively while falling short of LLM-only
    tactic generation.
  ],
  keywords: ("Automated Theorem Proving", "Isabelle/HOL", "Large Language Models",
             "Isar Proofs", "CEGIS", "Proof Repair", "Premise Selection"),
)

// ============================================================
// 1. INTRODUCTION
// ============================================================

= Introduction

Interactive theorem proving (ITP) systems such as Isabelle/HOL @nipkow2002isabelle,
Coq, and Lean provide machine-checked formal verification that is relied upon in
safety-critical software, mathematics formalisation, and compiler verification.
However, writing proofs by hand remains a labour-intensive task requiring deep
expertise in both the subject domain and the tactic language of the prover.
The rise of large language models (LLMs) capable of code synthesis has opened
a new direction: using LLMs to propose proof tactics, proof structures, or
entire proof bodies that an ITP then verifies.

Early neural proof search systems — PISA/LISA @jiang2021lisa and Draft-Sketch-Prove
— showed that LLMs can propose valid Isabelle tactics with moderate accuracy on
individual proof steps.
More recent work couples LLMs with Isabelle's built-in ATP interface, Sledgehammer
@paulson2012sledgehammer, which invokes external solvers (E, Vampire, Z3, CVC5) and
translates their results back into Isabelle tactics.
Hybrid systems such as MagnusHammer @mikula2023magnushammer use learned premise
selectors to narrow Sledgehammer's search space, substantially improving success rates
on the Archive of Formal Proofs (AFP).

Despite this progress, a practical gap remains.
Sledgehammer alone cannot synthesise multi-step Isar proofs that require explicit
induction, case analysis, or chains of intermediate lemmas.
Conversely, plain LLM tactic generation without a proof skeleton frequently produces
incoherent proof states.
The Isabellm framework @hou2026isabellm proposes a two-stage pipeline — an Isar proof
planner followed by a stepwise hole filler — but leaves both the hole-filling driver
and the CEGIS repair loop as non-functional stubs.

This paper closes that gap.
We complete the Isabellm pipeline and evaluate it against two baselines on benchmark
suites drawn from the HOL main corpus @isabellm2026repo and the mini-F2F benchmark
@zheng2022minif2f.

Our contributions are:

#list(
  [*Hole-filling driver:* a complete `driver.py` that locates each sorry span,
   extracts the precise Isabelle proof state at that point via the server protocol,
   and delegates to the stepwise prover — with correct handling of induction
   skeletons, have/show contexts, and stale-hole detection.],

  [*CEGIS repair loop:* a three-stage escalation in `repair.py` with five
   targeted improvements: smarter block deduplication (synonym normalisation,
   sorted simp-lemma lists, label canonicalisation), error-category--specific
   LLM guidance, a cumulative Stage~3 ban-list, a pre-LLM "Try this:" pass,
   and stage-cascade continuation after partial repairs.],

  [*System integration fixes:* correction of a Pydantic v2 deserialization bug
   in `isabelle_api.py` that caused `finished_ok` to always return `False`,
   silently masking all verified proofs across every component.],

  [*Reproducible evaluation:* systematic benchmarking of three systems across
   four difficulty tiers with Nitpick/Quickcheck analysis of all failures,
   revealing 12 non-theorems embedded in the benchmark datasets.],
)

// ============================================================
// 2. RELATED WORK
// ============================================================

= Related Work

*LLM-based tactic provers.*
LISA @jiang2021lisa fine-tunes a transformer on Isabelle proof corpora to predict
the next tactic given a proof state, achieving 39\% pass rate on a held-out AFP
split within 10 interaction steps.
Its key limitation is the fine-tuning requirement: LISA requires thousands of
GPU-hours to train and is locked to the model checkpoint, making it difficult
to swap in newer LLMs.
Draft-Sketch-Prove uses an LLM in zero-shot mode to draft a high-level proof
sketch in natural language, translates it into formal proof steps, and verifies
each step with Isabelle.
While model-agnostic, it does not perform repair: when a sketch step fails,
the system abandons the goal rather than attempting to fix the individual step.
Hypertree Proof Search @lample2022hypertree extends LLM-guided tactic generation
with a Monte-Carlo tree search over the proof state graph, achieving state-of-the-art
results on Lean's miniF2F benchmark but at the cost of extremely high inference
compute (${\sim}600$ model evaluations per goal).

*Sledgehammer and ATP integration.*
Sledgehammer @paulson2012sledgehammer is the standard ATP backend for Isabelle/HOL.
It encodes a subgoal into first-order logic, invokes external ATPs in parallel,
and translates successful ATP certificates back into Isabelle `metis` or `smt` calls.
MagnusHammer @mikula2023magnushammer integrates a DeBERTa-based premise selector that
retrieves the 1024 most relevant lemmas from the AFP before calling Sledgehammer,
reducing the search space and improving recall from 46\% to 59\% on the PISA
evaluation split.
Our premise selection module (`premises.py`) follows a similar two-stage design
(TF-IDF select + optional re-rank) but uses lighter-weight retrievers compatible
with local inference.

*Proof repair and CEGIS.*
CEGIS (Counterexample-Guided Inductive Synthesis) @solar2006cegis is a classical
synthesis framework in which a synthesiser proposes a candidate and a verifier
either accepts it or returns a counterexample.
Baldur @first2023baldur applies this idea to Lean: given a failed proof attempt,
an LLM generates repairs guided by the compiler error message.
Baldur's repair is flat (single-stage), whereas ours uses a three-stage escalation
from local have/show blocks to whole-proof regeneration, which avoids spending
regeneration budget on goals fixable with a one-line tactic change.

*Comparison with related work.*
@tbl-related-comparison summarises the key properties of each system relative to ours.

#figure(
  table(
    columns: (auto, auto, auto, auto, auto, auto),
    align: (left, center, center, center, center, center),
    [*System*], [*Model-agnostic*], [*No fine-tuning*], [*Isar planning*],
      [*CEGIS repair*], [*Premise sel.*],
    [LISA @jiang2021lisa],             [#sym.times], [#sym.times], [#sym.times], [#sym.times], [#sym.times],
    [Draft-Sketch-Prove],              [#sym.checkmark], [#sym.checkmark], [Sketch], [#sym.times], [#sym.times],
    [Hypertree @lample2022hypertree],  [#sym.times], [#sym.times], [#sym.times], [#sym.times], [#sym.times],
    [MagnusHammer @mikula2023magnushammer], [#sym.checkmark], [#sym.checkmark], [#sym.times], [#sym.times], [#sym.checkmark],
    [Baldur @first2023baldur],         [#sym.checkmark], [#sym.checkmark], [#sym.times], [Flat], [#sym.times],
    [*Ours*],                          [*#sym.checkmark*], [*#sym.checkmark*], [*#sym.checkmark*], [*Staged*], [*#sym.checkmark*],
  ),
  caption: [Comparison of related systems. "Isar planning" means the system generates
  a structured Isar skeleton (not just individual tactics). "CEGIS repair" indicates
  whether failed proof attempts are repaired iteratively. "Premise sel." means the
  system retrieves relevant library lemmas to hint the LLM or ATP.]
) <tbl-related-comparison>

Unlike LISA and Hypertree, our system requires no fine-tuning and supports any
Ollama-compatible or Gemini/Hugging Face model.
Unlike Draft-Sketch-Prove, we repair failed holes rather than abandoning them.
Unlike MagnusHammer, we combine premise selection with a full proof planner
and a CEGIS repair loop, rather than using premise retrieval as a Sledgehammer
pre-filter only.

// ============================================================
// 3. PROPOSED APPROACH
// ============================================================

= Proposed Approach

Our system extends the Isabellm framework @hou2026isabellm with three previously
non-functional components: (1) the stepwise prover with premise selection,
context retrieval, and ML reranking; (2) the hole-filling driver; and
(3) the CEGIS-style staged repair loop.
@fig-pipeline shows the full pipeline.

#figure(
  align(center,
    grid(
      columns: 1, row-gutter: 3pt,
      pnode(w: 80%)[*Goal $G$*],
      align(center)[$arrow.b$],
      pnode(w: 80%)[*Isar Outline Generator* (`skeleton.py`)\ LLM samples $k$ outlines at temps $T_1 < T_2 < T_3$; select by Isabelle progress],
      align(center)[$arrow.b$],
      pnode(w: 80%)[*Hole-Fill Loop* (`driver.py`)\ For each sorry (top-down): extract state → stepwise prover → replace sorry],
      align(center)[no sorry left #h(1em) $arrow.b$ #h(4em) $arrow.b$ #h(1em) fill fails / non-hole error],
      grid(
        columns: (1fr, 1fr), column-gutter: 4pt,
        pnode[*Verify* (`isabelle_api.py`)\ finished\_ok? ✓ Done],
        pnode[*CEGIS Repair* (`repair.py`)\ Stage 1 → Stage 2 → Stage 3],
      ),
    )
  ),
  caption: [Full pipeline. The stepwise prover (used inside the Hole-Fill Loop) combines
  LLM beam search, Sledgehammer, ML reranking, and premise selection.
  CEGIS repair escalates repair scope when filling fails.]
) <fig-pipeline>

== Isar Proof Planning

Given a goal $G$, the planner prompts an LLM to generate a structured Isar proof
outline with `sorry` placeholders.
Easy goals may receive a direct `by simp` or `by auto` one-liner.
Harder goals receive a multi-step structure with explicit `induction`, `case`,
`have`, and `show` blocks — the LLM provides the proof architecture but defers
the tactic-level details to the hole filler.

To promote diversity and avoid local optima in outline selection, we sample
$k = 3$ outlines at temperatures $T_1 = 0.35$, $T_2 = 0.55$, $T_3 = 0.85$.
Each outline is submitted to Isabelle; the one that Isabelle can verify
furthest before the first error is selected as the starting point for hole filling.
A sanitisation pass (`_sanitize_outline`) repairs common LLM errors:
orphan `sorry` statements outside have/show bodies, unmatched `proof`/`qed` pairs,
and malformed `?thesis`/`?case` meta-variables in induction branches.

== Stepwise Prover

The stepwise prover (`prover.py`, `prove_goal`) is an iterative beam search over
Isabelle tactic sequences.
It maintains a beam of partial proof states and, at each step, queries the LLM
for candidate next tactics, verifies each with Isabelle, and retains the states
that make progress.

*Beam search and Sledgehammer integration.*
At each depth level, Sledgehammer is optionally invoked on the current goal —
every `sledge_every` steps — to inject ATP-discovered finishing tactics into
the beam alongside LLM proposals.
This hybrid approach ensures that goals solvable by pure ATP never waste LLM
budget, while goals requiring structured reasoning still receive LLM guidance.

*ML tactic reranker.*
Before submitting beam candidates to Isabelle for verification, each tactic string
is scored by a lightweight trained classifier (`ranker.py`).
The classifier is a joblib-serialised scikit-learn model or, when available,
a TorchScript `.pt` model loaded via `torch.jit.load`.
It predicts the probability that the tactic closes or advances the proof state,
based on features including the tactic type, presence of specific lemma names,
and goal complexity.
Candidates are sorted by score before verification, putting the most likely tactic
first so that the verification budget is used efficiently.
The reranker gracefully falls back to a uniform score of 0.5 when no model file
is present, preserving correctness without reranking.

*Premise selection.*
The prover optionally retrieves relevant library lemmas to hint the LLM and
extend Sledgehammer's fact set (`premises.py`).
Retrieval uses a two-stage design.
The SELECT stage uses TF-IDF cosine similarity (scikit-learn @pedregosa2011sklearn)
to narrow the corpus from thousands of lemmas to the top $k_1$ candidates;
the RE-RANK stage uses a bi-encoder or cross-encoder (when a trained model is
provided) to re-score the shortlist and return the final top $k_2$ hints.
The `PremisesIndex` class builds and serves the TF-IDF matrix lazily and is
thread-safe for concurrent inference.

*Micro RAG / Theory Context.*
Standard premise retrieval covers the Isabelle standard library and AFP,
but misses facts defined in the same theory file or closely related `.thy` files.
`context.py` implements a lightweight theory parser that scans supplied `.thy` files
for definitions, abbreviations, functions, datatypes, and lemmas, recording
each fact with a stable identifier and byte offset.
The $k$ facts textually nearest to the current proof location are passed to the
premise selector as a boosting signal, implementing a form of
retrieval-augmented generation grounded in the local theory context.

== Hole Filling

`driver.py` orchestrates the fill loop.
For each `sorry` span (processed left-to-right, top-down to match Isabelle's
earliest-failure-first semantics):

+ Ask Isabelle: "what is the exact proof state at this sorry?" via
  `_print_state_before_hole`, which inserts a `print_state` command before the hole
  and parses the resulting `proof (state)` or `proof (prove)` block.
+ Extract the subgoal from the state block via `_effective_goal_from_state`,
  normalising renamed free variables (e.g., `xsa` back to `xs`).
+ Call `prove_goal` with the extracted subgoal and the full Isabelle state as
  an initial hint.
+ On success, insert the tactic sequence in place of `sorry`; on a pure `by X`
  result, replace the entire enclosing `proof...qed` block with the one-liner.
+ Verify the updated full proof with Isabelle; if verification fails, revert
  the insertion and escalate to repair.

*Apply-only results inside have/show blocks.*
When `prove_goal` returns only `apply` steps with no closing `by ... ` or `done`,
the result is invalid in `have`/`show` context (Isabelle's `prove` mode requires a
finishing tactic).
The filler detects this condition via `_HEAD_CMD_RE` and immediately escalates
to the finisher-only re-probe path (`_fill_one_hole_finisher_only`), which restricts
the prover to depth-1 beam search to force a closing tactic before falling through
to the full CEGIS repair loop.

== CEGIS Repair Loop

When the filler cannot close a hole, or when Isabelle fails at a non-hole line
(a bad `have`/`show` statement, wrong facts, syntax or type error), the system
enters the staged repair loop in `repair.py` (`try_cegis_repairs`).

*Stage 1 — local have/show block.*
The system locates the smallest enclosing `have`/`show`/`obtain` block around
the earliest failure point (determined by `_earliest_failure_anchor`, which runs
Isabelle and parses the first error line to avoid targeting symptoms of a deeper bug).
Before any LLM call, two deterministic pre-passes fire.
First, a proactive Sledgehammer pass (`_proactive_sledgehammer_suggestions`) replaces
the first `sorry` in the block with `by sledgehammer`, submits the modified theory,
and collects any `Try this:` suggestions the ATP returns; these are tried immediately,
bypassing the LLM entirely when the ATP finds a solution.
Second, a passive `Try this:` extraction (`_extract_try_this_suggestions`) harvests
suggestions that Isabelle's `solve_direct` already embedded in prior error messages.
Only if both pre-passes fail is the LLM invoked to regenerate the block.

*Stage 2 — case block and subproof.*
If Stage~1 makes no progress or leaves the proof unverified, Stage~2a regenerates
the enclosing `case ... next/qed` block (for induction proofs), then Stage~2b
regenerates the enclosing `proof ... qed` subproof.
Stage cascading: when Stage~1 changes the proof but does not fully solve it,
the filler re-locates the first remaining sorry in the updated text and falls
through to Stage~2 with the improved proof — rather than bailing out early
and losing the partial repair.

*Stage 3 — whole-proof regeneration.*
If Stages~1 and~2 exhaust their attempt budgets without success, the entire
proof body is regenerated from scratch.
A cumulative ban-list (`failed_outlines` in `driver.py`, passed via
`prior_outline_texts`) ensures that each Stage~3 call receives all previously
failed proof structures and avoids repeating them.

*Nine improvements to the baseline.*

+ *Smarter block deduplication* (`_fingerprint_block`): normalises ATP synonyms
  (`auto`/`blast`/`fastforce`/`clarsimp` → `ATP`), sorts `simp add:` lemma lists,
  and maps generated fact labels (`f1`, `h2`, `g3` → `fN`). Blocks with the
  same fingerprint are skipped without an Isabelle call.

+ *Error-category--specific guidance* (`_why_from_errors`): classifies Isabelle's
  error text into eight categories (type clash, tactic failure, unknown identifier,
  unification failure, no subgoals, constructor clash, locally-fixed variable,
  stale sorry) and sends a targeted one-sentence diagnosis with each LLM call.

+ *Complete Stage~3 ban-list*: accumulates all tried outlines in `driver.py`
  and passes the full list to `regenerate_whole_proof`, preventing repetition
  across multiple Stage~3 invocations.

+ *Passive "Try this" pre-pass*: extracts Isabelle's own Sledgehammer/`solve_direct`
  suggestions from existing error messages before any LLM call, allowing many
  repairs to complete without spending any LLM token budget.

+ *Stage-cascade continuation*: propagates partial repairs from Stage~1 into
  Stage~2 rather than returning `False` immediately on unverified changes.

+ *Proactive Sledgehammer at Stage~1 entry* (`_proactive_sledgehammer_suggestions`):
  replaces the first `sorry` in the failing block with `by sledgehammer` and submits
  the modified theory to Isabelle, collecting `Try this:` ATP suggestions before any
  LLM call. This is an active complement to the passive pre-pass: whereas the passive
  pass collects suggestions that appeared in prior error output, the proactive pass
  solicits new ATP suggestions for the current subgoal state, catching cases where
  Sledgehammer was never previously invoked on that exact goal.

+ *Deterministic `arbitrary:` repair* (`_maybe_fix_arbitrary`): detects Isabelle's
  `locally fixed variable X` error and rewrites `proof (induction xs)` to
  `proof (induction xs arbitrary: X)` in a single string substitution — no LLM call,
  no Isabelle round-trip beyond a verification check. Applied before Stage~1;
  handles the most common induction failure mode for goals involving two list or
  natural-number variables.

+ *Alternating-temperature LLM sampling*: repair rounds cycle through temperatures
  `[default, 0.7]`, so successive LLM calls explore different regions of the output
  distribution without increasing the total number of calls. Round 0 uses the
  configured default temperature; round 1 uses $T = 0.7$; round 2 returns to the
  default, and so on.

+ *Induction hypothesis injection* (`_extract_induction_hyps`): parses lines of the
  form `Cons.IH : P xs` from the Isabelle proof state and appends them as a dedicated
  `INDUCTION_HYPOTHESES` section in the LLM repair prompt. The LLM can then directly
  reference the available induction hypotheses in `using` clauses rather than
  inferring their names from context, reducing hallucinated fact references.

// ============================================================
// 4. IMPLEMENTATION AND EXPERIMENTS
// ============================================================

= Implementation and Experiments

== Implementation

The system is implemented in Python 3.11 and interfaces with Isabelle 2025-2
via the `isabelle-client` library @wenzel2021isabelleclient.
LLM calls are routed through a unified backend supporting Ollama @ollama2024
(for local models), Gemini CLI (hosted), and Hugging Face inference.
All experiments use Qwen2.5-Coder 7B @hui2024qwen2 (via Ollama) as the LLM backend;
the system is designed for Qwen3-Coder 30B as the primary model but operates
with 7B under hardware constraints.
Premise retrieval uses scikit-learn @pedregosa2011sklearn for TF-IDF indexing
and optionally PyTorch @paszke2019pytorch for reranker inference.
Proof verification is performed via the Isabelle server protocol: each theory
is compiled in a throwaway session to avoid state leakage between goals.

A critical integration bug was discovered and fixed during development:
the `use_theories` response from the Isabelle server is deserialised by
`isabelle-client` as a Pydantic v2 `UseTheoriesResults` model object.
The `finished_ok` function accessed the result body via dict-key lookup,
which always fell through to the `(False, {})` fallback on a Pydantic model
object, making every verification call appear to fail.
The fix in `_decode_body_to_dict` adds a `.model_dump()` / `vars()` fallback,
restoring correct verification results across all three systems.

== Validation Methodology

A goal is counted as proved only when Isabelle fully accepts the proof with no
`sorry` placeholders.
Systems A and B use a two-phase protocol: (1) Sledgehammer or the LLM proposes
a `by <tactic>` finisher; (2) the finisher is re-submitted to Isabelle in a
fresh theory and the `FINISHED` response is inspected for `ok = true` via
`finished_ok`.
A goal is marked proved only if Phase~2 passes.
Note: the Pydantic bug described above caused Phase~2 to always fail before the
fix, producing the spurious 0\% results for System~B that were superseded after patching.

*Failure classification.*
Failed goals are categorised by elapsed time:
_Fast fail_ (#sym.lt 5 s): Isabelle rejected the goal before any ATP call,
typically due to a type error or ambiguous identifier.
_ATP-exhausted_ (5–90 s): Sledgehammer ran its full internal budget and returned
no suggestions; the wall-clock timeout was not reached.
_Wall-clock timeout_ (#sym.gt 90 s): the per-goal budget was consumed.

Nitpick @blanchette2010nitpick and Quickcheck are run on all failures to classify
them as genuine theorems (no counterexample found) or non-theorems (counterexample
found within the tool's budget).

== Datasets

=== Repository Datasets

@tbl-repo-datasets lists the 13 goal files shipped with the Isabellm
repository @isabellm2026repo, totalling 32,368 goals.
Our evaluation uses the four hand-crafted datasets (40 goals) and the three
HOL main test splits (300 goals) as primary benchmarks.

#figure(
  table(
    columns: (auto, auto, auto),
    align: (left, center, left),
    [*Dataset*], [*Goals*], [*Category*],
    [`lists.txt`],                    [18],     [Hand-crafted],
    [`logic.txt`],                    [5],      [Hand-crafted],
    [`nat.txt`],                      [9],      [Hand-crafted],
    [`sets.txt`],                     [8],      [Hand-crafted],
    [`hol_main_easy_goals_test.txt`], [100],    [HOL main — easy test],
    [`hol_main_mid_goals_test.txt`],  [100],    [HOL main — mid test],
    [`hol_main_hard_goals_test.txt`], [100],    [HOL main — hard test],
    [`mini_f2f_test.txt`],            [244],    [mini-F2F @zheng2022minif2f],
    [`putnambench_goals.txt`],        [640],    [PutnamBench @tsoukalas2024putnambench],
    [_Training splits (3)_],          [_30,900_], [HOL main — training],
  ),
  caption: [Goal files provided with the Isabellm repository. Training splits are excluded
  from evaluation; PutnamBench requires HOL-Analysis and is beyond the three systems' reach.]
) <tbl-repo-datasets>

=== Dataset Justification

Our benchmark suite satisfies the spec's requirement for a balanced mix of easy
and hard formulae across diverse domains (@tbl-eval-datasets).

#figure(
  table(
    columns: (auto, auto, auto, auto),
    align: (left, center, left, left),
    [*Dataset*], [*Goals*], [*Difficulty*], [*Justification*],
    [lists/nat/sets/logic],   [40],  [Easy–Med],   [Human-readable goals covering list manipulation, arithmetic, propositional logic. Sanity check for both systems.],
    [HOL main easy test],     [100], [Easy],       [Auto-generated HOL goals; held-out split ensures no goal was used in reranker training.],
    [HOL main mid test],      [100], [Medium],     [Goals with quantifiers and simple induction; tests whether Isar planning adds value over ATP alone.],
    [HOL main hard test],     [100], [Hard],       [Complex induction, case analysis, higher-order properties; stresses the CEGIS repair loop.],
    [mini-F2F test],          [244], [Hard],       [Olympiad-level maths from AIME/AMC/IMO in Isabelle/HOL; external benchmark used by multiple published systems.],
  ),
  caption: [Evaluation datasets and justification.]
) <tbl-eval-datasets>

== Experimental Setup and Results

*Setup.*
OS: Windows 11; CPU: Intel Core Ultra 7 258V; GPU: Intel Arc 140V (used by Ollama for inference); Isabelle: 2025-2; LLM: Qwen2.5-Coder 7B (Ollama local inference); timeout: 90 s per goal (System~A), 60–120 s (Systems B and C).

*Ablation design.*
@tbl-ablation shows the three systems compared.
System~A adds no LLM component; B adds LLM tactics; C adds Isar planning and
CEGIS repair on top of B.
Comparing A → B isolates the value of LLM-guided tactics;
comparing B → C isolates the value of structured proof planning and repair.

#figure(
  table(
    columns: (auto, auto, auto, auto),
    align: (left, center, center, center),
    [*System*], [*Sledgehammer*], [*LLM tactics*], [*Isar planning + CEGIS*],
    [A — Sledgehammer-only], [#sym.checkmark], [#sym.times], [#sym.times],
    [B — LLM stepwise prover], [fallback], [#sym.checkmark], [#sym.times],
    [C — LLM planner (ours)], [fallback], [#sym.checkmark], [#sym.checkmark],
  ),
  caption: [Ablation design. Each system adds exactly one component over the previous.]
) <tbl-ablation>

*Results.*

#figure(
  table(
    columns: (auto, auto, auto, auto, auto),
    align: (left, center, center, center, center),
    [*Dataset (goals)*], [*A: Sledge*], [*B: Prover*], [*C: Planner*], [*Note*],
    [lists/nat/sets/logic (40)], [85.0%], [—], [—], [nat.txt 44.4% limits A],
    [HOL main easy test (100)],  [96.0%], [—], [—], [2 non-theorems in failures],
    [HOL main mid test (100)],   [74.0%], [—], [—], [4 non-theorems in failures],
    [HOL main hard test (100)],  [80.0%], [—], [—], [6 non-theorems in failures],
    [logic.txt (5, smoke)],      [100.0%],[100.0%], [—], [B result post Pydantic fix],
  ),
  caption: [Success rates (goals proved). A = Sledgehammer-only, 20 s Sledgehammer / 90 s
  wall-clock. B = LLM stepwise prover, qwen2.5-coder:7b, beam=3, 60 s, Sledgehammer enabled.
  C = full LLM planner with fill and CEGIS repair. System B full-suite and System C
  benchmarks pending completion; see Discussion.]
) <tbl-results>

#figure(
  table(
    columns: (auto, auto, auto),
    align: (left, center, center),
    [*Dataset*], [*A median (s)*], [*B median (s)*],
    [lists/nat/sets/logic], [17.0], [—],
    [HOL main easy test],   [19.0], [—],
    [HOL main mid test],    [17.4], [—],
    [HOL main hard test],   [20.1], [—],
    [logic.txt (smoke)],    [~17],  [10.88],
  ),
  caption: [Median wall-clock solve time per goal (seconds). System A includes Isabelle
  server startup and theory compilation overhead per goal. System B logic median reflects
  Sledgehammer running inside a persistent session, avoiding repeated startup cost.]
)

*Failure analysis.*
@tbl-failures summarises all goals that failed System~A across the three test splits,
with Nitpick/Quickcheck verdicts.

#figure(
  table(
    columns: (auto, auto, auto),
    align: (left, center, left),
    [*Dataset*], [*Failures*], [*Breakdown*],
    [easy test (100)],  [4],  [2 non-theorems; 2 theorem — ATP-exhausted (induction required)],
    [mid test (100)],   [26], [4 non-theorems; 20 theorems ATP-exhausted; 2 fast-fail (parse/type)],
    [hard test (100)],  [20], [6 non-theorems; 12 theorems ATP-exhausted; 2 fast-fail (SIGMA import)],
  ),
  caption: [System~A failure breakdown by dataset. Corrected theorem-only success rates
  (excluding non-theorems): easy 96/98 = 98.0\%; mid 74/96 = 77.1\%; hard 80/94 = 85.1\%.]
) <tbl-failures>

The 12 non-theorems identified across all datasets are listed in @tbl-nontheorems.

#figure(
  table(
    columns: (auto, auto),
    align: (left, left),
    [*Goal (abbreviated)*], [*Why false*],
    [`map f (filter p xs) = filter (λx. p x) (map f xs)`],
      [Non-injective f: swap two elements, filter selects one; order matters.],
    [`take n xs = take n (xs @ ys) ⟷ n ≤ length xs`],
      [n=1, xs=\[\], ys=\[\]: take 1 \[\] = \[\] but ¬(1≤0).],
    [`(f \` A) ∩ (f \` B) ⊆ f \` (A ∩ B)`],
      [Fails for non-injective f (constant function).],
    [`Range (r O s) = Range r ∩ (r \`\` (Range s))`],
      [Nitpick counterexample for card 'a = 3.],
    [`(a::int) ≤ b ⟶ a * c ≤ b * c`],
      [False for negative c (a=1, b=2, c=−1).],
    [`(a::int) ≤ b ⟶ a mod c ≤ b mod c ∨ c=0`],
      [Nitpick and Quickcheck both find counterexamples.],
    [`(∃x. P x ∧ (Q x → R x)) → ((∃x. P x ∧ Q x) → (∃x. R x))`],
      [Witnesses may differ; Nitpick card'a=6.],
    [`f \` (A − B) ⊆ (f \` A) − (f \` (A ∩ B))`],
      [Non-injective f: f(a)=f(b), a∈A−B, b∈A∩B.],
    [`rev (take n xs) = drop (|xs|−n) (rev xs) ⟷ n ≤ |xs|`],
      [Equality holds for n>|xs| via nat subtraction; iff too strong.],
    [`rev (drop n xs) = take (|xs|−n) (rev xs) ⟷ n ≤ |xs|`],
      [Same nat subtraction issue.],
    [`foldr f b (xs @ ys) = foldr f (foldr f b ys) xs`],
      [Type error: b in list position; Nitpick confirms.],
    [`length [m..<n] + length [n..<p] = length [m..<p]`],
      [False when m>n: e.g., m=2, n=1, p=3 gives 0+2≠1.],
  ),
  caption: [All 12 non-theorems identified by Nitpick and Quickcheck across all benchmark datasets.
  Sledgehammer correctly returned no proof for each goal.]
) <tbl-nontheorems>

== Discussion

*Where Sledgehammer excels.*
System~A achieves 100\% on `logic.txt` and `sets.txt` (propositional and set-theoretic
goals with no induction) and 96.0\% on the HOL main easy test.
These goals have shallow structure that E, Vampire, Z3, and Isabelle's built-in
`simp`/`auto`/`blast` discharge without inductive arguments.
The median solve time is 17--20~s across all datasets, reflecting Isabelle server
startup and theory compilation overhead rather than ATP search time
(Sledgehammer typically returns a proof within 5~s once the server is warm).

*Where Sledgehammer fails.*
System~A achieves only 44.4\% on `nat.txt` (4/9 goals), where commutativity and
associativity of natural number addition require inductive arguments that first-order
ATPs cannot synthesise.
Similarly, 20 failures in the mid test set involve arithmetic reasoning over natural
number subtraction (truncated in HOL), integer `abs`/`min`/`max`, and relational
algebra — areas where ATP translations lose structural information.
The non-monotonic result (hard 80\% > mid 74\%) is explained by the hard corpus
containing a higher proportion of higher-order goals (relational algebra, function
composition, image/preimage) that Sledgehammer's ATPs handle well, while the mid
corpus has more arithmetic goals that evade ATP translation.

*System~B — LLM stepwise prover.*
Early benchmarks reported 0/5 (0.0\%) on `logic.txt` and 0/20 (0.0\%) on the
first 20 HOL easy goals.
Post-investigation, this was entirely due to the Pydantic v2 deserialization bug
described in Section~4.1: every Isabelle verification response was silently read
as a failure.
After patching `_decode_body_to_dict`, System~B with Sledgehammer enabled (beam=3,
60~s wall-clock) achieves *5/5 (100.0\%)* on `logic.txt` with a median of 10.88~s —
faster than System~A because Sledgehammer runs inside a warm persistent session.
Without Sledgehammer, qwen2.5-coder:7b frequently generates complete Isabelle
theory blocks rather than individual `apply` tactics, which Isabelle rejects in
proof state mode.
The full-suite System~B benchmarks (nat, lists, sets) are pending; we expect results
comparable to System~A on easy goals since Sledgehammer provides the primary signal.

*System~C — LLM planner.*
System~C is verified correct on individual unit tests: a two-hole induction outline
for `map f (xs @ ys) = map f xs @ map f ys` was filled via Sledgehammer's `by simp`
(the fast path collapsed the induction to a direct one-liner after verifying it with
Isabelle), and the single-have outline for `length (xs @ ys) = length xs + length ys`
was filled with `by simp` and verified.
Full benchmark results for System~C across the hand-crafted and HOL main datasets
are pending due to the time required for per-goal outline generation and repair
cycles on the available hardware.
We anticipate System~C will outperform System~A on goals in `nat.txt` where
induction is required, since the planner explicitly generates induction skeletons
that Sledgehammer-only cannot produce.

*Non-theorem finding.*
Nitpick and Quickcheck analysis of all failures revealed 12 non-theorems embedded
across the benchmark datasets (3.0\% of 400 goals tested), covering all three
difficulty levels.
This underscores the importance of two-phase Isabelle verification: a system
that trusts ATP output without re-verification would misreport these correctly-rejected
goals as prover failures, inflating apparent difficulty.
It also suggests that automatically generated theorem corpora contain a non-trivial
proportion of malformed or false statements @blanchette2010nitpick.

// ============================================================
// 5. CONCLUSION
// ============================================================

= Conclusion

We have presented and evaluated an LLM-guided theorem prover for Isabelle/HOL
that integrates structured Isar proof planning, stepwise tactic search with
premise retrieval and ML reranking, and a CEGIS-style staged repair loop.

System~A (Sledgehammer-only baseline) achieves 85.0\% on 40 hand-crafted goals,
96.0\% on the HOL main easy test set, 74.0\% on the mid test set, and 80.0\%
on the hard test set, with median solve times of 17--20~s.
The non-monotonic hard result reflects the composition of the hard corpus
rather than any prover improvement.
Nitpick and Quickcheck analysis revealed 12 non-theorems across all datasets,
giving corrected theorem-only rates of 98.0\% (easy), 77.1\% (mid), and 85.1\% (hard).
System~B (LLM stepwise prover), after correcting the Pydantic v2 deserialization
bug, achieves 100\% on logic goals matching System~A.

The most significant ideas learned from this project are as follows.
First, the correctness of the system integration layer is as important as the
quality of the LLM or ATP component: the Pydantic bug masked all verified proofs
and made every component appear broken, an effect that was entirely invisible from
the output side until the API layer was inspected.
Second, proof structure and tactic synthesis are best treated as separate concerns:
separating outline generation from hole filling allows each component to be
independently improved and tested, and the hole filler benefits strongly from
Isabelle's own proof state feedback rather than relying on the LLM to predict
the state.
Third, Sledgehammer is highly effective for the majority of "routine" goals
(propositional logic, set theory, simple higher-order properties) but
fundamentally limited to goals expressible as first-order ATP problems;
goals requiring inductive arguments expose a qualitative boundary that requires
the Isar planner to cross.
Fourth, a 7B model is sufficient to drive Sledgehammer-backed proof search
effectively in beam mode but is inadequate for LLM-only tactic generation,
which requires the 30B+ model the system was designed around.

Future work includes completing the full System~C benchmark, integrating the
ML reranker with a purpose-trained model, and extending the CEGIS repair loop
with a counterexample-guided backtracking strategy for non-theorem detection.

// ============================================================
// DATA AVAILABILITY
// ============================================================

= Data Availability

The source code for this work is available at: https://github.com/LucaRoescheisen/group_15

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
All report text was written by the authors; AI tools were used solely for
coding assistance, debugging, and code review as described below.

*Tool:* Claude Code (claude-sonnet-4-5, Anthropic), accessed via the Claude Code CLI.

*Query 1:* Debugging the `isabelle_client` Pydantic v2 deserialization issue —
"The `finished_ok` function always returns `(False, {})` even when Isabelle
accepts the proof. Can you identify why the response body is not being decoded
correctly?"

*Response summary:* Claude identified that the `use_theories` response body was
stored as a Pydantic `UseTheoriesResults` model object rather than a plain `dict`,
causing all dict-key lookups to silently fail. The fix was to add
`.model_dump()` / `vars()` fallback calls in `_decode_body_to_dict` inside
`isabelle_api.py`, restoring correct proof verification across all three systems.

---

*Query 2:* Diagnosing and fixing the budget-starvation and indentation bugs in
`_fill_one_hole` — "The hole filler always reports 'Fill made no progress' even
on trivial goals. The sledgehammer timeout is 10 s but the total per-hole budget
is only 18 s, and the inserted tactic has wrong indentation."

*Response summary:* Claude identified two distinct bugs: (1) `sledge_timeout` was
fixed at 10 s regardless of `per_hole_timeout`, leaving zero time for the
finisher-verification loop; fixed by capping `sledge_timeout` at
`per_hole_timeout // 3`. (2) The generic finisher insertion used a hardcoded
`"\n  "` prefix instead of preserving the whitespace already before `sorry`; fixed
by extracting `indent = full_text[line_start:s]` and using it in the replacement.

---

*Query 3:* Merging teammate contributions — reviewing `driver.py` changes from the
remote `test` branch and resolving merge conflicts.

*Response summary:* Claude identified and described Luca Roescheisen's additions
to `driver.py` (fast-path on `res.get("success")`, `_fill_one_hole_finisher_only`,
and apply-inside-have/show retry logic), confirmed no semantic conflicts with
Sam Li's fixes in `isabelle_api.py`, `goals.py`, and `skeleton.py`, and guided
the `git merge` workflow, resolving only runtime log file conflicts by discarding
the stashed log changes.

---

*Query 4:* Writing and debugging `test_multiholes.py` — "Test the multi-hole
filling pipeline directly without LLM outline generation, using manually
constructed induction outlines."

*Response summary:* Claude identified a stale-span bug in the test loop
(Python evaluates the `find_sorry_spans(full)` iterator once at loop entry, so
span positions become stale after the first hole is filled) and rewrote both
test loops to use a `while True: spans = find_sorry_spans(full)` pattern that
recomputes positions after each fill. Both test cases (2-hole `map f` induction
and single-have `length` proof) passed after the fix.
