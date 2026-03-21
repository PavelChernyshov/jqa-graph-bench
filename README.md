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

See [harness/runs/](harness/runs/) for all runs and reports.

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
