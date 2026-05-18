# benchmarks/

Standalone benchmark runners for Systems B and C.
**No original repo files are modified.** These scripts import from the repo
but live outside it so the baseline comparison is clean.

## Prerequisites

1. Ollama installed and running: https://ollama.com
2. A model pulled: `ollama pull qwen2.5-coder:7b`
3. Isabelle 2025-2 installed and on your PATH
4. Python venv activated (see repo README for setup)

## System B — LLM stepwise prover

### Windows (PowerShell)
```powershell
$env:ISABELLE_INST_DIR = "C:\Program Files\Isabelle2025-2"
$env:PYTHONUTF8        = "1"
$env:OLLAMA_MODEL      = "qwen2.5-coder:7b"

# Smoke test
& .venv\Scripts\python.exe benchmarks/run_system_b.py `
    --file datasets/logic.txt `
    --timeout 60 --sledge --sledge-timeout 20

# Full benchmark
& .venv\Scripts\python.exe benchmarks/run_system_b.py `
    --file datasets/hol_main_easy_goals_test.txt `
    --timeout 120 --beam 4 --max-depth 8 `
    --sledge --sledge-timeout 20
```

### Linux / macOS (bash)
```bash
# Ensure isabelle is on your PATH, e.g.:
# export PATH=$PATH:/path/to/Isabelle2025-2/bin

OLLAMA_MODEL=qwen2.5-coder:7b \
python benchmarks/run_system_b.py \
    --file datasets/logic.txt \
    --timeout 60 --sledge --sledge-timeout 20

# Full benchmark
OLLAMA_MODEL=qwen2.5-coder:7b \
python benchmarks/run_system_b.py \
    --file datasets/hol_main_easy_goals_test.txt \
    --timeout 120 --beam 4 --max-depth 8 \
    --sledge --sledge-timeout 20
```

Results are saved to `benchmarks/results/system_b_<dataset>.json`.

## System C — LLM planner + CEGIS (coming soon)

`run_system_c.py` will follow the same pattern using `planner.driver.plan_and_fill`.
