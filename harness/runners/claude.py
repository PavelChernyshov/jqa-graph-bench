"""Claude Code runner — invokes `claude -p` subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from . import RunResult


class ClaudeRunner:
    """Runner that invokes the Claude Code CLI (`claude -p`)."""

    def __init__(self, defaults: dict, bench_dir: Path | None = None):
        mcp_rel = defaults.get("mcp_config", "../.mcp.json")
        # Resolve relative to bench directory (where conditions.json lives)
        base = bench_dir or Path(__file__).parent.parent
        self.mcp_config_path = (base / mcp_rel).resolve()

    def run(self, *, prompt: str, system_prompt: str,
            condition: dict, model: str,
            max_budget: float, timeout: int,
            cwd: Path) -> RunResult:

        cmd = [
            "claude", "-p", prompt,
            "--model", model,
            "--output-format", "stream-json",
            "--verbose",
            "--max-budget-usd", str(max_budget),
            "--tools", condition["tools"],
            "--allowed-tools", condition["tools"],
            "--append-system-prompt", system_prompt,
            "--no-session-persistence",
            "--setting-sources", "",
            "--mcp-config", str(self.mcp_config_path),
            "--strict-mcp-config",
        ]
        if condition.get("disallowed_tools"):
            cmd += ["--disallowed-tools", condition["disallowed_tools"]]

        env = {**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}

        start = time.time()

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd),
                env=env,
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.time() - start) * 1000)
            return RunResult(
                error=True,
                error_msg=f"Timeout after {timeout}s",
                duration_ms=elapsed_ms,
            )

        elapsed_ms = int((time.time() - start) * 1000)

        if proc.returncode != 0:
            return RunResult(
                error=True,
                error_msg=f"Exit {proc.returncode}: {proc.stderr[:500]}",
                duration_ms=elapsed_ms,
                raw_log=proc.stdout,
            )

        return self._parse_stream(proc.stdout, elapsed_ms)

    def _parse_stream(self, stdout: str, elapsed_ms: int) -> RunResult:
        """Parse Claude's stream-json output into a RunResult."""
        tool_calls = []
        result_obj = None

        for line in stdout.strip().split("\n"):
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
                result_obj = obj

        if result_obj is None:
            return RunResult(
                error=True,
                error_msg="No result object in stream",
                tool_calls=tool_calls,
                duration_ms=elapsed_ms,
                raw_log=stdout,
            )

        # Extract the extra Claude-specific fields the scorer may use
        extra = {}
        if "modelUsage" in result_obj:
            extra["modelUsage"] = result_obj["modelUsage"]
        if "subtype" in result_obj:
            extra["subtype"] = result_obj["subtype"]

        usage = result_obj.get("usage", {})

        return RunResult(
            result_text=result_obj.get("result", ""),
            tool_calls=tool_calls,
            error=result_obj.get("is_error", False),
            cost_usd=result_obj.get("total_cost_usd"),
            duration_ms=result_obj.get("duration_ms", elapsed_ms),
            num_turns=result_obj.get("num_turns"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            raw_log=stdout,
            extra=extra,
        )
