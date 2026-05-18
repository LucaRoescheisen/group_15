#!/usr/bin/env python3
"""
diagnose.py — Run this first if anything fails on your machine.

Usage:
    python diagnose.py
    python diagnose.py --model qwen3:8b   # test a specific model name
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

RESET  = "\033[0m"
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
BOLD   = "\033[1m"

def ok(msg):   print(f"  {GREEN}[OK]{RESET}  {msg}")
def fail(msg): print(f"  {RED}[FAIL]{RESET} {msg}")
def warn(msg): print(f"  {YELLOW}[WARN]{RESET} {msg}")
def section(title): print(f"\n{BOLD}{title}{RESET}")


def check_python():
    section("Python version")
    v = sys.version_info
    msg = f"Python {v.major}.{v.minor}.{v.micro}"
    if v.major == 3 and 10 <= v.minor <= 12:
        ok(msg)
    elif v.major == 3 and v.minor == 13:
        warn(f"{msg} — PyTorch may not install correctly on 3.13; prefer 3.10–3.12")
    else:
        fail(f"{msg} — need Python 3.10–3.12")


def check_isabelle():
    section("Isabelle")
    path = shutil.which("isabelle")
    if path:
        ok(f"isabelle found at: {path}")
        try:
            r = subprocess.run([path, "version"], capture_output=True, text=True, timeout=10)
            ver = (r.stdout + r.stderr).strip().splitlines()[0]
            ok(f"version: {ver}")
        except Exception as e:
            warn(f"Could not run 'isabelle version': {e}")
    else:
        fail("isabelle not found on PATH")
        print("     → On Linux: add Isabelle's bin/ directory to your PATH, e.g.:")
        print("         export PATH=$PATH:/opt/Isabelle2025-2/bin")
        print("       On macOS: same, or use the .app bundle's embedded isabelle binary")


def check_packages():
    section("Python packages")
    required = [
        ("requests",       "requests"),
        ("isabelle_client", "isabelle_client"),
        ("joblib",         "joblib"),
        ("tabulate",       "tabulate"),
    ]
    optional = [
        ("sklearn",  "scikit-learn", "needed for reranker; non-fatal if missing"),
        ("xgboost",  "xgboost",      "needed for reranker; non-fatal if missing"),
        ("torch",    "torch",        "needed for deep reranker; non-fatal if missing"),
    ]
    for mod, pkg, *_ in required:
        try:
            __import__(mod)
            ok(pkg)
        except ImportError:
            fail(f"{pkg} — run: pip install {pkg}")

    for mod, pkg, note in optional:
        try:
            __import__(mod)
            ok(f"{pkg} (optional)")
        except ImportError:
            warn(f"{pkg} not installed ({note})")
            if mod == "torch":
                print("       → CPU-only install: pip install torch --index-url https://download.pytorch.org/whl/cpu")


def check_ollama(model: str):
    section("Ollama")
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        r.raise_for_status()
        data = r.json()
        models = [m["name"] for m in data.get("models", [])]
        ok(f"Ollama is running ({len(models)} model(s) available)")
        if model in models:
            ok(f"Model '{model}' is pulled and ready")
        else:
            fail(f"Model '{model}' not found in Ollama")
            print(f"     → Run: ollama pull {model}")
            if models:
                print(f"     → Available models: {', '.join(models[:5])}")
    except Exception as e:
        fail(f"Cannot reach Ollama at http://localhost:11434 — {e}")
        print("     → Start Ollama: ollama serve")


def check_repo_imports():
    section("Repo imports")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    pairs = [
        ("prover.config",      "prover/config.py"),
        ("prover.isabelle_api","prover/isabelle_api.py"),
        ("prover.prover",      "prover/prover.py"),
        ("planner.skeleton",   "planner/skeleton.py"),
        ("planner.repair",     "planner/repair.py"),
        ("planner.driver",     "planner/driver.py"),
        ("planner.cli",        "planner/cli.py"),
    ]
    for mod, path in pairs:
        try:
            __import__(mod)
            ok(f"{path}")
        except Exception as e:
            fail(f"{path} — {type(e).__name__}: {e}")


def check_isabelle_server():
    section("Isabelle server startup")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from isabelle_client import start_isabelle_server, get_isabelle_client
        print("  Starting Isabelle server (may take 30–90 s the first time)…", flush=True)
        info, proc = start_isabelle_server(name="diagnose", log_file="logs/diagnose.log")
        ok(f"Server started: {info.strip()}")
        isa = get_isabelle_client(info)
        ok("Client connected")
        try:
            proc.terminate()
        except Exception:
            pass
    except Exception as e:
        fail(f"Could not start Isabelle server — {type(e).__name__}: {e}")
        if "not found" in str(e).lower() or "isabelle" in str(e).lower():
            print("     → Make sure 'isabelle' is on your PATH")
        elif "Unexpected server info" in str(e):
            print("     → Isabelle started but returned no server info.")
            print("       On Windows, set ISABELLE_INST_DIR to your Isabelle install path.")
            print("       On Linux/macOS, ensure 'isabelle' is the correct binary.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen2.5-coder:7b",
                        help="Ollama model name to check (default: qwen2.5-coder:7b)")
    parser.add_argument("--skip-server", action="store_true",
                        help="Skip the Isabelle server startup test (faster)")
    args = parser.parse_args()

    print(f"{BOLD}=== Isabellm diagnostic ==={RESET}")
    print(f"Platform: {sys.platform}  |  Checking model: {args.model}")

    check_python()
    check_isabelle()
    check_packages()
    check_ollama(args.model)
    check_repo_imports()
    if not args.skip_server:
        check_isabelle_server()

    print(f"\n{BOLD}Done.{RESET}  Fix any [FAIL] items above and re-run to confirm.\n")


if __name__ == "__main__":
    main()
