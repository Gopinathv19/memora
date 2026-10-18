#!/usr/bin/env python3
"""Report how many questions of an eval set plain BM25 already solves.

READ-ONLY against the database. Prints, per query type, the share of questions
whose supporting chunks are in the BM25 top k (`any` / `all`, see
`evals/solvability.py`). Use it before reading a Graph RAG benchmark: questions
with a high `all` rate do not separate graph from vector retrieval.

    .venv/bin/python evals/scripts/lexical_solvability.py \\
        --eval-set evals/data/graph_eval_set_draft.jsonl --tenant-id <uuid> [--k 5]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("API_SECRET", "solvability-report")

from app.db.database import SessionLocal  # noqa: E402
from evals.solvability import solvability  # noqa: E402
from generate_graph_eval_set import load_chunks  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--eval-set", required=True)
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--subject-id")
    p.add_argument("--k", type=int, default=5)
    args = p.parse_args()

    questions = [json.loads(line) for line in Path(args.eval_set).read_text().splitlines() if line.strip()]
    with SessionLocal() as db:
        chunks, _ = load_chunks(
            db, uuid.UUID(args.tenant_id), uuid.UUID(args.subject_id) if args.subject_id else None,
            re.compile(r"$^"),  # never exclude: the supporting chunks were vetted when the set was made
        )
    report = solvability(questions, {c["id"]: c["content"] for c in chunks}, args.k)
    print(f"BM25 top-{report['k']} over {len(chunks)} chunks; scored {report['scored']} questions "
          f"({report['stale_support']} skipped: supporting chunk no longer exists)")
    print(f"{'query_type':<16}{'n':>4}{'any':>8}{'all':>8}")
    for qtype, v in report["by_type"].items():
        print(f"{qtype:<16}{v['n']:>4}{v['any']:>8.0%}{v['all']:>8.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
