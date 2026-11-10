#!/usr/bin/env python3
"""Extract structured reasoning traces from experiment JSONL logs.

Usage: trace.py [--bench BENCH] <run-name> [log-file ...]
Examples:
  trace.py 1-first                            # all logs in run
  trace.py 1-first impl-001_graph.jsonl       # specific log
"""

import argparse
import json
import sys
from pathlib import Path

HARNESS_DIR = Path(__file__).parent
DEFAULT_BENCH = HARNESS_DIR.parent / "bench"


def extract_trace(log_file: Path) -> list[dict]:
    """Parse a JSONL log and return a list of steps."""
    steps = []
    for line in log_file.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = obj.get("type")

        if msg_type == "assistant":
            msg = obj.get("message", {})
            for block in msg.get("content", []):
                if block.get("type") == "text":
                    text = block["text"].strip()
                    if text:
                        steps.append({"kind": "text", "content": text})
                elif block.get("type") == "tool_use":
                    tool_name = block.get("name", "")
                    tool_input = block.get("input", {})
                    step = {"kind": "tool_call", "tool": tool_name}

                    if tool_name == "Glob":
                        step["detail"] = tool_input.get("pattern", "")
                    elif tool_name == "Grep":
                        step["detail"] = tool_input.get("pattern", "")
                        if tool_input.get("path"):
                            step["detail"] += f"  in={tool_input['path']}"
                        if tool_input.get("glob"):
                            step["detail"] += f"  glob={tool_input['glob']}"
                    elif tool_name == "Read":
                        path = tool_input.get("file_path", "")
                        # Shorten path for readability
                        marker = "subjects/thingsboard/repo/"
                        idx = path.find(marker)
                        if idx != -1:
                            path = path[idx + len(marker):]
                        step["detail"] = path
                    elif "graph" in tool_name or "neo4j" in tool_name:
                        step["detail"] = tool_input.get("query", tool_input.get("cypher", str(tool_input)))
                    else:
                        step["detail"] = str(tool_input)[:200]

                    step["tool_use_id"] = block.get("id", "")
                    steps.append(step)

        elif msg_type == "user":
            msg = obj.get("message", {})
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_result":
                        tool_use_id = block.get("tool_use_id", "")
                        result_content = block.get("content", "")
                        is_error = block.get("is_error", False)
                        if isinstance(result_content, str):
                            lines = result_content.strip().split("\n")
                            summary = f"{len(lines)} lines"
                            if is_error:
                                summary = f"ERROR: {result_content[:200]}"
                        else:
                            summary = str(result_content)[:200]
                        steps.append({
                            "kind": "tool_result",
                            "tool_use_id": tool_use_id,
                            "summary": summary,
                            "is_error": is_error,
                        })

        elif msg_type == "result":
            steps.append({
                "kind": "result",
                "turns": obj.get("num_turns"),
                "cost": obj.get("total_cost_usd"),
                "duration_ms": obj.get("duration_ms"),
            })

    return steps


def format_trace(steps: list[dict]) -> str:
    """Format trace steps into readable text."""
    lines = []
    step_num = 0
    for step in steps:
        kind = step["kind"]

        if kind == "text":
            step_num += 1
            text = step["content"]
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(f"{step_num:2d}. [think] {text}")

        elif kind == "tool_call":
            step_num += 1
            tool = step["tool"]
            detail = step.get("detail", "")
            if "graph" in tool or "neo4j" in tool:
                tool = tool.split("__")[-1]
            lines.append(f"{step_num:2d}. [{tool}] {detail}")

        elif kind == "tool_result":
            summary = step["summary"]
            prefix = "    ERROR ->" if step.get("is_error") else "    ->"
            lines.append(f"{prefix} {summary}")

        elif kind == "result":
            lines.append(f"\n--- DONE: {step['turns']} turns, ${step['cost']:.4f}, {step['duration_ms']}ms ---")

    return "\n".join(lines)


def _collect_log_files(logs_dir: Path) -> list[Path]:
    """Collect log files, handling both flat and model-subdirectory layouts."""
    subdirs = [d for d in logs_dir.iterdir()
               if d.is_dir() and not d.name.startswith(("_", "."))]
    if subdirs:
        files = []
        for model_dir in sorted(subdirs):
            for f in sorted(model_dir.glob("*.jsonl")):
                files.append(f)
        return files
    return sorted(logs_dir.glob("*.jsonl"))


def main():
    parser = argparse.ArgumentParser(description="Extract reasoning traces from experiment logs")
    parser.add_argument("run_name", help="Run name (looked up under bench/runs/)")
    parser.add_argument("log_files", nargs="*", help="Specific log files to trace (default: all)")
    parser.add_argument("--bench", default=str(DEFAULT_BENCH), help="Bench directory (default: ./bench)")
    args = parser.parse_args()

    bench_dir = Path(args.bench).resolve()
    runs_dir = bench_dir / "runs"

    logs_dir = runs_dir / args.run_name / "logs"
    if not logs_dir.exists():
        print(f"Logs not found: {logs_dir}")
        existing = sorted(p.name for p in runs_dir.iterdir() if p.is_dir()) if runs_dir.exists() else []
        if existing:
            print("\nAvailable runs:")
            for name in existing:
                print(f"  {name}")
        sys.exit(1)

    if args.log_files:
        files = []
        for f_name in args.log_files:
            candidate = logs_dir / f_name
            if candidate.exists():
                files.append(candidate)
            else:
                found = list(logs_dir.rglob(f_name))
                if found:
                    files.extend(found)
                else:
                    files.append(candidate)
    else:
        files = _collect_log_files(logs_dir)

    for log_file in files:
        if not log_file.exists():
            print(f"Not found: {log_file}")
            continue
        rel = log_file.relative_to(logs_dir)
        label = str(rel)
        print(f"\n{'='*70}")
        print(f"TRACE: {label}")
        print(f"{'='*70}")
        steps = extract_trace(log_file)
        print(format_trace(steps))


if __name__ == "__main__":
    main()
