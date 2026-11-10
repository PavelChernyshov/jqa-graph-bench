"""Tests for the orchestrator: config loading, prompt reading, run_single with a fake runner."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import run
from runners import RunResult


# ── load_config ───────────────────────────────────────────────────────

class TestLoadConfig:
    def _make_bench(self, tmp_path, conditions):
        """Create a minimal bench dir with prompts and a conditions.json."""
        prompts = tmp_path / "prompts"
        prompts.mkdir()
        (prompts / "base.md").write_text("base prompt")
        config_file = tmp_path / "conditions.json"
        config_file.write_text(json.dumps(conditions))
        return tmp_path

    def test_valid_config(self, tmp_path):
        bench = self._make_bench(tmp_path, {
            "conditions": {
                "test": {"tools": "Read,Grep", "system_prompts": ["base.md"]},
            },
            "defaults": {"model": "sonnet"},
        })
        result = run.load_config(bench)
        assert "conditions" in result
        assert "test" in result["conditions"]

    def test_missing_conditions_key(self, tmp_path):
        bench = self._make_bench(tmp_path, {"defaults": {}})
        with pytest.raises(SystemExit):
            run.load_config(bench)

    def test_missing_system_prompts(self, tmp_path):
        bench = self._make_bench(tmp_path, {
            "conditions": {"test": {"tools": "Read"}},
        })
        with pytest.raises(SystemExit):
            run.load_config(bench)

    def test_nonexistent_prompt_file(self, tmp_path):
        bench = self._make_bench(tmp_path, {
            "conditions": {"test": {"system_prompts": ["missing.md"]}},
        })
        with pytest.raises(SystemExit):
            run.load_config(bench)


# ── read_system_prompt ────────────────────────────────────────────────

class TestReadSystemPrompt:
    def test_single_file(self, tmp_path):
        prompts = tmp_path / "prompts"
        prompts.mkdir()
        (prompts / "base.md").write_text("base content")
        result = run.read_system_prompt(tmp_path, ["base.md"])
        assert result == "base content"

    def test_concatenates_multiple(self, tmp_path):
        prompts = tmp_path / "prompts"
        prompts.mkdir()
        (prompts / "base.md").write_text("base")
        (prompts / "graph.md").write_text("graph")
        result = run.read_system_prompt(tmp_path, ["base.md", "graph.md"])
        assert result == "base\n\ngraph"


# ── run_single ────────────────────────────────────────────────────────

class FakeRunner:
    """A runner that returns a canned result without invoking any external process."""

    def __init__(self, result: RunResult):
        self._result = result
        self.calls = []

    def run(self, **kwargs) -> RunResult:
        self.calls.append(kwargs)
        return self._result


class TestRunSingle:
    def _make_bench(self, tmp_path):
        """Create a minimal bench dir for run_single tests."""
        bench = tmp_path / "bench"
        (bench / "results").mkdir(parents=True)
        (bench / "logs").mkdir(parents=True)
        prompts = bench / "prompts"
        prompts.mkdir()
        (prompts / "base.md").write_text("system prompt")
        return bench

    def test_success_writes_result_and_log(self, tmp_path):
        bench = self._make_bench(tmp_path)
        fake = FakeRunner(RunResult(
            result_text="RELEVANT_FILES_START\na.java | found\nRELEVANT_FILES_END",
            tool_calls=["Grep", "Read"],
            cost_usd=0.04,
            duration_ms=2000,
            num_turns=2,
            raw_log='{"type":"result"}\n',
        ))

        condition = {"system_prompts": ["base.md"], "tools": "Read,Grep"}
        question = {"question": "Find all implementations"}

        status = run.run_single(
            fake, bench, "impl-001", question, "baseline", condition,
            "sonnet", 0.30, 300, 1,
        )

        assert status["error"] is False
        assert status["tag"] == "impl-001/baseline"

        result_file = bench / "results" / "sonnet" / "impl-001_baseline.json"
        assert result_file.exists()
        data = json.loads(result_file.read_text())
        assert data["result"] == fake._result.result_text
        assert data["tool_calls"] == ["Grep", "Read"]

        log_file = bench / "logs" / "sonnet" / "impl-001_baseline.jsonl"
        assert log_file.exists()

    def test_skip_existing_result(self, tmp_path):
        bench = self._make_bench(tmp_path)
        model_dir = bench / "results" / "sonnet"
        model_dir.mkdir(parents=True)
        (model_dir / "impl-001_baseline.json").write_text("{}")

        fake = FakeRunner(RunResult())
        condition = {"system_prompts": ["base.md"], "tools": "Read"}
        question = {"question": "test"}

        status = run.run_single(
            fake, bench, "impl-001", question, "baseline", condition,
            "sonnet", 0.30, 300, 1,
        )

        assert status["skipped"] is True
        assert len(fake.calls) == 0

    def test_error_result_written(self, tmp_path):
        bench = self._make_bench(tmp_path)
        fake = FakeRunner(RunResult(
            error=True,
            error_msg="Timeout after 300s",
            duration_ms=300000,
        ))
        condition = {"system_prompts": ["base.md"], "tools": "Read"}
        question = {"question": "test"}

        status = run.run_single(
            fake, bench, "impl-001", question, "graph", condition,
            "opus", 0.30, 300, 1,
        )

        assert status["error"] is True
        result_file = bench / "results" / "opus" / "impl-001_graph.json"
        data = json.loads(result_file.read_text())
        assert data["error"] is True
        assert data["error_msg"] == "Timeout after 300s"

    def test_run_num_suffix(self, tmp_path):
        bench = self._make_bench(tmp_path)
        fake = FakeRunner(RunResult(result_text="ok"))
        condition = {"system_prompts": ["base.md"], "tools": "Read"}
        question = {"question": "test"}

        status = run.run_single(
            fake, bench, "impl-001", question, "baseline", condition,
            "sonnet", 0.30, 300, 3,
        )

        assert status["tag"] == "impl-001/baseline/r3"
        assert (bench / "results" / "sonnet" / "impl-001_baseline_r3.json").exists()

    def test_runner_receives_correct_args(self, tmp_path):
        bench = self._make_bench(tmp_path)
        fake = FakeRunner(RunResult(result_text="ok"))
        condition = {"system_prompts": ["base.md"], "tools": "Read,Grep"}
        question = {"question": "What implements Foo?"}

        run.run_single(
            fake, bench, "q1", question, "graph", condition,
            "haiku", 0.50, 120, 1,
        )

        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["prompt"] == "What implements Foo?"
        assert call["system_prompt"] == "system prompt"
        assert call["condition"] is condition
        assert call["model"] == "haiku"
        assert call["max_budget"] == 0.50
        assert call["timeout"] == 120

    def test_no_log_written_when_raw_log_is_none(self, tmp_path):
        bench = self._make_bench(tmp_path)
        fake = FakeRunner(RunResult(result_text="ok", raw_log=None))
        condition = {"system_prompts": ["base.md"], "tools": "Read"}
        question = {"question": "test"}

        run.run_single(
            fake, bench, "impl-001", question, "baseline", condition,
            "sonnet", 0.30, 300, 1,
        )

        log_file = bench / "logs" / "sonnet" / "impl-001_baseline.jsonl"
        assert not log_file.exists()
