"""The evaluation CLI.

    .venv/bin/python -m evals.runner validate  --dataset evals/datasets/example_synthetic.jsonl
    .venv/bin/python -m evals.runner coverage  --dataset evals/datasets/example_synthetic.jsonl
    .venv/bin/python -m evals.runner retrieval --dataset <eval.jsonl> --tenant-id <uuid> --output evals/reports/
    .venv/bin/python -m evals.runner full      --dataset <eval.jsonl> --tenant-id <uuid> --output evals/reports/
    .venv/bin/python -m evals.runner chunking  --source-id <uuid>

Runs are sequential by default (``--parallel N`` is opt-in and bounded),
write JSON + Markdown reports locally, and never touch production tables.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evals import dataset as dataset_mod
from evals import metrics
from evals.adapter import run_full, run_retrieval, scope_for
from evals.reports import build_payload, write_reports

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = BACKEND / "evals" / "datasets" / "example_synthetic.jsonl"
DEFAULT_OUTPUT = BACKEND / "evals" / "reports"


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=BACKEND.parent,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _app_settings():
    sys.path.insert(0, str(BACKEND))
    import os

    os.environ.setdefault("API_SECRET", "eval-runner")
    from app.core.config import get_settings

    return get_settings()


# --- commands ----------------------------------------------------------------------


def cmd_validate(args) -> int:
    path = Path(args.dataset)
    if not path.exists():
        print(f"No dataset at {path}")
        return 1
    examples, errors = dataset_mod.load_lenient(path, legacy=args.legacy)
    for err in errors:
        print(f"  line {err.line_no}: {err.reason}", file=sys.stderr)
    print(f"Valid examples: {len(examples)}")
    print(f"Invalid lines:  {len(errors)}")
    return 1 if errors else 0


def cmd_coverage(args) -> int:
    path = Path(args.dataset)
    examples, errors = dataset_mod.load_lenient(path, legacy=args.legacy)
    for err in errors:
        print(f"  line {err.line_no}: {err.reason}", file=sys.stderr)
    cov = dataset_mod.coverage(examples)
    print(f"Total: {cov['total']}  (answerable {cov['answerable']}, unanswerable {cov['unanswerable']})")
    print("By query type:")
    for t, n in sorted(cov["by_query_type"].items()):
        print(f"  {t:16s} {n}")
    print("By difficulty:")
    for d, n in sorted(cov["by_difficulty"].items()):
        print(f"  {d:10s} {n}")
    print("By tenant:")
    for t, n in sorted(cov["by_tenant"].items()):
        print(f"  {t}  {n}")
    print("Annotation coverage:")
    for k, v in cov["annotation_coverage"].items():
        print(f"  {k:22s} {v}/{cov['total']}")
    return 0


def _run_examples(examples, settings, mode: str, tenant_id, parallel: int) -> list[dict]:
    """Run retrieval (or full) for every example. Sequential by default."""
    from app.db.database import SessionLocal

    scope = scope_for(tenant_id)
    per_example: list[dict] = []

    def one(ex):
        db = SessionLocal()
        try:
            subject_id = uuid.UUID(ex.subject_id) if ex.subject_id else None
            if mode == "retrieval":
                res = run_retrieval(db, settings, scope, ex.query, subject_id=subject_id)
                return {
                    "id": ex.id or ex.query[:60],
                    "query": ex.query,
                    "query_type": ex.query_type,
                    "difficulty": ex.difficulty,
                    "tenant_id": ex.tenant_id,
                    "relevant_ids": ex.supporting_chunk_ids,
                    "retrieved_ids": res.stages.final_ids,
                    "hnsw_ids": res.stages.hnsw_ids,
                    "mmr_ids": res.stages.mmr_ids,
                    "timings_ms": res.timings_ms,
                    "usage": res.usage,
                    "cost_usd": res.cost_usd,
                    "error": res.error,
                }
            res = run_full(db, settings, scope, ex.query, subject_id=subject_id)
            return {
                "id": ex.id or ex.query[:60],
                "query": ex.query,
                "query_type": ex.query_type,
                "difficulty": ex.difficulty,
                "tenant_id": ex.tenant_id,
                "relevant_ids": ex.supporting_chunk_ids,
                "retrieved_ids": res.stages.final_ids,
                "hnsw_ids": res.stages.hnsw_ids,
                "mmr_ids": res.stages.mmr_ids,
                "answer": res.answer,
                "refusal": res.refusal,
                "refusal_rule": res.refusal_rule,
                "expected_answer": ex.expected_answer,
                "reranker_used": res.reranker_used,
                "timings_ms": res.timings_ms,
                "usage": res.usage,
                "cost_usd": res.cost_usd,
                "error": res.error,
            }
        finally:
            db.close()

    if parallel and parallel > 1:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            per_example = list(pool.map(one, examples))
    else:
        for i, ex in enumerate(examples, start=1):
            print(f"[{i}/{len(examples)}] {ex.query[:70]}")
            per_example.append(one(ex))
    return per_example


def _summarize_run(per_example: list[dict], examples, *, full: bool) -> dict:
    retrieval_summary = metrics.summarize_retrieval(per_example)
    summary: dict = {
        "overall": {
            "n": retrieval_summary["n"],
            "recall_at_5": retrieval_summary["recall_at_5"],
            "recall_at_10": retrieval_summary["recall_at_10"],
            "mrr": retrieval_summary["mrr"],
            "ndcg_at_10": retrieval_summary["ndcg_at_10"],
            "hit_rate": retrieval_summary["hit_rate"],
            "context_precision": retrieval_summary["context_precision"],
            "context_recall": retrieval_summary["context_recall"],
        },
        "by_query_type": metrics.group_by(per_example, "query_type"),
        "by_difficulty": metrics.group_by(per_example, "difficulty"),
        "by_tenant": metrics.group_by(per_example, "tenant_id"),
        "annotation_coverage": dataset_mod.coverage(examples)["annotation_coverage"],
        "warnings": [],
    }
    # Redundancy needs chunk texts, which the adapter does not return in
    # the per-example dict; mark it N/A with a reason.
    summary["warnings"].append(
        "redundancy_rate: N/A (requires retrieved chunk texts; not recorded "
        "in this run's per-example output)"
    )
    if full:
        unanswerable = [e for e in per_example if not e.get("relevant_ids")]
        answers = [e.get("answer", "") for e in unanswerable]
        summary["abstention"] = {
            "refusal_rate": metrics.refusal_rate(answers),
            "false_answer_rate": metrics.false_answer_rate(answers),
        }
        latencies = [sum(e.get("timings_ms", {}).values()) for e in per_example]
        summary["operational"] = {
            "latency_avg_ms": metrics.mean(latencies),
            "latency_p50_ms": metrics.percentile(latencies, 50),
            "latency_p95_ms": metrics.percentile(latencies, 95),
            "cost_usd_total": round(sum(e.get("cost_usd", 0) for e in per_example), 6),
        }
        summary["warnings"].append(
            "TTFT: N/A (the LLM client does not expose streaming timings)"
        )
        summary["warnings"].append(
            "answer correctness/completeness/relevance and faithfulness: "
            "judge-based, only computed with --judge (not part of this run)"
        )
    else:
        latencies = [sum(e.get("timings_ms", {}).values()) for e in per_example]
        summary["operational"] = {
            "latency_avg_ms": metrics.mean(latencies),
            "latency_p50_ms": metrics.percentile(latencies, 50),
            "latency_p95_ms": metrics.percentile(latencies, 95),
            "cost_usd_total": round(sum(e.get("cost_usd", 0) for e in per_example), 6),
        }
    return summary


def cmd_retrieval(args) -> int:
    return _run_eval_cmd(args, mode="retrieval")


def cmd_full(args) -> int:
    return _run_eval_cmd(args, mode="full")


def _run_eval_cmd(args, mode: str) -> int:
    path = Path(args.dataset)
    examples, errors = dataset_mod.load_lenient(path, legacy=args.legacy)
    for err in errors:
        print(f"  line {err.line_no}: {err.reason}", file=sys.stderr)
    if args.limit:
        examples = examples[: args.limit]
    if not examples:
        print("No valid examples to run.")
        return 1

    settings = _app_settings()
    tenant_id = uuid.UUID(args.tenant_id)
    started = time.monotonic()
    per_example = _run_examples(examples, settings, mode, tenant_id, args.parallel)
    total_seconds = time.monotonic() - started

    summary = _summarize_run(per_example, examples, full=(mode == "full"))
    summary["operational"]["throughput_qps"] = metrics.throughput(len(examples), total_seconds)

    payload = build_payload(
        label=args.label,
        summary=summary,
        per_example=per_example,
        run_info={
            "mode": mode,
            "dataset": str(path),
            "dataset_sha256": dataset_mod.checksum(path),
            "git_commit": _git_commit(),
            "models": {
                "answer": settings.llm_answer_model,
                "embedding": settings.embedding_model,
                "reranker": settings.reranker_model,
            },
            "settings": {
                "retrieval_hnsw_top_k": settings.retrieval_hnsw_top_k,
                "retrieval_hnsw_ef_search": settings.retrieval_hnsw_ef_search,
                "retrieval_mmr_lambda": settings.retrieval_mmr_lambda,
                "retrieval_mmr_top_k": settings.retrieval_mmr_top_k,
                "retrieval_final_top_k": settings.retrieval_final_top_k,
            },
        },
    )
    out_dir = Path(args.output)
    json_path, md_path = write_reports(out_dir, args.label, payload)

    print()
    print(f"=== {args.label} ({mode}) ===")
    o = summary["overall"]
    print(f"n={o['n']}  Recall@5={o['recall_at_5']}  Recall@10={o['recall_at_10']}  "
          f"MRR={o['mrr']}  nDCG@10={o['ndcg_at_10']}  Hit={o['hit_rate']}")
    if "abstention" in summary:
        print(f"Refusal rate: {summary['abstention']['refusal_rate']}")
    ops = summary["operational"]
    print(f"Latency avg/p50/p95: {ops['latency_avg_ms']}/{ops['latency_p50_ms']}/"
          f"{ops['latency_p95_ms']} ms  throughput={ops['throughput_qps']} q/s")
    print(f"Cost: ${ops['cost_usd_total']}")
    print(f"Reports: {json_path.name}  {md_path.name}")
    return 0


def cmd_chunking(args) -> int:
    from evals.chunking_check import run_chunking_check

    return run_chunking_check(args.source_id)


# --- CLI -----------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--dataset", default=str(DEFAULT_DATASET))
        p.add_argument("--legacy", action="store_true",
                       help="map the older evals/data schema first")

    p = sub.add_parser("validate", help="validate a dataset")
    common(p)
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("coverage", help="dataset coverage by type/difficulty/tenant")
    common(p)
    p.set_defaults(fn=cmd_coverage)

    p = sub.add_parser("retrieval", help="retrieval-only evaluation")
    common(p)
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--label", default="retrieval")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--parallel", type=int, default=1,
                   help="bounded parallel workers (opt-in; default sequential)")
    p.set_defaults(fn=cmd_retrieval)

    p = sub.add_parser("full", help="end-to-end evaluation (retrieval + answer)")
    common(p)
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--label", default="full")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--parallel", type=int, default=1)
    p.set_defaults(fn=cmd_full)

    p = sub.add_parser("chunking", help="chunking QA report (wraps verify_chunking.py)")
    p.add_argument("--source-id", default=None)
    p.set_defaults(fn=cmd_chunking)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
