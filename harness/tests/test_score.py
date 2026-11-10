"""Tests for scoring functions: path normalization, file parsing, metrics, discovery."""

import json
from pathlib import Path

from score import (
    normalize_path,
    parse_file_list,
    compute_metrics,
    score_result,
    _extract_conditions,
    discover_models_and_conditions,
)


# ── normalize_path ────────────────────────────────────────────────────

class TestNormalizePath:
    def test_strips_repo_marker(self):
        p = "/abs/subjects/thingsboard/repo/app/src/main/java/Foo.java"
        assert normalize_path(p) == "app/src/main/java/Foo.java"

    def test_strips_dot_slash(self):
        assert normalize_path("./app/Foo.java") == "app/Foo.java"

    def test_already_relative(self):
        assert normalize_path("app/src/Foo.java") == "app/src/Foo.java"

    def test_strips_whitespace(self):
        assert normalize_path("  app/Foo.java  ") == "app/Foo.java"

    def test_repo_marker_takes_precedence_over_dot_slash(self):
        p = "./subjects/thingsboard/repo/app/Foo.java"
        assert normalize_path(p) == "app/Foo.java"


# ── parse_file_list ───────────────────────────────────────────────────

class TestParseFileList:
    def test_basic(self):
        text = """Some preamble.
RELEVANT_FILES_START
app/src/main/java/Foo.java | implements interface
app/src/main/java/Bar.java | also implements
RELEVANT_FILES_END
Some epilogue."""
        files = parse_file_list(text)
        assert files == [
            "app/src/main/java/Foo.java",
            "app/src/main/java/Bar.java",
        ]

    def test_with_repo_prefix(self):
        text = """RELEVANT_FILES_START
subjects/thingsboard/repo/app/src/Foo.java | reason
RELEVANT_FILES_END"""
        files = parse_file_list(text)
        assert files == ["app/src/Foo.java"]

    def test_no_block_returns_empty(self):
        assert parse_file_list("no files here") == []

    def test_empty_block(self):
        text = "RELEVANT_FILES_START\n\nRELEVANT_FILES_END"
        assert parse_file_list(text) == []

    def test_non_java_files_excluded(self):
        text = """RELEVANT_FILES_START
app/Foo.java | yes
app/config.xml | no
app/Bar.java | yes
RELEVANT_FILES_END"""
        assert parse_file_list(text) == ["app/Foo.java", "app/Bar.java"]

    def test_no_reason_column(self):
        text = """RELEVANT_FILES_START
app/Foo.java
RELEVANT_FILES_END"""
        assert parse_file_list(text) == ["app/Foo.java"]

    def test_whitespace_around_lines(self):
        text = """RELEVANT_FILES_START
  app/Foo.java | reason
RELEVANT_FILES_END"""
        assert parse_file_list(text) == ["app/Foo.java"]


# ── compute_metrics ───────────────────────────────────────────────────

class TestComputeMetrics:
    def test_perfect_match(self):
        m = compute_metrics(["a.java", "b.java"], ["a.java", "b.java"])
        assert m["recall"] == 1.0
        assert m["precision"] == 1.0
        assert m["f1"] == 1.0
        assert m["tp_count"] == 2
        assert m["fp_count"] == 0
        assert m["fn_count"] == 0

    def test_partial_recall(self):
        m = compute_metrics(["a.java"], ["a.java", "b.java"])
        assert m["recall"] == 0.5
        assert m["precision"] == 1.0
        assert m["fn_count"] == 1

    def test_false_positives(self):
        m = compute_metrics(["a.java", "c.java"], ["a.java"])
        assert m["recall"] == 1.0
        assert m["precision"] == 0.5
        assert m["fp_count"] == 1

    def test_no_overlap(self):
        m = compute_metrics(["c.java"], ["a.java", "b.java"])
        assert m["recall"] == 0.0
        assert m["precision"] == 0.0
        assert m["f1"] == 0.0

    def test_empty_reported(self):
        m = compute_metrics([], ["a.java"])
        assert m["recall"] == 0.0
        assert m["precision"] == 0
        assert m["f1"] == 0.0

    def test_empty_ground_truth(self):
        m = compute_metrics(["a.java"], [])
        assert m["recall"] == 0
        assert m["f1"] == 0.0

    def test_both_empty(self):
        m = compute_metrics([], [])
        assert m["f1"] == 0.0

    def test_f1_formula(self):
        # 2 of 3 GT found, 1 FP: recall=2/3, precision=2/3, f1=2/3
        m = compute_metrics(["a.java", "b.java", "x.java"], ["a.java", "b.java", "c.java"])
        assert m["tp_count"] == 2
        assert m["fp_count"] == 1
        assert m["fn_count"] == 1
        assert m["recall"] == round(2 / 3, 3)
        assert m["precision"] == round(2 / 3, 3)
        expected_f1 = round(2 * (2/3) * (2/3) / ((2/3) + (2/3)), 3)
        assert m["f1"] == expected_f1

    def test_duplicates_in_reported_deduplicated(self):
        m = compute_metrics(["a.java", "a.java"], ["a.java"])
        assert m["tp_count"] == 1
        assert m["fp_count"] == 0
        assert m["reported_count"] == 1


# ── score_result ──────────────────────────────────────────────────────

class TestScoreResult:
    def test_scores_valid_result(self, tmp_path):
        result_data = {
            "result": "RELEVANT_FILES_START\na.java | found\nb.java | found\nRELEVANT_FILES_END",
            "total_cost_usd": 0.05,
            "duration_ms": 3000,
            "num_turns": 2,
            "usage": {"input_tokens": 1000, "output_tokens": 200},
            "tool_calls": ["Grep", "Read", "mcp__code-graph__find_types"],
        }
        result_file = tmp_path / "q1_baseline.json"
        result_file.write_text(json.dumps(result_data))

        m = score_result(result_file, tmp_path, ["a.java", "b.java", "c.java"])
        assert m["tp_count"] == 2
        assert m["fn_count"] == 1
        assert m["cost_usd"] == 0.05
        assert m["total_tool_calls"] == 3
        assert m["graph_queries"] == 1
        assert m["used_graph"] is True

    def test_error_result_returns_error(self, tmp_path):
        result_file = tmp_path / "q1_baseline.json"
        result_file.write_text(json.dumps({"error": True, "returncode": 1}))
        m = score_result(result_file, tmp_path, ["a.java"])
        assert m["error"] is True

    def test_missing_file_returns_none(self, tmp_path):
        m = score_result(tmp_path / "nonexistent.json", tmp_path, ["a.java"])
        assert m is None

    def test_empty_result_text(self, tmp_path):
        result_file = tmp_path / "q1_baseline.json"
        result_file.write_text(json.dumps({"result": ""}))
        m = score_result(result_file, tmp_path, ["a.java"])
        assert m["error"] is True

    def test_no_tool_calls_in_result_falls_back_to_log(self, tmp_path):
        result_data = {
            "result": "RELEVANT_FILES_START\na.java | found\nRELEVANT_FILES_END",
            "total_cost_usd": 0.01,
            "usage": {},
        }
        result_file = tmp_path / "q1_graph.json"
        result_file.write_text(json.dumps(result_data))

        # Write a log file that the scorer should fall back to
        log_file = tmp_path / "q1_graph.jsonl"
        log_file.write_text(json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "mcp__code-graph__query_graph"},
            ]},
        }))

        m = score_result(result_file, tmp_path, ["a.java"])
        assert m["graph_queries"] == 1


# ── _extract_conditions ───────────────────────────────────────────────

class TestExtractConditions:
    def test_basic(self, tmp_path):
        (tmp_path / "impl-001_baseline.json").write_text("{}")
        (tmp_path / "impl-001_graph.json").write_text("{}")
        (tmp_path / "blast-001_baseline.json").write_text("{}")
        q_ids = {"impl-001", "blast-001"}
        assert _extract_conditions(tmp_path, q_ids) == {"baseline", "graph"}

    def test_with_run_suffix(self, tmp_path):
        (tmp_path / "impl-001_baseline.json").write_text("{}")
        (tmp_path / "impl-001_baseline_r2.json").write_text("{}")
        (tmp_path / "impl-001_baseline_r10.json").write_text("{}")
        q_ids = {"impl-001"}
        assert _extract_conditions(tmp_path, q_ids) == {"baseline"}

    def test_custom_condition_name(self, tmp_path):
        (tmp_path / "impl-001_graph-v2.json").write_text("{}")
        q_ids = {"impl-001"}
        assert _extract_conditions(tmp_path, q_ids) == {"graph-v2"}

    def test_ignores_underscore_prefix(self, tmp_path):
        (tmp_path / "_config.json").write_text("{}")
        (tmp_path / "impl-001_baseline.json").write_text("{}")
        q_ids = {"impl-001"}
        assert _extract_conditions(tmp_path, q_ids) == {"baseline"}

    def test_empty_dir(self, tmp_path):
        assert _extract_conditions(tmp_path, {"impl-001"}) == set()


# ── discover_models_and_conditions ────────────────────────────────────

class TestDiscoverModelsAndConditions:
    def _questions(self):
        return {"impl-001": {}, "blast-001": {}}

    def test_model_subdirs(self, tmp_path):
        sonnet = tmp_path / "sonnet"
        sonnet.mkdir()
        (sonnet / "impl-001_baseline.json").write_text("{}")
        (sonnet / "impl-001_graph.json").write_text("{}")
        opus = tmp_path / "opus"
        opus.mkdir()
        (opus / "impl-001_graph.json").write_text("{}")

        result = discover_models_and_conditions(tmp_path, self._questions())
        assert result == {
            "opus": ["graph"],
            "sonnet": ["baseline", "graph"],
        }

    def test_legacy_flat_layout(self, tmp_path):
        (tmp_path / "impl-001_baseline.json").write_text("{}")
        (tmp_path / "impl-001_graph.json").write_text("{}")

        result = discover_models_and_conditions(tmp_path, self._questions())
        assert result == {"unknown": ["baseline", "graph"]}

    def test_empty_dir_falls_back(self, tmp_path):
        result = discover_models_and_conditions(tmp_path, self._questions())
        assert result == {"unknown": ["baseline", "graph"]}

    def test_hidden_dirs_ignored(self, tmp_path):
        (tmp_path / ".cache").mkdir()
        (tmp_path / "impl-001_baseline.json").write_text("{}")

        result = discover_models_and_conditions(tmp_path, self._questions())
        assert result == {"unknown": ["baseline"]}
