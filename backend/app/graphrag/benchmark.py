"""Benchmark the pipelines over a question set and store the result.

For every question, `vector`, one `graph:<technique>` per requested technique,
and (optionally) `hybrid:<first technique>` run on the same scope with the same
reranker and answer model. Each result is scored with the retrieval metrics the
rest of Memora already uses (`evals.metrics`: Recall@k, MRR, nDCG@10, hit rate,
context precision/recall, against the question's `supporting_chunk_ids`), plus:

* **answer correctness**: an LLM judge scores the answer 0 / 0.5 / 1 against the
  expected answer (optional; labelled as judge-based, like faithfulness in the
  eval package)
* **latency** (p50 / p95), **tokens** and **cost** per question

Aggregates are reported overall and per `query_type`, with a 95% bootstrap
confidence interval on the means that matter (a few dozen questions give wide
intervals, and the dashboard shows them rather than hiding them), and a
win / tie / loss count of each pipeline against `vector` per question.
"""

import hashlib
import json
import logging
import random
import statistics
import sys
import threading
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.db.models import BenchmarkRun
from app.graphrag import compare as compare_module
from app.graphrag.params import Pipeline, RetrievalParams, Technique
from app.graphrag.pipelines import llm_cost
from app.graphrag.store import GraphRagStore
from app.llm.client import LLMError, get_llm_client
from app.services.scope import Scope

log = logging.getLogger(__name__)

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:  # `evals` lives next to `app`
    sys.path.insert(0, str(_BACKEND))

_JUDGE_SYSTEM = (
    "You grade an answer against a reference answer. Return JSON {\"score\": 0, 0.5 or 1}. "
    "1 = the answer states the same facts as the reference (wording may differ). "
    "0.5 = partly right or missing a required piece. 0 = wrong, unrelated, or says the "
    "information is not available while the reference has it. Judge only against the reference."
)
TIE_MARGIN = 0.05


def pipeline_key(pipeline: str, technique: str | None) -> str:
    return "vector" if pipeline == "vector" else f"{pipeline}:{technique}"


def bootstrap_ci(values: list[float], n: int = 1000, seed: int = 7) -> tuple[float, float] | None:
    """95% percentile bootstrap interval of the mean."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(n))
    return round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4)


def judge(question: str, expected: str, answer: str) -> tuple[float | None, float]:
    """(score or None if the judge failed, cost in USD)."""
    model = get_settings().llm_answer_model
    user = f"Question: {question}\n\nReference answer: {expected}\n\nAnswer to grade: {answer}"
    try:
        data, usage = get_llm_client().chat_json(model, _JUDGE_SYSTEM, user)
    except LLMError:
        return None, 0.0
    cost = llm_cost(model, usage.prompt_tokens, usage.completion_tokens)
    try:
        score = float(data.get("score"))
    except (TypeError, ValueError):
        return None, cost
    return (score if score in (0.0, 0.5, 1.0) else None), cost


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(p * len(ordered)))], 1)


def aggregate(per_question: list[dict], keys: list[str]) -> tuple[dict, dict]:
    """Per-pipeline aggregates and the same broken down by query_type."""
    from evals import metrics

    aggregates: dict[str, dict] = {}
    by_type: dict[str, dict] = {}
    for key in keys:
        rows = [q["pipelines"][key] for q in per_question if key in q["pipelines"]]
        ok = [r for r in rows if r["status"] == "ok"]
        retrieval_rows = [
            {"retrieved_ids": r["retrieved_ids"], "relevant_ids": q["relevant_ids"],
             "query_type": q["query_type"]}
            for q in per_question
            for r in [q["pipelines"].get(key)]
            if r and r["status"] == "ok"
        ]
        summary = metrics.summarize_retrieval(retrieval_rows)
        correctness = [r["correctness"] for r in ok if r.get("correctness") is not None]
        latencies = [r["total_ms"] for r in ok]
        recalls = [
            metrics.recall_at_k(r["retrieved_ids"], q["relevant_ids"], 5)
            for q in per_question for r in [q["pipelines"].get(key)]
            if r and r["status"] == "ok" and q["relevant_ids"]
        ]
        recalls = [x for x in recalls if x is not None]
        aggregates[key] = {
            **summary,
            "questions": len(rows), "errors": len(rows) - len(ok),
            "correctness": round(statistics.fmean(correctness), 4) if correctness else None,
            "correctness_ci": bootstrap_ci(correctness),
            "recall_at_5_ci": bootstrap_ci(recalls),
            "latency_p50_ms": _percentile(latencies, 0.5),
            "latency_p95_ms": _percentile(latencies, 0.95),
            "latency_mean_ms": round(statistics.fmean(latencies), 1) if latencies else None,
            "cost_usd_total": round(sum(r["cost_usd"] for r in ok), 6),
            "cost_usd_per_question": round(sum(r["cost_usd"] for r in ok) / len(ok), 6) if ok else None,
            "tokens_total": sum(r["prompt_tokens"] + r["completion_tokens"] for r in ok),
        }
        grouped: dict[str, dict] = {}
        for qtype in sorted({q["query_type"] for q in per_question}):
            sub = [q for q in per_question if q["query_type"] == qtype and key in q["pipelines"]]
            sub_ok = [q["pipelines"][key] for q in sub if q["pipelines"][key]["status"] == "ok"]
            corr = [r["correctness"] for r in sub_ok if r.get("correctness") is not None]
            rec = [
                metrics.recall_at_k(q["pipelines"][key]["retrieved_ids"], q["relevant_ids"], 5)
                for q in sub if q["pipelines"][key]["status"] == "ok" and q["relevant_ids"]
            ]
            rec = [x for x in rec if x is not None]
            lat = [r["total_ms"] for r in sub_ok]
            grouped[qtype] = {
                "n": len(sub),
                "correctness": round(statistics.fmean(corr), 4) if corr else None,
                "recall_at_5": round(statistics.fmean(rec), 4) if rec else None,
                "latency_mean_ms": round(statistics.fmean(lat), 1) if lat else None,
            }
        by_type[key] = grouped

    # win / tie / loss vs vector, per question: correctness when judged, else recall@5
    for key in keys:
        if key == "vector":
            continue
        wins = ties = losses = 0
        for q in per_question:
            a, b = q["pipelines"].get(key), q["pipelines"].get("vector")
            if not a or not b or a["status"] != "ok" or b["status"] != "ok":
                continue
            va = a.get("correctness") if a.get("correctness") is not None else a.get("recall_at_5")
            vb = b.get("correctness") if b.get("correctness") is not None else b.get("recall_at_5")
            if va is None or vb is None:
                continue
            if va > vb + TIE_MARGIN:
                wins += 1
            elif vb > va + TIE_MARGIN:
                losses += 1
            else:
                ties += 1
        aggregates[key]["vs_vector"] = {"wins": wins, "ties": ties, "losses": losses}
    return aggregates, by_type


def run_benchmark(
    benchmark_id: uuid.UUID, scope: Scope, rag_store: GraphRagStore, subject_id, tenant_id,
    questions: list[dict], techniques: list[Technique], include_hybrid: bool, do_judge: bool,
    params: RetrievalParams,
) -> None:
    """Runs in a background thread: updates the BenchmarkRun row after every question."""
    from evals import metrics

    if not techniques:
        raise ValueError("at least one technique is required")
    keys = ["vector"] + [pipeline_key("graph", str(t)) for t in techniques]
    if include_hybrid:
        keys.append(pipeline_key("hybrid", str(techniques[0])))

    per_question: list[dict] = []
    judge_cost = 0.0
    try:
        for index, q in enumerate(questions):
            relevant = list(q.get("supporting_chunk_ids") or [])
            entry = {
                "index": index, "query": q["query"], "query_type": q.get("query_type", "factoid"),
                "expected_answer": q.get("expected_answer"), "relevant_ids": relevant,
                "pipelines": {},
            }
            # One compare per technique so each gets its own params; the vector
            # pipeline and the hybrid pipeline ride along with the first one.
            for i, technique in enumerate(techniques):
                pipes = [Pipeline.GRAPH]
                if i == 0:
                    pipes = [Pipeline.VECTOR] + pipes + ([Pipeline.HYBRID] if include_hybrid else [])
                p = RetrievalParams(**{**params.__dict__, "technique": technique})
                result = compare_module.compare(
                    scope, rag_store, q["query"], subject_id, tenant_id, p, pipes
                )
                for pipe_name, r in result["results"].items():
                    key = pipeline_key(pipe_name, r.get("technique"))
                    retrieved = [c["chunk_id"] for c in r["chunks"]]
                    row = {
                        "status": r["status"], "error": r.get("error"),
                        "answer": r["answer"], "retrieved_ids": retrieved,
                        "total_ms": r["total_ms"], "cost_usd": r["cost_usd"],
                        "prompt_tokens": r["prompt_tokens"], "completion_tokens": r["completion_tokens"],
                        "recall_at_5": metrics.recall_at_k(retrieved, relevant, 5) if relevant else None,
                        "correctness": None,
                    }
                    if do_judge and r["status"] == "ok" and q.get("expected_answer"):
                        score, cost = judge(q["query"], q["expected_answer"], r["answer"])
                        row["correctness"] = score
                        judge_cost += cost
                    entry["pipelines"][key] = row
            per_question.append(entry)
            aggregates, by_type = aggregate(per_question, keys)
            _save(benchmark_id, per_question, aggregates, by_type, status="running",
                  extra={"judge_cost_usd": round(judge_cost, 6)})
        aggregates, by_type = aggregate(per_question, keys)
        _save(benchmark_id, per_question, aggregates, by_type, status="done",
              extra={"judge_cost_usd": round(judge_cost, 6)})
    except Exception as exc:  # noqa: BLE001 - recorded on the row
        log.exception("benchmark %s failed", benchmark_id)
        _save(benchmark_id, per_question, {}, {}, status="failed", error=str(exc)[:500])


def _save(benchmark_id, results, aggregates, by_type, *, status: str, error: str | None = None,
          extra: dict | None = None) -> None:
    with SessionLocal() as db:
        run = db.get(BenchmarkRun, benchmark_id)
        if run is None:
            return
        run.results = results
        run.aggregates = aggregates
        run.by_type = by_type
        run.status = status
        run.error = error
        if extra:
            run.config = {**run.config, **extra}
        db.commit()


def create_run(
    db: Session, *, tenant_id, subject_id, label: str, questions: list[dict], config: dict,
    build_cost: dict,
) -> BenchmarkRun:
    sha = hashlib.sha256(json.dumps(questions, sort_keys=True).encode()).hexdigest()
    run = BenchmarkRun(
        tenant_id=tenant_id, subject_id=subject_id, label=label, status="running",
        dataset_sha=sha, question_count=len(questions), config=config, build_cost=build_cost,
    )
    db.add(run)
    db.commit()
    return run


def start_background(**kwargs) -> None:
    threading.Thread(target=run_benchmark, kwargs=kwargs, daemon=True).start()
