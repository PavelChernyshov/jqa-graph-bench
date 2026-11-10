"""Tests for the runner interface and Claude runner stream parsing."""

import json

from runners import RunResult
from runners.claude import ClaudeRunner


# ── RunResult.to_result_dict ──────────────────────────────────────────

class TestRunResultSerialization:
    def test_success(self):
        r = RunResult(
            result_text="answer",
            tool_calls=["Grep", "Read"],
            cost_usd=0.05,
            duration_ms=5000,
            num_turns=3,
            input_tokens=1000,
            output_tokens=500,
        )
        d = r.to_result_dict()
        assert d["result"] == "answer"
        assert d["error"] is False
        assert d["is_error"] is False
        assert d["tool_calls"] == ["Grep", "Read"]
        assert d["total_cost_usd"] == 0.05
        assert d["duration_ms"] == 5000
        assert d["num_turns"] == 3
        assert d["usage"]["input_tokens"] == 1000
        assert d["usage"]["output_tokens"] == 500

    def test_error_includes_msg(self):
        r = RunResult(error=True, error_msg="Timeout after 300s")
        d = r.to_result_dict()
        assert d["error"] is True
        assert d["error_msg"] == "Timeout after 300s"

    def test_error_without_msg_has_no_key(self):
        r = RunResult(error=True)
        d = r.to_result_dict()
        assert "error_msg" not in d

    def test_extra_fields_merged(self):
        r = RunResult(
            result_text="x",
            extra={"modelUsage": {"claude-sonnet-4-6": {"input": 100}}, "subtype": "success"},
        )
        d = r.to_result_dict()
        assert d["modelUsage"] == {"claude-sonnet-4-6": {"input": 100}}
        assert d["subtype"] == "success"

    def test_defaults(self):
        r = RunResult()
        assert r.result_text == ""
        assert r.tool_calls == []
        assert r.error is False
        assert r.cost_usd is None

    def test_serializable_to_json(self):
        r = RunResult(result_text="ok", tool_calls=["Grep"], cost_usd=0.01,
                      duration_ms=100, num_turns=1, input_tokens=10, output_tokens=5)
        s = json.dumps(r.to_result_dict())
        assert isinstance(s, str)


# ── ClaudeRunner._parse_stream ────────────────────────────────────────

def _make_stream(*lines):
    """Build a JSONL stream string from dicts."""
    return "\n".join(json.dumps(obj) for obj in lines)


class TestClaudeParseStream:
    def _parser(self):
        # Create a runner instance (mcp_config path doesn't matter for parsing)
        return ClaudeRunner({"mcp_config": "/dev/null"})

    def test_successful_stream(self):
        stream = _make_stream(
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "thinking..."},
                {"type": "tool_use", "name": "Grep", "id": "t1", "input": {"pattern": "foo"}},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "id": "t2", "input": {"file_path": "/a.java"}},
            ]}},
            {"type": "result", "result": "RELEVANT_FILES_START\na.java | found\nRELEVANT_FILES_END",
             "is_error": False, "total_cost_usd": 0.05, "duration_ms": 3000,
             "num_turns": 2, "usage": {"input_tokens": 800, "output_tokens": 200}},
        )
        r = self._parser()._parse_stream(stream, 3500)
        assert r.error is False
        assert r.tool_calls == ["Grep", "Read"]
        assert "RELEVANT_FILES_START" in r.result_text
        assert r.cost_usd == 0.05
        assert r.duration_ms == 3000  # from result obj, not wall clock
        assert r.num_turns == 2
        assert r.input_tokens == 800
        assert r.output_tokens == 200
        assert r.raw_log == stream

    def test_no_result_object(self):
        stream = _make_stream(
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Grep", "id": "t1", "input": {}},
            ]}},
        )
        r = self._parser()._parse_stream(stream, 1000)
        assert r.error is True
        assert "No result object" in r.error_msg
        assert r.tool_calls == ["Grep"]

    def test_empty_stream(self):
        r = self._parser()._parse_stream("", 500)
        assert r.error is True

    def test_malformed_lines_skipped(self):
        stream = "not json\n" + json.dumps(
            {"type": "result", "result": "answer", "is_error": False,
             "total_cost_usd": 0.01, "usage": {}}
        )
        r = self._parser()._parse_stream(stream, 100)
        assert r.error is False
        assert r.result_text == "answer"

    def test_extra_fields_extracted(self):
        stream = _make_stream(
            {"type": "result", "result": "ok", "is_error": False,
             "total_cost_usd": 0.02, "usage": {},
             "modelUsage": {"claude-sonnet-4-6": {"input": 50}},
             "subtype": "success"},
        )
        r = self._parser()._parse_stream(stream, 100)
        assert r.extra["modelUsage"] == {"claude-sonnet-4-6": {"input": 50}}
        assert r.extra["subtype"] == "success"

    def test_is_error_in_result(self):
        stream = _make_stream(
            {"type": "result", "result": "something went wrong", "is_error": True,
             "total_cost_usd": 0.01, "usage": {}},
        )
        r = self._parser()._parse_stream(stream, 100)
        assert r.error is True

    def test_duration_falls_back_to_wall_clock(self):
        stream = _make_stream(
            {"type": "result", "result": "ok", "is_error": False,
             "total_cost_usd": 0.01, "usage": {}},
            # no duration_ms in result object
        )
        r = self._parser()._parse_stream(stream, 9999)
        assert r.duration_ms == 9999

    def test_multiple_assistant_messages(self):
        stream = _make_stream(
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Grep", "id": "1", "input": {}},
                {"type": "tool_use", "name": "Glob", "id": "2", "input": {}},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "id": "3", "input": {}},
            ]}},
            {"type": "result", "result": "done", "is_error": False,
             "total_cost_usd": 0.03, "usage": {}},
        )
        r = self._parser()._parse_stream(stream, 100)
        assert r.tool_calls == ["Grep", "Glob", "Read"]
