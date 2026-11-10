"""Runner interface for benchmark harness.

A runner knows how to invoke a model/provider and return a standardized result.
The harness handles everything else: task matrix, parallelism, file I/O, scoring.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class RunResult:
    """Standardized result from a single benchmark run."""
    result_text: str = ""
    tool_calls: list[str] = field(default_factory=list)
    error: bool = False
    error_msg: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw_log: str | None = None
    extra: dict = field(default_factory=dict)

    def to_result_dict(self) -> dict:
        """Convert to the JSON format written to result files."""
        d = {
            "result": self.result_text,
            "error": self.error,
            "is_error": self.error,
            "tool_calls": self.tool_calls,
            "total_cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "num_turns": self.num_turns,
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            },
        }
        if self.error_msg:
            d["error_msg"] = self.error_msg
        d.update(self.extra)
        return d


class Runner(Protocol):
    """Interface that provider-specific runners must implement."""

    def run(self, *, prompt: str, system_prompt: str,
            condition: dict, model: str,
            max_budget: float, timeout: int,
            cwd: Path) -> RunResult:
        """Execute a single benchmark run.

        Args:
            prompt: The question text.
            system_prompt: Concatenated system prompt content.
            condition: The condition config dict from conditions.json.
            model: Model identifier (provider-specific interpretation).
            max_budget: Cost cap per run in USD.
            timeout: Time limit in seconds.
            cwd: Working directory for the model invocation.

        Returns:
            RunResult with the model's answer, tool usage, and metrics.
        """
        ...


def get_runner(name: str, defaults: dict, bench_dir: Path | None = None) -> Runner:
    """Instantiate a runner by name."""
    if name == "claude":
        from .claude import ClaudeRunner
        return ClaudeRunner(defaults, bench_dir)
    else:
        sys.exit(f"Unknown runner: '{name}'. Available: claude")
