# Runs

| Run | n | Baseline F1 | Graph F1 | Notes |
|-----|---|-------------|----------|-------|
| [1-first](1-first/REPORT.md) | 1 | 0.80 | 0.98 | First run. 10 questions, 2 conditions. |
| [run-n-10](run-n-10/REPORT.md) | 10 | 0.85 ±0.16 | 0.98 ±0.04 | Replication. Baseline failures on chain questions are deterministic (std=0.00), not stochastic. Graph variance comes from path hallucination artifact. |
