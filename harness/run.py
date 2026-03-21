#!/usr/bin/env python3
"""Structural question experiment runner: loops questions x conditions, calls claude -p."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_stop_requested = False


def _handle_sigint(signum, frame):
    global _stop_requested
    if _stop_requested:
        print("\n  Force quit.")
        sys.exit(1)
    _stop_requested = True
    print("\n  Ctrl+C received — will stop after current run finishes. Press again to force quit.")


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
               tools: str) -> Path:
    result_file = RESULTS_DIR / f"{q_id}_{condition_name}.json"
    log_file = LOGS_DIR / f"{q_id}_{condition_name}.jsonl"
    if result_file.exists():
        print(f"  SKIP {q_id}/{condition_name} — result exists")
        return result_file

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
    # Baseline: explicitly block graph tools
    if condition_name == "baseline":
        cmd += ["--disallowed-tools", TOOLS_GRAPH_ONLY]

    # Isolate from user memory files
    env = {**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}

    print(f"  RUN  {q_id}/{condition_name}")
    print(f"       category: {question_data['category']}")
    print(f"       tools: {'graph+file' if 'mcp' in tools else 'file-only'}")
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
            print(f"  FAIL {q_id}/{condition_name} (exit {result.returncode})")
            print(f"       stderr: {result.stderr[:500]}")
            error_data = {
                "error": True,
                "returncode": result.returncode,
                "stderr": result.stderr,
                "elapsed_seconds": elapsed,
            }
            with open(result_file, "w") as f:
                json.dump(error_data, f, indent=2)
            return result_file

        # Save full stream as JSONL log
        with open(log_file, "w") as f:
            f.write(result.stdout)

        # Parse stream: extract tool usage and result object
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

        # Attach tool call summary to the result
        result_data["tool_calls"] = tool_calls

        with open(result_file, "w") as f:
            json.dump(result_data, f, indent=2)

        cost = result_data.get("total_cost_usd", "?")
        turns = result_data.get("num_turns", "?")
        graph_calls = sum(1 for t in tool_calls if "graph" in t)
        print(f"  DONE {q_id}/{condition_name} — "
              f"${cost}, {turns} turns, {elapsed:.0f}s, "
              f"{len(tool_calls)} tool calls ({graph_calls} graph)")

        return result_file

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"  TIMEOUT {q_id}/{condition_name} after {elapsed:.0f}s")
        error_data = {"error": True, "timeout": True, "elapsed_seconds": elapsed}
        with open(result_file, "w") as f:
            json.dump(error_data, f, indent=2)
        return result_file


def main():
    signal.signal(signal.SIGINT, _handle_sigint)

    questions = load_questions()
    RESULTS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)

    # Allow filtering: run.py [question_id] [condition]
    filter_q = sys.argv[1] if len(sys.argv) > 1 else None
    filter_cond = sys.argv[2] if len(sys.argv) > 2 else None

    total = 0
    failed = 0

    for q_id, q_data in questions.items():
        if _stop_requested:
            break
        if filter_q and q_id != filter_q:
            continue
        print(f"\n{'='*60}")
        print(f"Q {q_id}: {q_data['question'][:80]}...")
        print(f"Category: {q_data['category']} | Answer: {len(q_data['answer_files'])} files")
        print(f"{'='*60}")

        for condition_name, tools in CONDITIONS:
            if _stop_requested:
                break
            if filter_cond and condition_name != filter_cond:
                continue
            total += 1
            result_file = run_single(q_id, q_data, condition_name, tools)

            try:
                with open(result_file) as f:
                    data = json.load(f)
                if data.get("error"):
                    failed += 1
            except (json.JSONDecodeError, FileNotFoundError):
                failed += 1

    print(f"\n{'='*60}")
    print(f"Complete: {total} runs, {failed} failed")
    print(f"Results in: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
