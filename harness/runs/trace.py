#!/usr/bin/env python3
"""Extract structured reasoning traces from experiment JSONL logs.

Usage: trace.py <run-name> [log-file ...]
Examples:
  trace.py run-0-first                        # all logs in run
  trace.py run-0-first impl-001_graph.jsonl    # specific log
"""

import json
import sys
from pathlib import Path

RUNS_DIR = Path(__file__).parent


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
                        # Summarize the result
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
            # Truncate long text blocks
            text = step["content"]
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(f"{step_num:2d}. [think] {text}")

        elif kind == "tool_call":
            step_num += 1
            tool = step["tool"]
            detail = step.get("detail", "")
            # Shorten neo4j tool names
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


def main():
    if len(sys.argv) < 2:
        print("Usage: trace.py <run-name> [log-file ...]")
        print()
        existing = sorted(p.name for p in RUNS_DIR.iterdir() if p.is_dir())
        if existing:
            print("Available runs:")
            for name in existing:
                print(f"  {name}")
        sys.exit(1)

    run_name = sys.argv[1]
    logs_dir = RUNS_DIR / run_name / "logs"
    if not logs_dir.exists():
        print(f"Logs not found: {logs_dir}")
        sys.exit(1)

    if len(sys.argv) > 2:
        files = [logs_dir / f for f in sys.argv[2:]]
    else:
        files = sorted(logs_dir.glob("*.jsonl"))

    for log_file in files:
        if not log_file.exists():
            print(f"Not found: {log_file}")
            continue
        print(f"\n{'='*70}")
        print(f"TRACE: {log_file.name}")
        print(f"{'='*70}")
        steps = extract_trace(log_file)
        print(format_trace(steps))


if __name__ == "__main__":
    main()
