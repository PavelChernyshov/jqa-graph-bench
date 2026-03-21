# Run 1-first: Structural Question Benchmark

10 questions across 3 categories (implementation discovery, blast radius, dependency chain), 2 conditions (baseline file-only vs graph-augmented), n=1 per condition. 528K-line Java codebase (ThingsBoard).

## Setup

- **Model**: Sonnet
- **Budget**: $0.30/run
- **Conditions**: baseline (Read/Glob/Grep), graph (+ MCP code-graph tools)
- **Isolation**: `--setting-sources ""`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `--strict-mcp-config`
- **Questions**: 10 (3 impl discovery, 4 blast radius, 3 dependency chain)
- **Total runs**: 20 (10 questions x 2 conditions)

## Results

### Aggregate

| Condition | Avg Recall | Avg Precision | Avg F1 | Avg Turns | Total Cost |
|-----------|-----------|--------------|--------|-----------|------------|
| baseline  | 0.87      | 0.79         | 0.80   | 7.4       | $1.23      |
| graph     | 0.99      | 0.98         | 0.98   | 3.7       | $0.58      |

### By Category

| Category | Condition | Avg R | Avg P | Avg F1 | Avg Turns | Cost |
|----------|-----------|-------|-------|--------|-----------|------|
| implementation_discovery | baseline | 0.95 | 0.71 | 0.81 | 3.7 | $0.26 |
| implementation_discovery | graph | 1.00 | 1.00 | 1.00 | 3.0 | $0.17 |
| blast_radius | baseline | 1.00 | 0.81 | 0.89 | 3.0 | $0.39 |
| blast_radius | graph | 1.00 | 0.98 | 0.99 | 3.5 | $0.19 |
| dependency_chain | baseline | 0.60 | 0.83 | 0.67 | 17.0 | $0.59 |
| dependency_chain | graph | 0.96 | 0.96 | 0.96 | 4.7 | $0.22 |

### Per Question

| Question | Baseline F1 | Graph F1 | B turns | G turns | Turn ratio | Cost saved |
|----------|------------|----------|---------|---------|------------|------------|
| impl-001 | 0.78 | 1.00 | 4 | 3 | 1.3x | 56% |
| impl-002 | 0.85 | 1.00 | 4 | 3 | 1.3x | 1% |
| impl-003 | 0.80 | 1.00 | 3 | 3 | 1.0x | 51% |
| blast-001 | 0.80 | 1.00 | 5 | 4 | 1.2x | 65% |
| blast-002 | 0.94 | 1.00 | 3 | 3 | 1.0x | 61% |
| blast-003 | 0.91 | 1.00 | 2 | 3 | 0.7x | 55% |
| blast-004 | 0.92 | 0.96 | 2 | 4 | 0.5x | 10% |
| chain-001 | 0.30 | 1.00 | 8 | 5 | 1.6x | 63% |
| chain-002 | 1.00 | 1.00 | 26 | 5 | 5.2x | 62% |
| chain-003 | 0.72 | 0.87 | 17 | 4 | 4.2x | 64% |

## Agent Behavior

### Baseline

**Implementation discovery**: Grep for `implements InterfaceName`. Works for directly named classes. Fails when the target interface isn't first in a multi-interface declaration — impl-003 missed `DefaultNotificationCenter` because it declares `implements NotificationCenter, NotificationChannel<User, ...>`, and grep for `implements NotificationChannel` doesn't match.

**Blast radius**: Grep for method name across codebase. Consistently includes interface definitions and their direct implementations as false positives when asked only for callers.

**Dependency chain**: Read the controller, find `@Autowired` fields, grep for each service. Struggles with inherited dependencies:
- chain-001: Found 2 of 7 services. Saw references to inherited services but excluded them as "not direct field dependencies." Never read the base class. Also hallucinated `entitiary` for the real `entitiy` directory → R=0.20.
- chain-002: Found all 6 services but took 26 turns of iterative grep→read→grep. Graph got the same result in 5 turns.
- chain-003: Found 9 of 15 files after 17 turns. Missed inherited services.

### Graph

**Implementation discovery**: `find_types` → `query_graph(MATCH (t)-[:IMPLEMENTS]->(i) ...)` — 2 calls, correct every time.

**Blast radius**: `find_types` → `get_type_context(include=["callers"])` — 2-3 calls.

**Dependency chain**: `find_types` → `get_type_context(include=["dependencies"])` → `query_graph(MATCH (impl)-[:IMPLEMENTS]->(iface) ...)` — 3-4 calls. Returns all dependencies including inherited.

### Core difference

Baseline reads a file then greps to follow references — an iterative loop that expands with each discovery. Graph returns a complete relationship list in one query.

## Observations

1. **Precision gap**: Graph avoids false positives that grep produces (interface definitions counted as callers, partial name matches). Recall gap comes from inherited dependencies that baseline doesn't traverse.

2. **Dependency chains have the largest gap** (F1 0.67→0.96, turns 17→4.7). The 2-hop traversal (controller→interface→implementation) maps directly to graph queries but requires iterative discovery with grep.

3. **Grep has a structural blind spot on multi-interface declarations.** `implements A, B` — grepping for `implements B` won't match. This is a property of Java syntax, not a grep limitation per se. Any structured parser (tree-sitter, bytecode) would handle it.

4. **Path hallucination is a measurement problem.** ThingsBoard has a real typo: `service.entitiy`. The agent sometimes "corrects" this to `entitiary` in output. Nondeterministic — appeared in both conditions. Without it, graph would score F1=1.00 on all chain questions.

5. **n=1 per condition.** Variation is visible — chain-001 baseline got R=0.20 while chain-002 got 1.00 on a similar task. Results need replication.
