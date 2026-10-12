#!/usr/bin/env python3
"""Compare two eval report JSON files (baseline vs new).

Prints metric differences overall and per question type, plus the
questions that got better and the ones that got worse.

Run:
    .venv/bin/python evals/scripts/compare_reports.py \\
        evals/reports/report_baseline_<ts>.json evals/reports/report_new_<ts>.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def load_report(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"No report at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_delta(new, old, higher_is_better=True):
    if new is None or old is None:
        return "n/a"
    d = new - old
    sign = "+" if d > 0 else ""
    arrow = ""
    if d != 0:
        arrow = " ↑" if (d > 0) == higher_is_better else " ↓"
    return f"{old} -> {new} ({sign}{round(d, 4)}{arrow})"


def _pct(v):
    return f"{round(v * 100, 1)}%" if isinstance(v, (int, float)) else "n/a"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", help="baseline report JSON")
    parser.add_argument("new", help="new report JSON")
    args = parser.parse_args()

    base = load_report(Path(args.baseline))
    new = load_report(Path(args.new))
    bs, ns = base["summary"], new["summary"]

    print(f"Baseline: {base['run_info']['label']} ({base['run_info']['timestamp']})")
    print(f"New:      {new['run_info']['label']} ({new['run_info']['timestamp']})")
    print()

    print("== Overall ==")
    rows = [
        ("Questions", f"{bs['counts']['total']} -> {ns['counts']['total']}", None),
        ("Recall @ HNSW", _fmt_delta(ns["recall"]["at_hnsw"], bs["recall"]["at_hnsw"]), None),
        ("Recall @ MMR", _fmt_delta(ns["recall"]["at_mmr"], bs["recall"]["at_mmr"]), None),
        ("Recall @ final", _fmt_delta(ns["recall"]["at_final"], bs["recall"]["at_final"]), None),
        ("All expected in final",
         _fmt_delta(ns["recall"]["all_expected_in_final"], bs["recall"]["all_expected_in_final"]), None),
        ("At least one in final",
         _fmt_delta(ns["recall"]["at_least_one_in_final"],
                   bs["recall"]["at_least_one_in_final"]), None),
        ("MRR", _fmt_delta(ns["mrr"], bs["mrr"]), None),
        ("Refusal rate (unanswerable)",
         _fmt_delta(ns["refusal_rate"], bs["refusal_rate"], higher_is_better=False), None),
        ("Latency avg (ms)",
         _fmt_delta(ns["latency_ms"]["avg"], bs["latency_ms"]["avg"], higher_is_better=False), None),
        ("Latency p95 (ms)",
         _fmt_delta(ns["latency_ms"]["p95"], bs["latency_ms"]["p95"], higher_is_better=False), None),
        ("Cost pipeline ($)",
         _fmt_delta(ns["cost_usd"]["pipeline"], bs["cost_usd"]["pipeline"], higher_is_better=False), None),
        ("Lost at MMR",
         _fmt_delta(ns["lost_at_stage"]["lost_at_mmr"],
                    bs["lost_at_stage"]["lost_at_mmr"], higher_is_better=False), None),
        ("Lost at reranker",
         _fmt_delta(ns["lost_at_stage"]["lost_at_reranker"],
                    bs["lost_at_stage"]["lost_at_reranker"], higher_is_better=False), None),
    ]
    for name, val, _ in rows:
        print(f"  {name:<28} {val}")

    print()
    print("== Per question type (recall @ final / MRR) ==")
    types = sorted(set(bs["by_type"]) | set(ns["by_type"]))
    for t in types:
        b = bs["by_type"].get(t, {})
        n = ns["by_type"].get(t, {})
        print("  {:<16} n {} -> {}  recall {}  mrr {}".format(
            t, b.get("n", 0), n.get("n", 0),
            _fmt_delta(n.get("recall_final"), b.get("recall_final")),
            _fmt_delta(n.get("mrr"), b.get("mrr"))))

    # Per-question better/worse, keyed on question_id.
    print()
    print("== Questions that got better / worse ==")
    bq = {q["question_id"]: q for q in base["questions"]}
    nq = {q["question_id"]: q for q in new["questions"]}

    def score(q):
        m = q.get("metrics") or {}
        if q.get("expected_behavior") != "answer":
            return (1 if q.get("refusal") else 0,)
        return (1 if m.get("any_in_final") else 0, m.get("mrr") or 0.0)

    better, worse = [], []
    for qid in sorted(set(bq) & set(nq)):
        bs_score, ns_score = score(bq[qid]), score(nq[qid])
        if ns_score > bs_score:
            better.append(qid)
        elif ns_score < bs_score:
            worse.append(qid)

    if better:
        print(f"  Better ({len(better)}):")
        for qid in better:
            print(f"    + {qid}  {nq[qid]['question'][:70]}")
    else:
        print("  Better: (none)")
    if worse:
        print(f"  Worse ({len(worse)}):")
        for qid in worse:
            print(f"    - {qid}  {nq[qid]['question'][:70]}")
    else:
        print("  Worse: (none)")

    only_new = sorted(set(nq) - set(bq))
    only_base = sorted(set(bq) - set(nq))
    if only_new:
        print(f"  Only in new run: {', '.join(only_new)}")
    if only_base:
        print(f"  Only in baseline run: {', '.join(only_base)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())