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
            result_file = results_dir / f"{q_id}_{cond}.json"
            if not result_file.exists():
                print(f"  {cond:12s}  — no result file")
                continue

            metrics = score_result(result_file, logs_dir, q_data["answer_files"])
            if not metrics or metrics.get("error"):
                print(f"  {cond:12s}  — error")
                continue

            row = {"question_id": q_id, "category": q_data["category"],
                   "condition": cond, **metrics}
            all_results.append(row)

            graph_flag = ""
            if cond == "graph":
                graph_flag = f"  graph={'YES' if metrics.get('used_graph') else 'NO'}({metrics.get('graph_queries', 0)})"
            print(f"  {cond:12s}  "
                  f"R={metrics['recall']:.2f}  "
                  f"P={metrics['precision']:.2f}  "
                  f"F1={metrics['f1']:.2f}  "
                  f"reported={metrics['reported_count']}  "
                  f"TP={metrics['tp_count']}  "
                  f"FP={metrics['fp_count']}  "
                  f"FN={metrics['fn_count']}  "
                  f"cost=${metrics.get('cost_usd', '?')}"
                  f"{graph_flag}")

            if metrics["false_negatives"]:
                for fn_file in metrics["false_negatives"][:5]:
                    print(f"    MISSED: {fn_file}")
            if metrics["false_positives"]:
                for fp_file in metrics["false_positives"][:5]:
                    print(f"    EXTRA:  {fp_file}")

    if all_results:
        csv_path = results_dir / "summary.csv"
        csv_fields = [
            "question_id", "category", "condition", "recall", "precision", "f1",
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
            avg_recall = sum(r["recall"] for r in cond_results) / len(cond_results)
            avg_precision = sum(r["precision"] for r in cond_results) / len(cond_results)
            avg_f1 = sum(r["f1"] for r in cond_results) / len(cond_results)
            total_cost = sum(r.get("cost_usd", 0) or 0 for r in cond_results)
            avg_turns = sum(r.get("num_turns", 0) or 0 for r in cond_results) / len(cond_results)
            print(f"  {cond:12s}  "
                  f"avg_R={avg_recall:.2f}  "
                  f"avg_P={avg_precision:.2f}  "
                  f"avg_F1={avg_f1:.2f}  "
                  f"avg_turns={avg_turns:.1f}  "
                  f"total_cost=${total_cost:.2f}  "
                  f"n={len(cond_results)}")

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
                avg_recall = sum(r["recall"] for r in cat_cond) / len(cat_cond)
                avg_precision = sum(r["precision"] for r in cat_cond) / len(cat_cond)
                avg_f1 = sum(r["f1"] for r in cat_cond) / len(cat_cond)
                total_cost = sum(r.get("cost_usd", 0) or 0 for r in cat_cond)
                avg_turns = sum(r.get("num_turns", 0) or 0 for r in cat_cond) / len(cat_cond)
                print(f"    {cond:12s}  "
                      f"avg_R={avg_recall:.2f}  "
                      f"avg_P={avg_precision:.2f}  "
                      f"avg_F1={avg_f1:.2f}  "
                      f"avg_turns={avg_turns:.1f}  "
                      f"cost=${total_cost:.2f}  "
                      f"n={len(cat_cond)}")


if __name__ == "__main__":
    main()
