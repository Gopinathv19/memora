#!/usr/bin/env python3
"""Compare vector retrieval with the unified graph retrieval on an eval set, retrieval only.

For every question it runs the vector pipeline's own candidate search plus the shared reranker (the baseline),
and the unified pipeline in three settings: question analyser on, analyser off (rules only), and fixed channel
weights. No answer is written, so this is fast and cheap, and it records NOTHING in the database: the standard
benchmark (`POST /compare/benchmarks`) writes run rows into Postgres, this does not.

Reads Postgres (read-only) and a FalkorDB you name with `--falkordb-url`. It refuses the FalkorDB configured in
`.env` unless `--allow-configured-falkordb` is given, so a trial run cannot touch the real graph.
Writes one JSON file. The analyser's model call is the only LLM use (set LLM_CACHE_ENABLED=false so it is not cached).

    API_SECRET=x LLM_CACHE_ENABLED=false .venv/bin/python evals/scripts/offline_graph_benchmark.py \\
        --eval-set evals/data/a.jsonl --eval-set evals/data/b.jsonl --tenant-id <uuid> \\
        --falkordb-url redis://localhost:6390 --prefix memora_e2e --out evals/data/offline_benchmark.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("API_SECRET", "offline-benchmark")

from falkordb import FalkorDB  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.graph.store import FalkorGraphStore  # noqa: E402
from app.graphrag import pipelines, unified  # noqa: E402
from app.graphrag.params import RetrievalParams  # noqa: E402
from app.graphrag.store import GraphRagStore  # noqa: E402
from app.llm.client import get_llm_client  # noqa: E402
from app.services.scope import Scope  # noqa: E402
from app.graphrag.abstain import is_abstention  # noqa: E402
from evals.offline_benchmark import summarize, summarize_answers  # noqa: E402

VARIANTS = {
    "unified": RetrievalParams(unified_analyser=True),
    "unified_rules": RetrievalParams(unified_analyser=False),
    "unified_fixed": RetrievalParams(unified_analyser=False, unified_weights={"vector": 1.0, "atoms": 1.0, "walk": 0.6}),
}


def load_questions(paths: list[str]) -> list[dict]:
    out = []
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                q = json.loads(line)
                q["is_unanswerable"] = bool(q.get("is_unanswerable")) or q.get("query_type") == "unanswerable"
                out.append(q)
    return out


def row(qid, q, chunks, evidence, ms):
    return {"qid": qid, "query_type": q.get("query_type", "factoid"), "is_unanswerable": q["is_unanswerable"],
            "relevant_ids": list(q.get("supporting_chunk_ids") or []), "retrieved_ids": [str(c.chunk_id) for c in chunks],
            "abstained": bool(evidence["weak"]), "best_rerank": evidence.get("best_rerank"), "ms": ms}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--eval-set", action="append", required=True)
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--subject-id")
    p.add_argument("--falkordb-url", required=True)
    p.add_argument("--allow-configured-falkordb", action="store_true")
    p.add_argument("--prefix", default="memora")
    p.add_argument("--k", type=int, default=6)
    p.add_argument("--limit", type=int)
    p.add_argument("--answers", action="store_true",
                   help="also run the full answer step (one model call per answerable question) for the rules-only "
                        "unified setting and measure refusals, citations and checks; costs a few cents")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    settings = get_settings()
    if settings.falkordb_url and args.falkordb_url.rstrip("/") == settings.falkordb_url.rstrip("/") and not args.allow_configured_falkordb:
        print("refusing: --falkordb-url is the FalkorDB configured in .env; pass --allow-configured-falkordb if that is intended")
        return 2
    tenant_id = uuid.UUID(args.tenant_id)
    base = FalkorGraphStore(FalkorDB.from_url(args.falkordb_url), prefix=args.prefix)
    rag = GraphRagStore(base)
    subject_id = uuid.UUID(args.subject_id) if args.subject_id else uuid.UUID(
        base._read(tenant_id, "MATCH (d:Document) RETURN d.subject_id AS s LIMIT 1")[0]["s"])
    scope = Scope.for_users(uuid.uuid4(), [tenant_id])
    client = get_llm_client()
    questions = load_questions(args.eval_set)[: args.limit]
    print(f"{len(questions)} questions, subject {str(subject_id)[:8]}, graph {base.graph_name(tenant_id)}")

    per_system: dict[str, list[dict]] = {"vector": [], **{name: [] for name in VARIANTS}}
    errors = 0
    for qid, q in enumerate(questions):
        try:
            for i, (name, params) in enumerate(VARIANTS.items()):
                t0 = time.monotonic()
                with SessionLocal() as db:
                    got = unified.retrieve_chunks(db, scope, rag, q["query"], subject_id, params, client=client,
                                                  vector_baseline=(i == 0))
                ms = int((time.monotonic() - t0) * 1000)
                per_system[name].append(row(qid, q, got.final, got.evidence, ms))
                if i == 0:
                    vec = got.vector_final or []
                    per_system["vector"].append(row(qid, q, vec, pipelines.assess_evidence(
                        [{"rerank_score": c.rerank_score} for c in vec]), 0))
        except Exception as exc:  # noqa: BLE001 - one bad question must not end the run
            errors += 1
            for rows in per_system.values():
                while len(rows) > qid:
                    rows.pop()
            print(f"  question {qid} skipped: {type(exc).__name__}: {str(exc)[:120]}")
        if (qid + 1) % 10 == 0:
            print(f"  {qid + 1}/{len(questions)}")

    answer_rows: list[dict] = []
    if args.answers:
        print("answer step (rules-only unified setting)...")
        for qid, q in enumerate(questions):
            try:
                with SessionLocal() as db:
                    res = unified.run_unified(db, scope, rag, q["query"], subject_id, VARIANTS["unified_rules"], client=client)
                answer_rows.append({
                    "qid": qid, "query_type": q.get("query_type", "factoid"), "is_unanswerable": q["is_unanswerable"],
                    "refused": bool(res.evidence.get("abstained")) or is_abstention(res.answer),
                    "verification": res.verification, "has_citations": bool(res.citations),
                    "total_ms": res.total_ms, "cost_usd": res.cost_usd,
                })
            except Exception as exc:  # noqa: BLE001
                print(f"  answer for question {qid} skipped: {type(exc).__name__}: {str(exc)[:100]}")

    report = summarize(per_system, k=args.k)
    if answer_rows:
        report["answers"] = summarize_answers(answer_rows)
    report["questions"] = len(questions) - errors
    report["errors"] = errors
    report["mean_ms"] = {n: round(sum(r["ms"] for r in rs) / len(rs)) for n, rs in per_system.items() if rs and n != "vector"}
    Path(args.out).write_text(json.dumps({"report": report, "rows": per_system, "answer_rows": answer_rows}, indent=1))

    print(f"\nRecall@{args.k} / hit / MRR on answerable questions; abstention on unanswerable (n={len(questions) - errors})")
    for name, entry in report["systems"].items():
        o = entry["overall"]
        line = (f"{name:14} recall {o['recall']}  hit {o['hit']}  mrr {o['mrr']}  | abstain-on-unanswerable {o['abstain_on_unanswerable']}"
                f"  false-abstain {o['false_abstain_on_answerable']}")
        v = entry.get("vs_baseline")
        if v:
            line += f"  | vs vector: {v['mean_recall_diff']:+} CI {v['ci95']} w/t/l {v['wins']}/{v['ties']}/{v['losses']}"
        print(line)
    print("\nby question type (recall):")
    types = sorted({t for e in report["systems"].values() for t in e["by_type"]})
    for t in types:
        cells = []
        for name, entry in report["systems"].items():
            b = entry["by_type"].get(t)
            cells.append(f"{name}={b['recall'] if b and b['recall'] is not None else '-'}({b['n'] if b else 0})")
        print(f"  {t:16} " + "  ".join(cells))
    print("\nmean latency per question (ms):", report["mean_ms"])
    if answer_rows:
        a = report["answers"]
        print("\nfull answers (unified, rules-only):")
        print(f"  refused on unanswerable: {a['refused_on_unanswerable']} (n={a['unanswerable']})   "
              f"refused on answerable: {a['refused_on_answerable']} (n={a['answerable']})")
        print(f"  answered questions: cited {a['cited_rate']}  checks ok {a['verified_ok_rate']}  "
              f"uncited sentence {a['uncited_rate']}  unsupported number {a['unsupported_number_rate']} (n={a['answered']})")
        print(f"  mean answer-step cost ${a['mean_cost_usd']}  mean total {a['mean_total_ms']} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
