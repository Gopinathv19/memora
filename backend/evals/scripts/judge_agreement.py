#!/usr/bin/env python3
"""Check the benchmark's LLM judge against your own grades.

Two steps. READ-ONLY against the database.

1. Make a labelling file from a finished benchmark run: a random sample of its
   answerable (question, pipeline) answers, each with the reference answer and an
   empty `human_score`.

       .venv/bin/python evals/scripts/judge_agreement.py make --run-id <uuid> --n 40 --out evals/data/judge_labels.jsonl

2. Fill `human_score` by hand (1 = states the same facts as the reference,
   0.5 = partly right or missing a piece, 0 = wrong, unrelated, or says the
   information is unavailable), then score the judge against you:

       .venv/bin/python evals/scripts/judge_agreement.py score --labels evals/data/judge_labels.jsonl

   The judge is `GRAPHRAG_JUDGE_MODEL`; run it once per candidate model to pick one.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("API_SECRET", "judge-agreement")

from app.db.database import SessionLocal  # noqa: E402
from app.db.models import BenchmarkRun  # noqa: E402
from app.graphrag.benchmark import judge, judge_info  # noqa: E402
from evals.agreement import compare  # noqa: E402


def make(args) -> int:
    with SessionLocal() as db:
        run = db.get(BenchmarkRun, uuid.UUID(args.run_id))
        if run is None:
            print("no such benchmark run")
            return 1
        results = run.results or []
    items = [
        {"query": q["query"], "expected_answer": q["expected_answer"], "pipeline": key,
         "answer": row["answer"], "human_score": None}
        for q in results if q.get("expected_answer") and not q.get("is_unanswerable")
        for key, row in q["pipelines"].items() if row.get("status") == "ok" and row.get("answer")
    ]
    random.Random(args.seed).shuffle(items)
    items = items[: args.n]
    Path(args.out).write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
    print(f"wrote {len(items)} items to {args.out}; fill human_score (0, 0.5 or 1) and run `score`")
    return 0


def score(args) -> int:
    items = [json.loads(line) for line in Path(args.labels).read_text().splitlines() if line.strip()]
    human = [i.get("human_score") for i in items]
    if any(h is not None and h not in (0, 0.5, 1) for h in human):
        print("human_score must be 0, 0.5 or 1")
        return 1
    judged = [judge(i["query"], i["expected_answer"], i["answer"])[0] for i in items]
    report = compare([None if h is None else float(h) for h in human], judged)
    print(f"judge {judge_info()['judge_model']} (same as answer model: {judge_info()['judge_same_as_answer']})")
    print(json.dumps(report, indent=2))
    if report["n"] < 30:
        print("note: under 30 labelled pairs; treat kappa as indicative only")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--run-id", required=True)
    m.add_argument("--n", type=int, default=40)
    m.add_argument("--seed", type=int, default=42)
    m.add_argument("--out", required=True)
    m.set_defaults(fn=make)
    s = sub.add_parser("score")
    s.add_argument("--labels", required=True)
    s.set_defaults(fn=score)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
