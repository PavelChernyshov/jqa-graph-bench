#!/usr/bin/env python3
"""Score a completed structural-question run against ground truth.

Usage: score.py <run-name>
Example: score.py run-0-first
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Optional

RUNS_DIR = Path(__file__).parent
HARNESS_DIR = RUNS_DIR.parent


def normalize_path(path: str) -> str:
    path = path.strip()
    repo_marker = "subjects/thingsboard/repo/"
    idx = path.find(repo_marker)
    if idx != -1:
        path = path[idx + len(repo_marker):]
    if path.startswith("./"):
        path = path[2:]
    return path


def parse_file_list(text: str) -> list[str]:
    pattern = r"RELEVANT_FILES_START\s*\n(.*?)RELEVANT_FILES_END"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return []

    files = []
    for line in match.group(1).strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 1)
        file_path = normalize_path(parts[0])
        if file_path and file_path.endswith(".java"):
            files.append(file_path)
    return files


def compute_metrics(reported: list[str], ground_truth: list[str]) -> dict:
    reported_set = set(reported)
    gt_set = set(ground_truth)

    tp = reported_set & gt_set
    fp = reported_set - gt_set
    fn = gt_set - reported_set

    recall = len(tp) / len(gt_set) if gt_set else 0
    precision = len(tp) / len(reported_set) if reported_set else 0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0

    return {
        "true_positives": sorted(tp),
        "false_positives": sorted(fp),
        "false_negatives": sorted(fn),
        "tp_count": len(tp),
        "fp_count": len(fp),
        "fn_count": len(fn),
        "recall": round(recall, 3),
        "precision": round(precision, 3),
        "f1": round(f1, 3),
        "reported_count": len(reported_set),
        "ground_truth_count": len(gt_set),
    }


def extract_tool_calls_from_log(log_file: Path) -> list[str]:
    if not log_file.exists():
        return []
    tool_calls = []
    for line in log_file.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "assistant":
            msg = obj.get("message", {})
            for block in msg.get("content", []):
                if block.get("type") == "tool_use":
                    tool_calls.append(block.get("name", ""))
    return tool_calls


def score_result(result_file: Path, logs_dir: Path, gt_files: list[str]) -> Optional[dict]:
    try:
        with open(result_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None

    if data.get("error"):
        return {"error": True, "file": str(result_file)}

    result_text = data.get("result", "")
    if not result_text:
        return {"error": True, "no_result": True, "file": str(result_file)}

    reported = parse_file_list(result_text)
    metrics = compute_metrics(reported, gt_files)

    metrics["cost_usd"] = data.get("total_cost_usd")
    metrics["duration_ms"] = data.get("duration_ms")
    metrics["num_turns"] = data.get("num_turns")
    metrics["total_input_tokens"] = data.get("usage", {}).get("input_tokens")
    metrics["total_output_tokens"] = data.get("usage", {}).get("output_tokens")

    tool_calls = data.get("tool_calls")
    if tool_calls is None:
        log_file = logs_dir / result_file.name.replace(".json", ".jsonl")
        tool_calls = extract_tool_calls_from_log(log_file)
    graph_queries = sum(1 for t in tool_calls if "graph" in t or "neo4j" in t)
    metrics["total_tool_calls"] = len(tool_calls)
    metrics["graph_queries"] = graph_queries
    metrics["used_graph"] = graph_queries > 0

    return metrics


def main():
    if len(sys.argv) < 2:
        print("Usage: score.py <run-name>")
        print()
        existing = sorted(p.name for p in RUNS_DIR.iterdir() if p.is_dir())
        if existing:
            print("Available runs:")
            for name in existing:
                print(f"  {name}")
        sys.exit(1)

    run_name = sys.argv[1]
    run_dir = RUNS_DIR / run_name
    if not run_dir.exists():
        print(f"Run not found: {run_dir}")
        sys.exit(1)

    results_dir = run_dir / "results"
    logs_dir = run_dir / "logs"

    # Ground truth: run's own data/ or fallback to harness/data/
    gt_file = run_dir / "data" / "questions.json"
    if not gt_file.exists():
        gt_file = HARNESS_DIR / "data" / "questions.json"
    with open(gt_file) as f:
        questions = json.load(f)

    conditions = ["baseline", "graph"]
    all_results = []

    for q_id, q_data in questions.items():
        print(f"\n{'='*70}")
        print(f"Q {q_id}: {q_data['question'][:70]}...")
        print(f"Category: {q_data['category']} | Ground truth: {len(q_data['answer_files'])} files")
        print(f"{'='*70}")

        for cond in conditions:
            # Find all result files for this question/condition (run 1 + _r2, _r3, ...)
            result_files = sorted(results_dir.glob(f"{q_id}_{cond}*.json"))
            # Filter out files from other conditions (e.g. baseline_r2 vs baseline)
            result_files = [f for f in result_files
                           if f.stem == f"{q_id}_{cond}"
                           or f.stem.startswith(f"{q_id}_{cond}_r")]
            if not result_files:
                print(f"  {cond:12s}  — no result file")
                continue

            run_metrics = []
            for result_file in result_files:
                metrics = score_result(result_file, logs_dir, q_data["answer_files"])
                if not metrics or metrics.get("error"):
                    print(f"  {cond:12s}  {result_file.name} — error")
                    continue
                run_metrics.append(metrics)

                row = {"question_id": q_id, "category": q_data["category"],
                       "condition": cond, "run_file": result_file.name, **metrics}
                all_results.append(row)

            if not run_metrics:
                continue

            n_runs = len(run_metrics)
            for m in run_metrics:
                graph_flag = ""
                if cond == "graph":
                    graph_flag = f"  graph={'YES' if m.get('used_graph') else 'NO'}({m.get('graph_queries', 0)})"
                print(f"  {cond:12s}  "
                      f"R={m['recall']:.2f}  "
                      f"P={m['precision']:.2f}  "
                      f"F1={m['f1']:.2f}  "
                      f"reported={m['reported_count']}  "
                      f"TP={m['tp_count']}  "
                      f"FP={m['fp_count']}  "
                      f"FN={m['fn_count']}  "
                      f"cost=${m.get('cost_usd', '?')}"
                      f"{graph_flag}")

                if m["false_negatives"]:
                    for fn_file in m["false_negatives"][:5]:
                        print(f"    MISSED: {fn_file}")
                if m["false_positives"]:
                    for fp_file in m["false_positives"][:5]:
                        print(f"    EXTRA:  {fp_file}")

            if n_runs > 1:
                avg_f1 = sum(m["f1"] for m in run_metrics) / n_runs
                std_f1 = (sum((m["f1"] - avg_f1) ** 2 for m in run_metrics) / n_runs) ** 0.5
                avg_turns = sum((m.get("num_turns") or 0) for m in run_metrics) / n_runs
                print(f"  {'':12s}  "
                      f"mean F1={avg_f1:.2f} ±{std_f1:.2f}  "
                      f"mean turns={avg_turns:.1f}  "
                      f"n={n_runs}")

    if all_results:
        csv_path = results_dir / "summary.csv"
        csv_fields = [
            "question_id", "category", "condition", "run_file",
            "recall", "precision", "f1",
            "reported_count", "ground_truth_count",
            "tp_count", "fp_count", "fn_count",
            "cost_usd", "duration_ms", "num_turns",
            "total_input_tokens", "total_output_tokens",
            "total_tool_calls", "graph_queries", "used_graph",
        ]
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_results)
        print(f"\nSummary CSV: {csv_path}")

    if all_results:
        # Aggregate by condition
        print(f"\n{'='*70}")
        print("AGGREGATE BY CONDITION")
        print(f"{'='*70}")
        for cond in conditions:
            cond_results = [r for r in all_results if r["condition"] == cond]
            if not cond_results:
                continue
            n = len(cond_results)
            avg_recall = sum(r["recall"] for r in cond_results) / n
            avg_precision = sum(r["precision"] for r in cond_results) / n
            avg_f1 = sum(r["f1"] for r in cond_results) / n
            total_cost = sum(r.get("cost_usd", 0) or 0 for r in cond_results)
            avg_turns = sum(r.get("num_turns", 0) or 0 for r in cond_results) / n
            f1_std = (sum((r["f1"] - avg_f1) ** 2 for r in cond_results) / n) ** 0.5
            f1_str = f"avg_F1={avg_f1:.2f}" + (f" ±{f1_std:.2f}" if n > 1 else "")
            print(f"  {cond:12s}  "
                  f"avg_R={avg_recall:.2f}  "
                  f"avg_P={avg_precision:.2f}  "
                  f"{f1_str}  "
                  f"avg_turns={avg_turns:.1f}  "
                  f"total_cost=${total_cost:.2f}  "
                  f"n={n}")

        # Aggregate by category
        print(f"\n{'='*70}")
        print("AGGREGATE BY CATEGORY")
        print(f"{'='*70}")
        categories = sorted(set(r["category"] for r in all_results))
        for cat in categories:
            print(f"\n  {cat}:")
            for cond in conditions:
                cat_cond = [r for r in all_results
                            if r["category"] == cat and r["condition"] == cond]
                if not cat_cond:
                    continue
                n = len(cat_cond)
                avg_recall = sum(r["recall"] for r in cat_cond) / n
                avg_precision = sum(r["precision"] for r in cat_cond) / n
                avg_f1 = sum(r["f1"] for r in cat_cond) / n
                total_cost = sum(r.get("cost_usd", 0) or 0 for r in cat_cond)
                avg_turns = sum(r.get("num_turns", 0) or 0 for r in cat_cond) / n
                f1_std = (sum((r["f1"] - avg_f1) ** 2 for r in cat_cond) / n) ** 0.5
                f1_str = f"avg_F1={avg_f1:.2f}" + (f" ±{f1_std:.2f}" if n > 1 else "")
                print(f"    {cond:12s}  "
                      f"avg_R={avg_recall:.2f}  "
                      f"avg_P={avg_precision:.2f}  "
                      f"{f1_str}  "
                      f"avg_turns={avg_turns:.1f}  "
                      f"cost=${total_cost:.2f}  "
                      f"n={n}")

        print_per_question_summary(all_results, conditions)
        print_cost_summary(all_results, conditions)


def _agg(values: list[float]) -> tuple[float, float, float, float]:
    """Return mean, std, min, max for a list of floats."""
    n = len(values)
    mean = sum(values) / n
    std = (sum((v - mean) ** 2 for v in values) / n) ** 0.5
    return mean, std, min(values), max(values)


def print_per_question_summary(all_results: list[dict], conditions: list[str]):
    """Print a compact per-question summary table with mean, std, turn spread."""
    from collections import defaultdict

    groups = defaultdict(list)
    for r in all_results:
        groups[(r["question_id"], r["condition"])].append(r)

    print(f"\n{'='*70}")
    print("PER-QUESTION SUMMARY")
    print(f"{'='*70}")
    print(f"  {'question':12s} {'cond':10s} {'n':>3s} "
          f"{'F1':>8s} {'±std':>6s} "
          f"{'R':>6s} {'P':>6s} "
          f"{'turns':>6s} {'spread':>8s} "
          f"{'$/run':>7s}")
    print(f"  {'-'*68}")

    prev_qid = None
    for (qid, cond), rs in sorted(groups.items()):
        if prev_qid and prev_qid != qid:
            print()
        prev_qid = qid

        n = len(rs)
        f1_mean, f1_std, _, _ = _agg([r["f1"] for r in rs])
        r_mean = sum(r["recall"] for r in rs) / n
        p_mean = sum(r["precision"] for r in rs) / n
        turns = [r.get("num_turns") or 0 for r in rs]
        t_mean, _, t_min, t_max = _agg(turns)
        costs = [r.get("cost_usd") or 0 for r in rs]
        c_mean = sum(costs) / n

        std_str = f"±{f1_std:.2f}" if n > 1 else ""
        spread_str = f"{t_min:.0f}–{t_max:.0f}" if n > 1 else f"{t_mean:.0f}"

        print(f"  {qid:12s} {cond:10s} {n:3d} "
              f"{f1_mean:8.3f} {std_str:>6s} "
              f"{r_mean:6.3f} {p_mean:6.3f} "
              f"{t_mean:6.1f} {spread_str:>8s} "
              f"${c_mean:6.4f}")


def print_cost_summary(all_results: list[dict], conditions: list[str]):
    """Print per-condition cost summary."""
    print(f"\n{'='*70}")
    print("COST SUMMARY")
    print(f"{'='*70}")
    for cond in conditions:
        cr = [r for r in all_results if r["condition"] == cond]
        if not cr:
            continue
        n = len(cr)
        costs = [r.get("cost_usd") or 0 for r in cr]
        total = sum(costs)
        per_run = total / n
        print(f"  {cond:12s}  total=${total:.2f}  per_run=${per_run:.4f}  n={n}")


if __name__ == "__main__":
    main()
