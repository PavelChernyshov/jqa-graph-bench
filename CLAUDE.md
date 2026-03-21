# CLAUDE.md

## What this is

Benchmark harness that measures whether a code graph improves AI agent accuracy and efficiency on structural questions about a large Java codebase.

Subject: ThingsBoard (528K lines, 14 Maven modules, Spring Boot).
Graph: jQAssistant bytecode extraction → Neo4j (15.6K types, 124K methods).
Agent: Claude via `claude -p`, two conditions — file-only vs file + graph MCP tools.

## Structure

- `mcp-server/` — MCP server (FastMCP + neo4j driver). Tools: `find_types`, `get_type_context`, `query_graph`, `get_graph_schema`
- `harness/` — Structural question benchmark. `run.py` runs questions x conditions, results in `runs/`
- `harness-tier1/` — PR-level file discovery benchmark (same structure)
- `subjects/thingsboard/` — Target codebase (git submodule), jQAssistant config, scan script

## Running

```bash
# Build graph
cd subjects/thingsboard && ./jqa-scan.sh scan && ./jqa-scan.sh analyze && ./jqa-scan.sh server

# Run benchmark
cd harness && python run.py              # all questions
cd harness && python run.py impl-001 graph  # single question + condition
```

## Graph access

Neo4j at bolt://localhost:7687 (started via `jqa-scan.sh server`). MCP config in `.mcp.json` (gitignored, local).
