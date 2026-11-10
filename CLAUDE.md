# CLAUDE.md

## What this is

Benchmark harness that measures whether a code graph improves AI agent accuracy and efficiency on structural questions about a large Java codebase.

Subject: ThingsBoard (528K lines, 14 Maven modules, Spring Boot).
Graph: jQAssistant bytecode extraction → Neo4j (15.6K types, 124K methods).
Agent: Pluggable runners (Claude Code, etc.), configurable conditions — file-only vs file + graph MCP tools.

## Structure

- `harness/` — Benchmark orchestrator (runner-agnostic)
  - `run.py` — main entry point, loops questions × conditions × models
  - `score.py`, `trace.py`, `archive.py` — analysis tools
  - `runners/` — pluggable backends (Claude Code, etc.)
  - `tests/` — pytest suite
- `bench/` — Benchmark definition and output
  - `questions.json` — structural questions with ground truth
  - `conditions.json` — condition definitions (tools, prompts per condition)
  - `prompts/` — system prompt fragments
  - `runs/` — archived experiment runs
  - `results/`, `logs/` — working directories (gitignored)
- `mcp-server/` — MCP server (FastMCP + neo4j driver). Tools: `find_types`, `get_type_context`, `query_graph`, `get_graph_schema`
- `subjects/thingsboard/` — Target codebase (git submodule), jQAssistant config, scan script

## Running

```bash
# Build graph
cd subjects/thingsboard && ./jqa-scan.sh scan && ./jqa-scan.sh analyze && ./jqa-scan.sh server

# Run benchmark (from project root)
python harness/run.py                              # all questions, default model
python harness/run.py impl-001 graph --model opus  # single question, specific model
python harness/run.py --runner claude --model haiku # different model

# Score and trace archived runs
python harness/score.py 1-first
python harness/trace.py 1-first

# Archive current results
python harness/archive.py my-run-name

# Run tests
cd harness && uv run --with pytest pytest tests/ -v
```

## Graph access

Neo4j at bolt://localhost:7687 (started via `jqa-scan.sh server`). MCP config in `.mcp.json` (gitignored, local).
