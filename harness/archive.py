#!/usr/bin/env python3
"""Archive a completed experiment run with all inputs and outputs."""

import shutil
import sys
from pathlib import Path

HARNESS_DIR = Path(__file__).parent
RUNS_DIR = HARNESS_DIR / "runs"


def archive(name: str):
    dest = RUNS_DIR / name
    if dest.exists():
        print(f"Archive '{name}' already exists at {dest}")
        sys.exit(1)

    # Check there are results to archive
    results_dir = HARNESS_DIR / "results"
    result_files = list(results_dir.glob("*.json"))
    if not result_files:
        print("No result files to archive.")
        sys.exit(1)

    dest.mkdir(parents=True)

    # Copy inputs
    shutil.copytree(HARNESS_DIR / "prompts", dest / "prompts")
    shutil.copytree(HARNESS_DIR / "data", dest / "data")

    # Copy outputs
    shutil.copytree(HARNESS_DIR / "results", dest / "results")
    logs_dir = HARNESS_DIR / "logs"
    if logs_dir.exists() and any(logs_dir.iterdir()):
        shutil.copytree(logs_dir, dest / "logs")

    # Summary
    n_results = len(list((dest / "results").glob("*.json")))
    n_logs = len(list((dest / "logs").glob("*.jsonl"))) if (dest / "logs").exists() else 0
    print(f"Archived to {dest}")
    print(f"  prompts/  — system prompts used")
    print(f"  data/     — questions")
    print(f"  results/  — {n_results} result files")
    print(f"  logs/     — {n_logs} conversation logs")
    print()
    print(f"Next steps:")
    print(f"  python3 harness/runs/score.py {name}")
    print(f"  python3 harness/runs/trace.py {name}")

    # Clear working directories for next run
    print()
    answer = input("Clear results/ and logs/ for next run? [y/N] ")
    if answer.lower() == "y":
        shutil.rmtree(HARNESS_DIR / "results")
        (HARNESS_DIR / "results").mkdir()
        if logs_dir.exists():
            shutil.rmtree(logs_dir)
            logs_dir.mkdir()
        print("Cleared.")
    else:
        print("Kept. Remove manually or the runner will skip existing results.")


def main():
    if len(sys.argv) < 2:
        print("Usage: archive.py <run-name>")
        print()
        print("Examples:")
        print("  archive.py run-0-first")
        print("  archive.py run-1-tuned-prompt")
        print()
        # List existing archives
        if RUNS_DIR.exists():
            existing = sorted(p.name for p in RUNS_DIR.iterdir() if p.is_dir())
            if existing:
                print("Existing archives:")
                for name in existing:
                    print(f"  {name}")
        sys.exit(1)

    archive(sys.argv[1])


if __name__ == "__main__":
    main()
