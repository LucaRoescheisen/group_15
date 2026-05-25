#!/usr/bin/env bash
# run_all.sh — sequential benchmark queue with timing.
# Each command runs to completion before the next starts.
# Per-task timing + final total-wall summary written to logs/queue_summary.tsv.
# Failures are logged but DO NOT stop the queue.

set -u
mkdir -p logs

# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------
SCRIPT_START=$(date +%s)
SUMMARY_FILE="logs/queue_summary.tsv"
# Header on the summary TSV (overwrites any previous run's summary).
printf 'name\tstart_iso\tend_iso\tseconds\thuman\trc\n' > "$SUMMARY_FILE"

# Convert seconds → "HhMmSs" / "MmSs" / "Ss" for human display.
fmt_hms() {
  local s=$1
  if (( s >= 3600 )); then
    printf '%dh%02dm%02ds' $((s/3600)) $(((s%3600)/60)) $((s%60))
  elif (( s >= 60 )); then
    printf '%dm%02ds' $((s/60)) $((s%60))
  else
    printf '%ds' "$s"
  fi
}

# Helper: run a command, time it, log success/failure to a summary.
run() {
  local name="$1"; shift
  local start_iso end_iso t0 t1 elapsed rc human
  start_iso=$(date '+%F %T')
  t0=$(date +%s)

  echo
  echo "==================== START: $name ===================="
  echo "[$start_iso] starting $name"

  # Run the command (any failure is captured, not propagated).
  "$@"
  rc=$?

  t1=$(date +%s)
  end_iso=$(date '+%F %T')
  elapsed=$((t1 - t0))
  human=$(fmt_hms "$elapsed")

  echo "[$end_iso] finished $name (rc=$rc, elapsed=${human})"
  echo "==================== END:   $name ===================="

  printf '%s\t%s\t%s\t%d\t%s\t%d\n' \
    "$name" "$start_iso" "$end_iso" "$elapsed" "$human" "$rc" \
    >> "$SUMMARY_FILE"
}

# ----- Tier 1: Sledge baselines -----
#run "t1_sledge_lists" bash -c '
#  python baselines/sledge_only.py --file datasets/lists.txt --imports Main \
#    --provers "e z3 vampire cvc5" --sledge-timeout 30 --goal-timeout 60 --print-logs \
#    2>&1 | tee logs/t1_sledge_lists.log
#'
#
#run "t1_sledge_nat" bash -c '
#  python baselines/sledge_only.py --file datasets/nat.txt --imports Main \
#    --provers "e z3 vampire cvc5" --sledge-timeout 30 --goal-timeout 60 --print-logs \
#    2>&1 | tee logs/t1_sledge_nat.log
#'
#
#run "t1_sledge_sets" bash -c '
#  python baselines/sledge_only.py --file datasets/sets.txt --imports Main \
#    --provers "e z3 vampire cvc5" --sledge-timeout 30 --goal-timeout 60 --print-logs \
#    2>&1 | tee logs/t1_sledge_sets.log
#'
#
#run "t1_sledge_logic" bash -c '
#  python baselines/sledge_only.py --file datasets/logic.txt --imports Main \
#    --provers "e z3 vampire cvc5" --sledge-timeout 30 --goal-timeout 60 --print-logs \
#    2>&1 | tee logs/t1_sledge_logic.log
#'
#
#run "t1_sledge_easy_test" bash -c '
#  python baselines/sledge_only.py --file datasets/hol_main_easy_goals_test.txt --imports Main \
#    --provers "e z3 vampire cvc5" --sledge-timeout 30 --goal-timeout 60 --print-logs \
#    2>&1 | tee logs/t1_sledge_easy_test.log
#'
#
#run "t1_sledge_mid_test" bash -c '
#  python baselines/sledge_only.py --file datasets/hol_main_mid_goals_test.txt --imports Main \
#    --provers "e z3 vampire cvc5" --sledge-timeout 30 --goal-timeout 60 --print-logs \
#    2>&1 | tee logs/t1_sledge_mid_test.log
#'
#
#run "t1_sledge_hard_test" bash -c '
#  python baselines/sledge_only.py --file datasets/hol_main_hard_goals_test.txt --imports Main \
#    --provers "e z3 vampire cvc5" --sledge-timeout 30 --goal-timeout 60 --print-logs \
#    2>&1 | tee logs/t1_sledge_hard_test.log
#'
#
## ----- Tier 1: Prover -----
#PROVER_FLAGS="--beam 3 --max-depth 6 --facts-limit 6 --sledge --reranker on --no-minimize \
#              --model gemini:gemini-3-flash-preview --shuffle --seed 42"
#
#run "t1_prover_lists" bash -c "
#  python -u -m prover.experiments bench --file datasets/lists.txt --timeout 60 $PROVER_FLAGS \
#    2>&1 | tee logs/t1_prover_lists.log
#"
#
#run "t1_prover_nat" bash -c "
#  python -u -m prover.experiments bench --file datasets/nat.txt --timeout 60 $PROVER_FLAGS \
#    2>&1 | tee logs/t1_prover_nat.log
#"
#
#run "t1_prover_sets" bash -c "
#  python -u -m prover.experiments bench --file datasets/sets.txt --timeout 60 $PROVER_FLAGS \
#    2>&1 | tee logs/t1_prover_sets.log
#"
#
#run "t1_prover_logic" bash -c "
#  python -u -m prover.experiments bench --file datasets/logic.txt --timeout 60 $PROVER_FLAGS \
#    2>&1 | tee logs/t1_prover_logic.log
#"
#
#run "t1_prover_easy_test" bash -c "
#  python -u -m prover.experiments bench --file datasets/hol_main_easy_goals_test.txt --timeout 90 $PROVER_FLAGS \
#    2>&1 | tee logs/t1_prover_easy_test.log
#"
#
#run "t1_prover_mid_test" bash -c "
#  python -u -m prover.experiments bench --file datasets/hol_main_mid_goals_test.txt --timeout 120 $PROVER_FLAGS \
#    2>&1 | tee logs/t1_prover_mid_test.log
#"

#run "t1_prover_hard_test" bash -c "
#  python -u -m prover.experiments bench --file datasets/hol_main_hard_goals_test.txt --timeout 200 \
#   --beam 3 --max-depth 8 --facts-limit 8 --sledge --reranker on --no-minimize \
#    --model gemini:gemini-3-flash-preview --shuffle --seed 42 \
##   2>&1 | tee logs/t1_prover_hard_test.log
#"

# ----- Tier 1: Planner -----
PLAN_FLAGS='--mode auto --diverse --k 3 --temps "0.35,0.55,0.85" --strict-no-sorry --verify \
            --model qwen3:8b --shuffle --seed 42 --trace'

#run "t1_planner_lists" bash -c "
#  python -u -m planner.experiments bench --file datasets/lists.txt --timeout 60 $PLAN_FLAGS \
#    2>&1 | tee logs/t1_planner_lists.log
#"
#
#run "t1_planner_nat" bash -c "
#  python -u -m planner.experiments bench --file datasets/nat.txt --timeout 60 $PLAN_FLAGS \
#    2>&1 | tee logs/t1_planner_nat.log
#"
#
#run "t1_planner_sets" bash -c "
#  python -u -m planner.experiments bench --file datasets/sets.txt --timeout 60 $PLAN_FLAGS \
#    2>&1 | tee logs/t1_planner_sets.log
#"
#
#run "t1_planner_logic" bash -c "
#  python -u -m planner.experiments bench --file datasets/logic.txt --timeout 60 $PLAN_FLAGS \
#    2>&1 | tee logs/t1_planner_logic.log
#"

#run "t1_planner_easy_test" bash -c "
#  python -u -m planner.experiments bench --file datasets/hol_main_easy_goals_test.txt --timeout 90 $PLAN_FLAGS \
#    2>&1 | tee logs/t1_planner_easy_test.log
#"

run "t1_planner_mid_test" bash -c "
  python -u -m planner.experiments bench --file datasets/hol_main_mid_goals_test.txt --timeout 120 $PLAN_FLAGS \
    2>&1 | tee logs/t1_planner_mid_test.log
"

run "t1_planner_hard_test" bash -c "
  python -u -m planner.experiments bench --file datasets/hol_main_hard_goals_test.txt --timeout 200 $PLAN_FLAGS \
    2>&1 | tee logs/t1_planner_hard_test.log
"

# ----- Tier 2: Ablation on mid_test -----
run "t2_no_repairs" bash -c "
  python -u -m planner.experiments bench --file datasets/hol_main_mid_goals_test.txt --timeout 120 \
    --mode auto --diverse --k 3 --temps '0.35,0.55,0.85' --strict-no-sorry --verify --no-repairs \
    --model gemini:gemini-3-flash-preview --shuffle --seed 42 --trace \
    2>&1 | tee logs/t2_no_repairs.log
"

run "t2_no_preprocess" bash -c "
  ABLATE_PREPROCESSING=1 python -u -m planner.experiments bench --file datasets/hol_main_mid_goals_test.txt --timeout 120 \
    --mode auto --diverse --k 3 --temps '0.35,0.55,0.85' --strict-no-sorry --verify \
    --model gemini:gemini-3-flash-preview --shuffle --seed 42 --trace \
    2>&1 | tee logs/t2_no_preprocess.log
"

run "t2_no_sledge" bash -c "
  python -u -m prover.experiments bench --file datasets/hol_main_mid_goals_test.txt --timeout 120 \
    --beam 3 --max-depth 6 --facts-limit 6 --reranker on --no-minimize \
    --model gemini:gemini-3-flash-preview --shuffle --seed 42 \
    2>&1 | tee logs/t2_no_sledge.log
"

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------
SCRIPT_END=$(date +%s)
TOTAL_S=$((SCRIPT_END - SCRIPT_START))
TOTAL_HMS=$(fmt_hms "$TOTAL_S")

echo
echo "==================== ALL DONE ===================="
echo "Total wall time: ${TOTAL_HMS} (${TOTAL_S}s)"
echo
echo "Per-task summary:"
if command -v column >/dev/null 2>&1; then
  column -t -s $'\t' "$SUMMARY_FILE"
else
  cat "$SUMMARY_FILE"
fi

# Also append a TOTAL row to the summary file for easy parsing later.
printf 'TOTAL\t-\t-\t%d\t%s\t-\n' "$TOTAL_S" "$TOTAL_HMS" >> "$SUMMARY_FILE"
