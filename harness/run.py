#!/usr/bin/env python3
"""Structural question experiment runner: loops questions x conditions, calls claude -p."""

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_stop_requested = False


def _handle_sigint(signum, frame):
    global _stop_requested
    if _stop_requested:
        print("\n  Force quit.")
        sys.exit(1)
    _stop_requested = True
    print("\n  Ctrl+C received — will stop after current runs finish. Press again to force quit.")


HARNESS_DIR = Path(__file__).parent
DATA_DIR = HARNESS_DIR / "data"
PROMPTS_DIR = HARNESS_DIR / "prompts"
RESULTS_DIR = HARNESS_DIR / "results"
LOGS_DIR = HARNESS_DIR / "logs"

TOOLS_BASELINE = "Read,Glob,Grep"
TOOLS_GRAPH = "Read,Glob,Grep,mcp__code-graph__query_graph,mcp__code-graph__get_graph_schema,mcp__code-graph__find_types,mcp__code-graph__get_type_context"
TOOLS_GRAPH_ONLY = "mcp__code-graph__query_graph,mcp__code-graph__get_graph_schema,mcp__code-graph__find_types,mcp__code-graph__get_type_context"

CONDITIONS = [
    ("baseline", TOOLS_BASELINE),
    ("graph",    TOOLS_GRAPH),
]

MAX_BUDGET_USD = 0.30
MODEL = "sonnet"

_print_lock = threading.Lock()


def _print_batch(lines: list[str]):
    """Print multiple lines atomically."""
    with _print_lock:
        print("\n".join(lines), flush=True)


def load_questions() -> dict:
    with open(DATA_DIR / "questions.json") as f:
        return json.load(f)


def read_system_prompt(condition_name: str) -> str:
    """Read and concatenate system prompt files for the condition."""
    base = (PROMPTS_DIR / "system_base.md").read_text()
    if condition_name == "graph":
        graph = (PROMPTS_DIR / "system_graph.md").read_text()
        return base + "\n\n" + graph
    return base


def build_prompt(question_data: dict) -> str:
    return question_data["question"]


def run_single(q_id: str, question_data: dict, condition_name: str,
               tools: str, run_num: int = 1) -> dict:
    """Run a single question/condition/run. Returns a result dict with output lines."""
    suffix = f"_r{run_num}" if run_num > 1 else ""
    tag = f"{q_id}/{condition_name}" + (f"/r{run_num}" if run_num > 1 else "")
    result_file = RESULTS_DIR / f"{q_id}_{condition_name}{suffix}.json"
    log_file = LOGS_DIR / f"{q_id}_{condition_name}{suffix}.jsonl"

    if result_file.exists():
        return {"skipped": True, "tag": tag}

    prompt = build_prompt(question_data)
    system_prompt = read_system_prompt(condition_name)

    cmd = [
        "claude", "-p", prompt,
        "--model", MODEL,
        "--output-format", "stream-json",
        "--verbose",
        "--max-budget-usd", str(MAX_BUDGET_USD),
        "--tools", tools,
        "--allowed-tools", tools,
        "--append-system-prompt", system_prompt,
        "--no-session-persistence",
        "--setting-sources", "",
        "--mcp-config", str(HARNESS_DIR.parent / ".mcp.json"),
        "--strict-mcp-config",
    ]
    if condition_name == "baseline":
        cmd += ["--disallowed-tools", TOOLS_GRAPH_ONLY]

    env = {**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}

    _print_batch([f"  START {tag}"])
    start = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(HARNESS_DIR.parent),
            env=env,
        )
        elapsed = time.time() - start

        if result.returncode != 0:
            error_data = {
                "error": True,
                "returncode": result.returncode,
                "stderr": result.stderr,
                "elapsed_seconds": elapsed,
            }
            with open(result_file, "w") as f:
                json.dump(error_data, f, indent=2)
            _print_batch([
                f"  FAIL  {tag} (exit {result.returncode}, {elapsed:.0f}s)",
                f"        stderr: {result.stderr[:300]}",
            ])
            return {"error": True, "tag": tag}

        with open(log_file, "w") as f:
            f.write(result.stdout)

        tool_calls = []
        result_data = None
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "assistant":
                msg = obj.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "tool_use":
                        tool_calls.append(block.get("name", ""))
            elif obj.get("type") == "result":
                result_data = obj

        if result_data is None:
            result_data = {"error": True, "msg": "no result object in stream"}

        result_data["tool_calls"] = tool_calls

        with open(result_file, "w") as f:
            json.dump(result_data, f, indent=2)

        cost = result_data.get("total_cost_usd", "?")
        turns = result_data.get("num_turns", "?")
        graph_calls = sum(1 for t in tool_calls if "graph" in t)
        _print_batch([
            f"  DONE  {tag} — "
            f"${cost}, {turns} turns, {elapsed:.0f}s, "
            f"{len(tool_calls)} tool calls ({graph_calls} graph)"
        ])

        return {"error": False, "tag": tag}

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        error_data = {"error": True, "timeout": True, "elapsed_seconds": elapsed}
        with open(result_file, "w") as f:
            json.dump(error_data, f, indent=2)
        _print_batch([f"  TIMEOUT {tag} after {elapsed:.0f}s"])
        return {"error": True, "tag": tag}


def main():
    signal.signal(signal.SIGINT, _handle_sigint)

    parser = argparse.ArgumentParser(description="Structural question benchmark runner")
    parser.add_argument("question", nargs="?", help="Question ID (e.g. impl-001)")
    parser.add_argument("condition", nargs="?", help="Condition (baseline or graph)")
    parser.add_argument("-n", "--runs", type=int, default=1, help="Number of runs per question/condition (default: 1)")
    parser.add_argument("-j", "--parallel", type=int, default=1, help="Max parallel runs (default: 1)")
    args = parser.parse_args()

    questions = load_questions()
    RESULTS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)

    # Build task list
    tasks = []
    for run_num in range(1, args.runs + 1):
        for q_id, q_data in questions.items():
            if args.question and q_id != args.question:
                continue
            for condition_name, tools in CONDITIONS:
                if args.condition and condition_name != args.condition:
                    continue
                tasks.append((q_id, q_data, condition_name, tools, run_num))

    print(f"Queued {len(tasks)} runs (j={args.parallel})")

    total = 0
    failed = 0

    if args.parallel <= 1:
        for q_id, q_data, cond, tools, run_num in tasks:
            if _stop_requested:
                break
            total += 1
            result = run_single(q_id, q_data, cond, tools, run_num)
            if result.get("error"):
                failed += 1
    else:
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {}
            for task in tasks:
                if _stop_requested:
                    break
                q_id, q_data, cond, tools, run_num = task
                fut = pool.submit(run_single, q_id, q_data, cond, tools, run_num)
                futures[fut] = task

            for fut in as_completed(futures):
                total += 1
                result = fut.result()
                if result.get("error"):
                    failed += 1

    print(f"\n{'='*60}")
    print(f"Complete: {total} runs, {failed} failed")
    print(f"Results in: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
