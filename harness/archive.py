#!/usr/bin/env python3
"""Archive a completed experiment run with all inputs and outputs."""

import shutil
import sys
from pathlib import Path

HARNESS_DIR = Path(__file__).parent
DEFAULT_BENCH = HARNESS_DIR.parent / "bench"


def archive(name: str, bench_dir: Path):
    runs_dir = bench_dir / "runs"
    dest = runs_dir / name
    if dest.exists():
        print(f"Archive '{name}' already exists at {dest}")
        sys.exit(1)

    # Check there are results to archive
    results_dir = bench_dir / "results"
    result_files = list(results_dir.rglob("*.json"))
    if not result_files:
        print("No result files to archive.")
        sys.exit(1)

    dest.mkdir(parents=True)

    # Copy inputs
    prompts_dir = bench_dir / "prompts"
    if prompts_dir.exists():
        shutil.copytree(prompts_dir, dest / "prompts")

    # Copy questions as data/questions.json (for self-contained scoring)
    questions_src = bench_dir / "questions.json"
    if questions_src.exists():
        (dest / "data").mkdir()
        shutil.copy2(questions_src, dest / "data" / "questions.json")

    # Copy conditions config
    conditions_src = bench_dir / "conditions.json"
    if conditions_src.exists():
        shutil.copy2(conditions_src, dest / "conditions.json")

    # Copy MCP config (gitignored, so capture for reproducibility)
    mcp_src = bench_dir.parent / ".mcp.json"
    if mcp_src.exists():
        shutil.copy2(mcp_src, dest / "mcp_config.json")

    # Copy outputs (may contain model subdirectories)
    shutil.copytree(results_dir, dest / "results")
    logs_dir = bench_dir / "logs"
    if logs_dir.exists() and any(logs_dir.iterdir()):
        shutil.copytree(logs_dir, dest / "logs")

    # Summary
    n_results = len(list((dest / "results").rglob("*.json")))
    n_logs = len(list((dest / "logs").rglob("*.jsonl"))) if (dest / "logs").exists() else 0

    # Detect models from directory structure
    model_dirs = [d.name for d in (dest / "results").iterdir()
                  if d.is_dir() and not d.name.startswith(("_", "."))]

    print(f"Archived to {dest}")
    print(f"  prompts/       — system prompts used")
    print(f"  data/          — questions + ground truth")
    print(f"  results/       — {n_results} result files")
    if model_dirs:
        print(f"    models:      {', '.join(sorted(model_dirs))}")
    print(f"  logs/          — {n_logs} conversation logs")
    if (dest / "conditions.json").exists():
        print(f"  conditions.json — conditions config")
    if (dest / "mcp_config.json").exists():
        print(f"  mcp_config.json — MCP server config snapshot")
    print()
    print(f"Next steps:")
    print(f"  python3 harness/score.py {name}")
    print(f"  python3 harness/trace.py {name}")

    # Clear working directories for next run
    print()
    answer = input("Clear results/ and logs/ for next run? [y/N] ")
    if answer.lower() == "y":
        shutil.rmtree(results_dir)
        results_dir.mkdir()
        if logs_dir.exists():
            shutil.rmtree(logs_dir)
            logs_dir.mkdir()
        print("Cleared.")
    else:
        print("Kept. Remove manually or the runner will skip existing results.")


def main():
    if len(sys.argv) < 2:
        print("Usage: archive.py [--bench BENCH] <run-name>")
        print()
        print("Examples:")
        print("  archive.py run-0-first")
        print("  archive.py --bench other-bench run-1-tuned")
        print()

        bench_dir = DEFAULT_BENCH
        # Parse --bench if provided
        args = sys.argv[1:]
        if "--bench" in args:
            idx = args.index("--bench")
            if idx + 1 < len(args):
                bench_dir = Path(args[idx + 1]).resolve()

        runs_dir = bench_dir / "runs"
        if runs_dir.exists():
            existing = sorted(p.name for p in runs_dir.iterdir() if p.is_dir())
            if existing:
                print("Existing archives:")
                for name in existing:
                    print(f"  {name}")
        sys.exit(1)

    # Simple arg parsing: extract --bench and the run name
    bench_dir = DEFAULT_BENCH
    args = sys.argv[1:]
    if "--bench" in args:
        idx = args.index("--bench")
        bench_dir = Path(args[idx + 1]).resolve()
        args = args[:idx] + args[idx + 2:]

    if not args:
        print("Error: run name required")
        sys.exit(1)

    archive(args[0], bench_dir)


if __name__ == "__main__":
    main()
