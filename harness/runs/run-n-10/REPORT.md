# Run n-10: Structural Question Benchmark — n=10

Same 10 questions and 2 conditions as run 1-first, repeated 10 times each. 199 scored runs (1 graph run errored out). Purpose: measure stochastic variation.

## Setup

Same as run 1-first:
- **Model**: Sonnet
- **Budget**: $0.30/run
- **Conditions**: baseline (Read/Glob/Grep), graph (+ MCP code-graph tools)
- **Isolation**: `--setting-sources ""`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `--strict-mcp-config`
- **Runs per condition**: 10 (200 total, 199 scored)
- **Total cost**: $8.74 ($5.12 baseline + $3.62 graph)

## Results

### Aggregate

| Condition | Avg F1 | ±std | Avg Turns | Cost/run | n |
|-----------|--------|------|-----------|----------|---|
| baseline  | 0.85   | 0.16 | 7.6       | $0.051   | 100 |
| graph     | 0.98   | 0.04 | 4.4       | $0.037   | 99 |

### By Category

| Category | Condition | Avg F1 | ±std | Avg Turns | n |
|----------|-----------|--------|------|-----------|---|
| implementation_discovery | baseline | 0.94 | 0.09 | 3.6 | 30 |
| implementation_discovery | graph | 0.99 | 0.05 | 3.4 | 30 |
| blast_radius | baseline | 0.90 | 0.05 | 2.5 | 40 |
| blast_radius | graph | 0.99 | 0.03 | 3.5 | 39 |
| dependency_chain | baseline | 0.68 | 0.16 | 18.5 | 30 |
| dependency_chain | graph | 0.97 | 0.05 | 6.4 | 30 |

### Per Question

| Question | Cond | Mean F1 | ±std | Mean R | Mean P | Mean Turns | Turn spread |
|----------|------|---------|------|--------|--------|------------|-------------|
| impl-001 | baseline | 0.87 | 0.12 | 1.00 | 0.80 | 4.6 | 3–7 |
| impl-001 | graph | 0.97 | 0.08 | 1.00 | 0.95 | 3.3 | 3–5 |
| impl-002 | baseline | 0.99 | 0.05 | 1.00 | 0.97 | 3.1 | 3–4 |
| impl-002 | graph | 1.00 | 0.00 | 1.00 | 1.00 | 3.0 | 3–3 |
| impl-003 | baseline | 0.97 | 0.04 | 0.94 | 1.00 | 3.0 | 3–3 |
| impl-003 | graph | 1.00 | 0.00 | 1.00 | 1.00 | 4.0 | 3–5 |
| blast-001 | baseline | 0.82 | 0.03 | 1.00 | 0.70 | 4.0 | 2–5 |
| blast-001 | graph | 0.97 | 0.06 | 1.00 | 0.95 | 3.8 | 3–5 |
| blast-002 | baseline | 0.94 | 0.03 | 1.00 | 0.88 | 2.1 | 2–3 |
| blast-002 | graph | 1.00 | 0.00 | 1.00 | 1.00 | 3.7 | 3–4 |
| blast-003 | baseline | 0.92 | 0.03 | 1.00 | 0.85 | 2.0 | 2–2 |
| blast-003 | graph | 0.99 | 0.02 | 1.00 | 0.97 | 3.5 | 3–5 |
| blast-004 | baseline | 0.93 | 0.01 | 1.00 | 0.86 | 2.0 | 2–2 |
| blast-004 | graph | 0.99 | 0.03 | 1.00 | 0.98 | 3.2 | 3–4 |
| chain-001 | baseline | 0.50 | 0.00 | 0.33 | 1.00 | 11.3 | 11–12 |
| chain-001 | graph | 0.95 | 0.06 | 0.95 | 0.95 | 5.9 | 4–12 |
| chain-002 | baseline | 0.89 | 0.03 | 0.88 | 0.92 | 23.8 | 22–30 |
| chain-002 | graph | 1.00 | 0.01 | 1.00 | 0.99 | 7.7 | 5–10 |
| chain-003 | baseline | 0.64 | 0.00 | 0.53 | 0.80 | 20.5 | 16–29 |
| chain-003 | graph | 0.97 | 0.05 | 0.97 | 0.97 | 5.7 | 4–10 |

## Observations

### 1. Baseline results are more stable than expected on some questions, unstable on others

chain-001 baseline: F1=0.50 with std=0.00 across all 10 runs. The agent consistently finds only 2 of 7 services (the two declared directly on DashboardController), decides the inherited ones are "not direct field dependencies," and stops. This is a deterministic reasoning failure, not stochastic variation.

chain-003 baseline: same pattern — F1=0.64, std=0.00. Finds 8 of 15, misses inherited services every time.

chain-002 baseline: F1=0.89, std=0.03, but turns range from 22 to 30. The agent reaches the same answer through different exploration paths — accuracy is stable, cost is not.

### 2. Stochastic variation is concentrated in precision, not recall

Baseline recall is stable per question (std usually 0.00). What varies is how many false positives the agent includes — subclasses, interface definitions, related-but-not-matching files. This shows up as precision variance (impl-001 baseline precision ranges from 0.58 to 1.00 across runs).

### 3. Graph stddev is 4x lower than baseline

Graph F1 std=0.04 vs baseline std=0.16. The remaining graph variance comes from the path hallucination artifact: the agent sometimes "corrects" the real `entitiy` path to `entitiary` or `entitiar`. This accounts for 7 of 30 chain/graph runs scoring below 1.00. Without it, graph chain F1 would be ~1.00 across all runs.

### 4. n=10 confirms the run-1 signal

Run 1 (n=1): baseline F1=0.80, graph F1=0.98.
Run n-10 (n=10): baseline F1=0.85±0.16, graph F1=0.98±0.04.

The means are consistent. Baseline moved from 0.80 to 0.85 — within the stddev. Graph stayed at 0.98.

### 5. Dependency chains are deterministically hard for baseline

The gap is not stochastic. Baseline fails on chain questions the same way every time: it reads the controller, finds directly declared fields, and either stops or takes 20+ turns to partially discover inherited dependencies. The graph returns all dependencies (including inherited) in one query.

### 6. Cost difference is smaller than run-1 suggested

Run-1 showed 53% cost savings. At n=10, it's 29% ($0.037 vs $0.051 per run). The baseline is cheaper on simple questions (blast-003/004 at 2 turns) while the graph uses 3-4 turns. The savings concentrate on dependency chains where baseline burns 18+ turns.

## Comparison with run 1-first

| Metric | Run 1 (n=1) | Run n-10 (n=10) |
|--------|-------------|-----------------|
| Baseline F1 | 0.80 | 0.85 ±0.16 |
| Graph F1 | 0.98 | 0.98 ±0.04 |
| Baseline turns | 7.4 | 7.6 |
| Graph turns | 3.7 | 4.4 |
| Cost/run baseline | $0.123 | $0.051 |
| Cost/run graph | $0.058 | $0.037 |

Cost per run dropped for both conditions. Likely model pricing or efficiency changes between runs, not a methodology difference.

## Next

1. Fix path hallucination — add prompt instruction to reproduce paths exactly as received. This would close the remaining graph variance.
2. Consider separating chain-001/chain-003 baseline failure into its own finding — it's a reasoning limitation (agent won't read base class), not a search limitation.
3. Add questions where graph and baseline should perform equally (single-file lookups, annotation queries) to establish a baseline floor.
