# Code Graph Benchmark

Measures whether a pre-built code graph helps an AI agent answer structural questions about a large codebase compared to file-only tools (Read/Glob/Grep).

## What this is

An experiment harness that runs Claude against structural questions about [ThingsBoard](https://github.com/thingsboard/thingsboard) (528K lines Java, 14 Maven modules, Spring Boot) under two conditions:

- **baseline** — file tools only (Read, Glob, Grep)
- **graph** — file tools + MCP server backed by a Neo4j code graph (15.6K types, 124K methods, call/inheritance/dependency edges)

The graph is extracted from compiled bytecode by [jQAssistant](https://jqassistant.github.io/jqassistant/current/). The MCP server exposes 4 tools: `find_types`, `get_type_context`, `query_graph`, `get_graph_schema`.

Questions cover 3 categories: implementation discovery ("what implements X?"), blast radius ("what calls method Y?"), and dependency chains ("what services does controller Z depend on?").

Each run is isolated — no memory, no user settings, strict MCP config.

## Results

See [harness/runs/1-first/REPORT.md](harness/runs/1-first/REPORT.md) for the latest benchmark results.

| Condition | Avg F1 | Avg Turns | Total Cost |
|-----------|--------|-----------|------------|
| baseline  | 0.80   | 7.4       | $1.23      |
| graph     | 0.98   | 3.7       | $0.58      |

n=1 per condition. 10 questions, 20 total runs.

## Setup

### Prerequisites

- Python 3.10+, [uv](https://github.com/astral-sh/uv)
- [jQAssistant CLI](https://jqassistant.github.io/jqassistant/current/) (set `JQA_BIN` or install via SDKMAN)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude`)
- Java 17+ (for building ThingsBoard)

### 1. Clone and build the subject

```bash
git submodule update --init subjects/thingsboard/repo
cd subjects/thingsboard/repo
mvn package -DskipTests -pl '!ui-ngx'
```

### 2. Build the graph

```bash
cd subjects/thingsboard

# Scan compiled JARs into Neo4j embedded store
./jqa-scan.sh scan

# Run analysis (cross-module type resolution, dependency edges)
./jqa-scan.sh analyze

# Start Neo4j (bolt://localhost:7687, browser at http://localhost:7474)
./jqa-scan.sh server
```

### 3. Install MCP server dependencies

```bash
cd mcp-server
uv sync
```

### 4. Run the benchmark

```bash
cd harness

# Run all questions under all conditions
python run.py

# Run a single question under a single condition
python run.py impl-001 baseline
python run.py chain-002 graph
```

Results go to `harness/results/`. Logs go to `harness/logs/`.

## Structure

```
mcp-server/          MCP server (Python, FastMCP + neo4j driver)
  server.py          4 tools: find_types, get_type_context, query_graph, get_graph_schema

harness/             Structural question benchmark
  run.py             Runner — loops questions x conditions, calls claude -p
  data/              questions.json — 10 questions with answer_files and verification Cypher
  prompts/           system_base.md, system_graph.md — agent instructions per condition
  results/           Output JSON per run
  runs/1-first/      Analysis and report for run 1

subjects/thingsboard/
  repo/              ThingsBoard source (git submodule)
  .jqassistant.yml   jQAssistant config (embedded Neo4j store, analysis concepts)
  jqa-scan.sh        CLI wrapper: scan, analyze, server, reset
```
