# Code Graph Benchmark

AI coding agents navigate codebases with text search — grep, glob, file reads. This works for most tasks, but fails on others: text search can't follow type hierarchies across modules, resolve which interface a field refers to, or find inherited dependencies. These failures are deterministic, not stochastic — the agent produces wrong results the same way every time.

The problem isn't accuracy on individual tasks. It's predictability. If you can't tell in advance which tasks the agent will fail on, you have to review everything. The cost of an agent isn't tokens — it's the human attention required to verify its output. Reliable structural understanding is the prerequisite for delegation.

This benchmark measures where text search breaks down and whether a pre-built code graph makes agent behavior more predictable.

## What this is

An experiment harness that runs Claude against structural questions about [ThingsBoard](https://github.com/thingsboard/thingsboard) (528K lines Java, 14 Maven modules, Spring Boot) under two conditions:

- **baseline** — file tools only (Read, Glob, Grep)
- **graph** — file tools + a Neo4j code graph via MCP (15.6K types, 124K methods, call/inheritance/dependency edges extracted from bytecode by [jQAssistant](https://jqassistant.github.io/jqassistant/current/))

Questions cover 3 categories: find all implementations of an interface, find all callers of a method, find all service dependencies of a controller.

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
