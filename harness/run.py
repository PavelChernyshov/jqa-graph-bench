#!/usr/bin/env python3
"""Benchmark runner: loops questions x conditions, delegates to a provider-specific runner.

The orchestrator handles task matrix, parallelism, file I/O, and progress.
Model invocation is delegated to runners (see runners/ package).
"""

import argparse
import json
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from runners import get_runner

_stop_requested = False


def _handle_sigint(signum, frame):
    global _stop_requested
    if _stop_requested:
        print("\n  Force quit.")
        sys.exit(1)
    _stop_requested = True
    print("\n  Ctrl+C received — will stop after current runs finish. Press again to force quit.")


HARNESS_DIR = Path(__file__).parent
DEFAULT_BENCH = HARNESS_DIR.parent / "bench"
DEFAULT_TIMEOUT = 300

_print_lock = threading.Lock()


def _print_batch(lines: list[str]):
    with _print_lock:
        print("\n".join(lines), flush=True)


def load_config(bench_dir: Path) -> dict:
    """Load and validate conditions config from the bench directory."""
    config_path = bench_dir / "conditions.json"
    prompts_dir = bench_dir / "prompts"

    with open(config_path) as f:
        config = json.load(f)

    if "conditions" not in config:
        sys.exit(f"Error: {config_path} missing 'conditions' key")
    for name, cond in config["conditions"].items():
        if "system_prompts" not in cond:
            sys.exit(f"Error: condition '{name}' missing 'system_prompts'")
        for prompt_file in cond["system_prompts"]:
            if not (prompts_dir / prompt_file).exists():
                sys.exit(f"Error: prompt file '{prompt_file}' not found for condition '{name}'")

    return config


def load_questions(bench_dir: Path) -> dict:
    with open(bench_dir / "questions.json") as f:
        return json.load(f)


def read_system_prompt(bench_dir: Path, prompt_files: list[str]) -> str:
    prompts_dir = bench_dir / "prompts"
    parts = []
    for filename in prompt_files:
        parts.append((prompts_dir / filename).read_text())
    return "\n\n".join(parts)


def run_single(runner, bench_dir: Path, q_id: str, question_data: dict,
               condition_name: str, condition_config: dict, model: str,
               max_budget: float, timeout: int, run_num: int = 1) -> dict:
    """Run a single question/condition/run via the runner. Returns status dict."""
    results_dir = bench_dir / "results"
    logs_dir = bench_dir / "logs"
    suffix = f"_r{run_num}" if run_num > 1 else ""
    tag = f"{q_id}/{condition_name}" + (f"/r{run_num}" if run_num > 1 else "")

    model_results_dir = results_dir / model
    model_logs_dir = logs_dir / model
    model_results_dir.mkdir(parents=True, exist_ok=True)
    model_logs_dir.mkdir(parents=True, exist_ok=True)

    result_file = model_results_dir / f"{q_id}_{condition_name}{suffix}.json"
    log_file = model_logs_dir / f"{q_id}_{condition_name}{suffix}.jsonl"

    if result_file.exists():
        return {"skipped": True, "tag": tag}

    prompt = question_data["question"]
    system_prompt = read_system_prompt(bench_dir, condition_config["system_prompts"])

    _print_batch([f"  START {tag} ({model})"])
    start = time.time()

    result = runner.run(
        prompt=prompt,
        system_prompt=system_prompt,
        condition=condition_config,
        model=model,
        max_budget=max_budget,
        timeout=timeout,
        cwd=HARNESS_DIR.parent,
    )

    elapsed = time.time() - start

    # Write raw log
    if result.raw_log:
        with open(log_file, "w") as f:
            f.write(result.raw_log)

    # Write standardized result
    result_data = result.to_result_dict()
    with open(result_file, "w") as f:
        json.dump(result_data, f, indent=2)

    if result.error:
        _print_batch([
            f"  FAIL  {tag} ({elapsed:.0f}s)",
            f"        {result.error_msg or 'unknown error'}",
        ])
        return {"error": True, "tag": tag}

    cost = result.cost_usd or "?"
    turns = result.num_turns or "?"
    graph_calls = sum(1 for t in result.tool_calls if "graph" in t)
    _print_batch([
        f"  DONE  {tag} ({model}) — "
        f"${cost}, {turns} turns, {elapsed:.0f}s, "
        f"{len(result.tool_calls)} tool calls ({graph_calls} graph)"
    ])

    return {"error": False, "tag": tag}


def main():
    signal.signal(signal.SIGINT, _handle_sigint)

    parser = argparse.ArgumentParser(description="Structural question benchmark runner")
    parser.add_argument("question", nargs="?", help="Question ID (e.g. impl-001)")
    parser.add_argument("condition", nargs="?", help="Condition name (e.g. baseline, graph)")
    parser.add_argument("-n", "--runs", type=int, default=1, help="Runs per question/condition (default: 1)")
    parser.add_argument("-j", "--parallel", type=int, default=1, help="Max parallel runs (default: 1)")
    parser.add_argument("--model", default=None, help="Model name (default: from config)")
    parser.add_argument("--runner", default=None, help="Runner backend (default: from config, e.g. claude)")
    parser.add_argument("--bench", default=str(DEFAULT_BENCH), help="Bench directory (default: ./bench)")
    parser.add_argument("--budget", type=float, default=None, help="Max budget per run in USD")
    parser.add_argument("--timeout", type=int, default=None, help=f"Timeout per run in seconds (default: {DEFAULT_TIMEOUT})")
    args = parser.parse_args()

    bench_dir = Path(args.bench).resolve()
    if not bench_dir.exists():
        sys.exit(f"Bench directory not found: {bench_dir}")

    config = load_config(bench_dir)
    defaults = config.get("defaults", {})

    model = args.model or defaults.get("model", "sonnet")
    max_budget = args.budget or defaults.get("max_budget_usd", 0.30)
    timeout = args.timeout or defaults.get("timeout", DEFAULT_TIMEOUT)
    runner_name = args.runner or defaults.get("runner", "claude")

    runner = get_runner(runner_name, defaults, bench_dir)
    conditions = config["conditions"]
    questions = load_questions(bench_dir)
    (bench_dir / "results").mkdir(exist_ok=True)
    (bench_dir / "logs").mkdir(exist_ok=True)

    # Build task list
    tasks = []
    for run_num in range(1, args.runs + 1):
        for q_id, q_data in questions.items():
            if args.question and q_id != args.question:
                continue
            for condition_name, condition_config in conditions.items():
                if args.condition and condition_name != args.condition:
                    continue
                tasks.append((runner, bench_dir, q_id, q_data, condition_name,
                              condition_config, model, max_budget, timeout, run_num))

    print(f"Queued {len(tasks)} runs (runner={runner_name}, model={model}, j={args.parallel})")

    total = 0
    failed = 0

    if args.parallel <= 1:
        for task in tasks:
            if _stop_requested:
                break
            total += 1
            result = run_single(*task)
            if result.get("error"):
                failed += 1
    else:
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {}
            for task in tasks:
                if _stop_requested:
                    break
                fut = pool.submit(run_single, *task)
                futures[fut] = task

            for fut in as_completed(futures):
                total += 1
                result = fut.result()
                if result.get("error"):
                    failed += 1

    print(f"\n{'='*60}")
    print(f"Complete: {total} runs, {failed} failed")
    print(f"Results in: {bench_dir / 'results' / model}")


if __name__ == "__main__":
    main()
