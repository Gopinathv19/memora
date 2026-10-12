#!/usr/bin/env python3
"""Run the reviewed evaluation set through Memora's real retrieval pipeline.

For each approved/edited question, executes the same stages `answer_query`
runs -- query embedding -> HNSW top-50 -> MMR top-10 -> reranker top-5 ->
LLM answer -- by importing the app's own stage functions, so the runner always
measures the current configuration. Records per-stage chunk IDs, latencies,
token usage and cost, computes retrieval metrics, optionally judges answer
quality, and writes a JSON + Markdown report.

READ-ONLY against the database: SELECT only. No file in app/, migrations/ or
tests/ is modified.

Run:
    .venv/bin/python evals/scripts/run_eval.py --tenant-id <uuid> --label baseline --limit 5
    .venv/bin/python evals/scripts/run_eval.py --tenant-id <uuid> --label baseline
    .venv/bin/python evals/scripts/run_eval.py --tenant-id <uuid> --label judged --judge
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

import os  # noqa: E402

os.environ.setdefault("API_SECRET", "eval-runner")

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.pricing import price_call  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.db.models import RetrievalChunk  # noqa: E402
from app.retrieval.mmr import mmr_select  # noqa: E402
from app.retrieval.reranker import RerankerError, get_reranker  # noqa: E402
from app.services import embedding_service, retrieval_service  # noqa: E402
from app.services.scope import Scope  # noqa: E402

DEFAULT_INPUT = BACKEND / "evals" / "data" / "eval_set_reviewed.jsonl"
DEFAULT_OUTPUT_DIR = BACKEND / "evals" / "reports"

# Refusal detection: the pipeline's two canned messages plus the wording the
# system prompt tells the model to use. Case-insensitive substring match.
_REFUSAL_PATTERNS = [
    "could not find any information",       # _no_context_answer (empty HNSW)
    "does not contain enough information",  # _generate_answer (empty final)
    "insufficient",                          # system prompt rule 1
    "not mentioned in",                      # common model phrasing
    "no information",                        # common model phrasing
]
_REFUSAL_RE = re.compile("|".join(map(re.escape, _REFUSAL_PATTERNS)), re.IGNORECASE)

SNIPPET_CHARS = 200


def is_refusal(answer: str) -> tuple[bool, str | None]:
    m = _REFUSAL_RE.search(answer or "")
    return (m is not None, m.group(0) if m else None)


# --- Input loading ---------------------------------------------------------------


def load_questions(path: Path) -> list[dict]:
    if not path.exists():
        print(f"No input file at {path}")
        return []
    records = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"Skipping malformed line {line_no}: {exc}", file=sys.stderr)
    return records


def usable(records: list[dict]) -> list[dict]:
    """Only approved or edited rows, in id order."""
    kept = [r for r in records if r.get("review_status") in ("approved", "edited")]
    return sorted(kept, key=lambda r: r.get("id", ""))


# --- Expected-chunk matching -----------------------------------------------------


def load_expected_chunks(db, questions: list[dict]) -> dict[str, dict[str, dict]]:
    """chunk_id -> question_id -> current chunk row (READ-ONLY)."""
    ids = {c["chunk_id"] for q in questions for c in q.get("expected_chunks") or []}
    if not ids:
        return {}
    rows = db.execute(
        select(RetrievalChunk).where(RetrievalChunk.id.in_(ids))
    ).scalars().all()
    return {str(r.id): {q["id"]: {
        "source_id": str(r.source_id),
        "page_start": r.page_start,
        "page_end": r.page_end,
        "content": r.content or "",
    } for q in questions} for r in rows}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


def match_chunk(
    expected: dict, current_by_id: dict[str, dict], fallback_pool: list[dict]
) -> tuple[bool, str]:
    """Does this retrieved chunk correspond to the expected one?

    Returns (matched, how). `fallback_pool` is every active chunk of the
    expected source (loaded once per question).
    """
    # 1. Direct chunk_id hit.
    row = current_by_id.get(expected["chunk_id"])
    if row is not None:
        return True, "chunk_id"

    # 2. Fallback: same source + overlapping page range + snippet similarity.
    exp_snip = _norm(expected.get("text_snippet", "")[:SNIPPET_CHARS])
    exp_src = expected.get("source_id")
    ps, pe = expected.get("page_start"), expected.get("page_end")
    for cand in fallback_pool:
        if str(cand["source_id"]) != exp_src:
            continue
        cs, ce = cand["page_start"], cand["page_end"]
        if ps is not None and cs is not None:
            lo, hi = max(ps, cs), min(pe or ps, ce or cs)
            if hi < lo:  # no page overlap
                continue
        if exp_snip and _norm(cand["content"][:SNIPPET_CHARS]) == exp_snip:
            return True, "fallback_snippet"
        if exp_snip and exp_snip in _norm(cand["content"]):
            return True, "fallback_snippet_contained"
    return False, "none"


# --- The pipeline, stage by stage (same order and params as answer_query) --------


def run_pipeline(db, settings, scope: Scope, question: dict, subject_id, source_id):
    """Run the real stages for one question. Returns a per-question record."""
    q = question["question"]
    strategy_id = None
    timings: dict[str, float] = {}
    usage_totals = {"prompt": 0, "completion": 0}
    cost = 0.0
    llm_calls = 0

    def _price(model, prompt, completion):
        nonlocal cost
        c, _ = price_call(settings.llm_provider, model, "answer", prompt, completion)
        cost += c

    # Stage 0: query embedding.
    t0 = time.monotonic()
    embedded = embedding_service.embed_query(db, q, strategy_id=strategy_id)
    query_vector = embedded["vector"]
    strategy = embedding_service.get_strategy(db, uuid.UUID(embedded["strategy_id"]))
    timings["embed_query"] = (time.monotonic() - t0) * 1000
    usage_totals["prompt"] += embedded["usage"]["total_tokens"]
    _price(settings.embedding_model, embedded["usage"]["total_tokens"], 0)

    # Stage 1: HNSW top-k (settings-driven, same as answer_query).
    t0 = time.monotonic()
    candidates = retrieval_service.hnsw_search(
        db, query_vector, strategy.id, scope,
        subject_id=subject_id, source_id=source_id,
    )
    timings["hnsw"] = (time.monotonic() - t0) * 1000
    top_ids = [str(c.chunk_id) for c in candidates]

    if not candidates:
        return {
            "question_id": question["id"], "question": q, "type": question["type"],
            "expected_behavior": question["expected_behavior"],
            "stages": {"hnsw": [], "mmr": [], "final": []},
            "answer": retrieval_service._no_context_answer(q, strategy)["answer"],
            "refusal": True, "refusal_rule": "could not find any information",
            "timings_ms": timings, "usage": usage_totals, "cost_usd": cost,
            "llm_calls": llm_calls, "reranker_used": False,
            "rerank_scores": [],
        }

    # Stage 2: MMR (settings-driven).
    retrieval_service._load_vectors(db, candidates)
    t0 = time.monotonic()
    mmr_picked = mmr_select(
        query_vector, candidates,
        top_k=settings.retrieval_mmr_top_k,
        lambda_=settings.retrieval_mmr_lambda,
    )
    timings["mmr"] = (time.monotonic() - t0) * 1000
    mmr_ids = [str(c.chunk_id) for c in mmr_picked]

    # Stage 3: reranker (settings-driven, same fallback as answer_query).
    passages = [c.embedding_text or c.content for c in mmr_picked]
    t0 = time.monotonic()
    rerank_scores: list[float | None] = []
    reranker_used = False
    try:
        ranked = get_reranker().rerank(q, passages)
        reranker_used = True
        final = []
        for result in ranked[: settings.retrieval_final_top_k]:
            cand = mmr_picked[result.index]
            cand.rerank_score = result.score
            final.append(cand)
        rerank_scores = [c.rerank_score for c in mmr_picked]
    except RerankerError:
        final = mmr_picked[: settings.retrieval_final_top_k]
    timings["rerank"] = (time.monotonic() - t0) * 1000
    final_ids = [str(c.chunk_id) for c in final]

    # Stage 4: generation (same prompt as answer_query).
    t0 = time.monotonic()
    answer, usage = retrieval_service._generate_answer(q, final)
    timings["generate"] = (time.monotonic() - t0) * 1000
    llm_calls += 1
    usage_totals["prompt"] += usage.prompt_tokens
    usage_totals["completion"] += usage.completion_tokens
    _price(settings.llm_answer_model, usage.prompt_tokens, usage.completion_tokens)

    refused, rule = is_refusal(answer)
    return {
        "question_id": question["id"], "question": q, "type": question["type"],
        "expected_behavior": question["expected_behavior"],
        "stages": {"hnsw": top_ids, "mmr": mmr_ids, "final": final_ids},
        "answer": answer,
        "refusal": refused, "refusal_rule": rule,
        "timings_ms": timings, "usage": usage_totals, "cost_usd": cost,
        "llm_calls": llm_calls, "reranker_used": reranker_used,
        "rerank_scores": [round(s, 4) if s is not None else None for s in rerank_scores],
        "final_details": [{
            "chunk_id": str(c.chunk_id), "source_id": str(c.source_id),
            "page_start": c.page_start, "page_end": c.page_end,
            "section_path": c.section_path,
            "similarity": round(c.similarity, 4),
            "rerank_score": round(c.rerank_score, 4) if c.rerank_score is not None else None,
        } for c in final],
    }


# --- Metrics -----------------------------------------------------------------------


def retrieval_metrics(question: dict, result: dict, current_by_id, fallback_pool):
    """Per-question retrieval metrics. Mutates `result` with 'metrics'."""
    expected = question.get("expected_chunks") or []
    exp_ids = [c["chunk_id"] for c in expected]
    stages = result["stages"]

    matched = {}
    how_counter = Counter()
    for e in expected:
        found = {"hnsw": False, "mmr": False, "final": False}
        how = "none"
        # Per-stage membership: direct chunk_id first, then fallback match.
        for stage in ("hnsw", "mmr", "final"):
            for cid in stages[stage]:
                if cid == e["chunk_id"]:
                    found[stage] = True
                    how = "chunk_id"
                    break
            if not found[stage]:
                # fallback match against this stage's chunks
                for cid in stages[stage]:
                    row = current_by_id.get(cid)
                    if row is None:
                        continue
                    ok, h = match_chunk(e, current_by_id, [row])
                    if ok:
                        found[stage] = True
                        how = h
                        break
        matched[e["chunk_id"]] = found
        how_counter[how] += 1

    result["metrics"] = {
        "expected_count": len(expected),
        "matched": {k: v for k, v in matched.items()},
        "match_how": dict(how_counter),
        "in_hnsw": sum(1 for v in matched.values() if v["hnsw"]),
        "in_mmr": sum(1 for v in matched.values() if v["mmr"]),
        "in_final": sum(1 for v in matched.values() if v["final"]),
        "all_in_final": all(v["final"] for v in matched.values()) if matched else False,
        "any_in_final": any(v["final"] for v in matched.values()) if matched else False,
    }
    # MRR: rank of the first correct chunk in the final list (1-based).
    mrr = 0.0
    for rank, cid in enumerate(stages["final"], start=1):
        for e in expected:
            if cid == e["chunk_id"]:
                mrr = 1.0 / rank
                break
        else:
            continue
        break
    result["metrics"]["mrr"] = round(mrr, 4)
    # Top reranker score (for a future cutoff).
    scores = [s for s in (result.get("rerank_scores") or []) if s is not None]
    result["metrics"]["top_rerank_score"] = max(scores) if scores else None
    return result


def lost_at_stage(results: list[dict]) -> dict:
    """Count questions whose right chunk was in top-50 but lost later."""
    lost_mmr = lost_rerank = 0
    for r in results:
        if r["expected_behavior"] != "answer":
            continue
        m = r.get("metrics") or {}
        if m.get("in_hnsw") and not m.get("in_mmr"):
            lost_mmr += 1
        elif m.get("in_mmr") and not m.get("in_final"):
            lost_rerank += 1
    return {"lost_at_mmr": lost_mmr, "lost_at_reranker": lost_rerank}


# --- Judge (Step 3) ------------------------------------------------------------------

_JUDGE_SYSTEM = """You are a strict evaluator of question-answering systems. You receive a question, \
a reference answer, and the context chunks the system retrieved. Score the generated answer.

Return exactly this JSON object and nothing else:
{
  "correctness": 0 or 1,
  "correctness_reason": "one short sentence",
  "faithfulness": 0 or 1,
  "faithfulness_reason": "one short sentence"
}

Rules:
- correctness = 1 only if the answer states the same fact(s) as the reference \
answer. Numbers, names, dates and amounts must match exactly. Partial or \
vague answers score 0.
- faithfulness = 1 only if every claim in the answer is supported by the \
retrieved context chunks. Any claim not supported by the chunks scores 0.
- If the answer says the information is insufficient, correctness = 1 only \
when the reference answer is empty (an unanswerable question); otherwise 0.
- Be strict. When in doubt, score 0."""


def judge_answer(client, model, question: dict, result: dict) -> dict | None:
    chunks_text = "\n\n".join(
        f"<chunk_{i}>\n{c['content'][:1500]}\n</chunk_{i}>"
        for i, c in enumerate(
            (d for d in result.get("final_details") or []), start=1
        )
    ) or "(no chunks retrieved)"
    user = (
        f"Question: {question['question']}\n\n"
        f"Reference answer: {question.get('reference_answer') or '(none - unanswerable question)'}\n\n"
        f"Retrieved context:\n{chunks_text}\n\n"
        f"Generated answer: {result['answer']}\n\n"
        "Return the JSON object now."
    )
    try:
        raw, usage = client.chat_json(model, _JUDGE_SYSTEM, user)
    except Exception:
        return None
    return {
        "correctness": 1 if raw.get("correctness") == 1 else 0,
        "correctness_reason": str(raw.get("correctness_reason") or "")[:300],
        "faithfulness": 1 if raw.get("faithfulness") == 1 else 0,
        "faithfulness_reason": str(raw.get("faithfulness_reason") or "")[:300],
        "usage": {"prompt": usage.prompt_tokens, "completion": usage.completion_tokens},
    }


# --- Report writing -------------------------------------------------------------------


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=BACKEND.parent,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return "unknown"


def summarize(results: list[dict], settings, label: str, judge_cost: float) -> dict:
    answerable = [r for r in results if r["expected_behavior"] == "answer"]
    unanswerable = [r for r in results if r["expected_behavior"] == "insufficient"]

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    def p95(vals):
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        idx = min(len(vals) - 1, max(0, int(round(0.95 * (len(vals) - 1)))))
        return round(vals[idx], 1)

    def stage_recall(stage):
        vals = [r["metrics"][stage] for r in answerable if r.get("metrics")]
        return round(sum(vals) / len(vals), 4) if vals else None

    latencies = [sum(r["timings_ms"].values()) for r in results]
    refusals = [r for r in unanswerable if r.get("refusal")]
    return {
        "label": label,
        "counts": {
            "total": len(results), "answerable": len(answerable),
            "unanswerable": len(unanswerable),
        },
        "recall": {
            "at_hnsw": stage_recall("in_hnsw"),
            "at_mmr": stage_recall("in_mmr"),
            "at_final": stage_recall("in_final"),
            "all_expected_in_final": round(
                sum(1 for r in answerable if r.get("metrics", {}).get("all_in_final"))
                / len(answerable), 4) if answerable else None,
            "at_least_one_in_final": round(
                sum(1 for r in answerable if r.get("metrics", {}).get("any_in_final"))
                / len(answerable), 4) if answerable else None,
        },
        "mrr": avg([r.get("metrics", {}).get("mrr") for r in answerable]),
        "refusal_rate": round(len(refusals) / len(unanswerable), 4) if unanswerable else None,
        "latency_ms": {"avg": round(avg(latencies), 1) if latencies else None, "p95": p95(latencies)},
        "cost_usd": {
            "pipeline": round(sum(r["cost_usd"] for r in results), 6),
            "judge": round(judge_cost, 6),
        },
        "lost_at_stage": lost_at_stage(results),
        "by_type": {
            t: {
                "n": len([r for r in results if r["type"] == t]),
                "recall_final": stage_recall_per_type(results, t, "in_final"),
                "mrr": avg([r.get("metrics", {}).get("mrr") for r in results
                            if r["type"] == t and r["expected_behavior"] == "answer"]),
            } for t in sorted({r["type"] for r in results})
        },
    }


def stage_recall_per_type(results, qtype, stage):
    vals = [r["metrics"][stage] for r in results
            if r["type"] == qtype and r["expected_behavior"] == "answer" and r.get("metrics")]
    return round(sum(vals) / len(vals), 4) if vals else None


def write_reports(out_dir: Path, label: str, summary: dict, results: list[dict],
                  settings, judge_used: bool) -> tuple[Path, Path]:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"report_{label}_{ts}.json"
    md_path = out_dir / f"report_{label}_{ts}.md"

    payload = {
        "run_info": {
            "timestamp": ts, "label": label, "git_commit": git_commit(),
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
            "judge_used": judge_used,
        },
        "summary": summary,
        "questions": results,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # Markdown summary.
    lines = [f"# Eval report: {label} ({ts})", ""]
    s = summary
    lines += [
        "## Overall", "",
        "| Metric | Value |", "|---|---|",
        f"| Questions | {s['counts']['total']} (answerable {s['counts']['answerable']}, unanswerable {s['counts']['unanswerable']}) |",
        f"| Recall @ top-{settings.retrieval_hnsw_top_k} (HNSW) | {s['recall']['at_hnsw']} |",
        f"| Recall @ MMR top-{settings.retrieval_mmr_top_k} | {s['recall']['at_mmr']} |",
        f"| Recall @ final {settings.retrieval_final_top_k} | {s['recall']['at_final']} |",
        f"| All expected in final | {s['recall']['all_expected_in_final']} |",
        f"| At least one in final | {s['recall']['at_least_one_in_final']} |",
        f"| MRR | {s['mrr']} |",
        f"| Refusal rate (unanswerable) | {s['refusal_rate']} |",
        f"| Latency avg / p95 (ms) | {s['latency_ms']['avg']} / {s['latency_ms']['p95']} |",
        f"| Cost: pipeline / judge | ${s['cost_usd']['pipeline']} / ${s['cost_usd']['judge']} |",
        "",
        "## By question type", "",
        "| Type | n | Recall @ final | MRR |", "|---|---|---|---|",
    ]
    for t, row in s["by_type"].items():
        lines.append(f"| {t} | {row['n']} | {row['recall_final']} | {row['mrr']} |")
    lines += [
        "",
        "## Lost at stage", "",
        f"- Right chunk in top-{settings.retrieval_hnsw_top_k} but lost at MMR: **{s['lost_at_stage']['lost_at_mmr']}**",
        f"- Survived MMR but lost at the reranker: **{s['lost_at_stage']['lost_at_reranker']}**",
        "",
        "## Worst failures (expected chunk not in final)", "",
    ]
    failures = [r for r in results
                if r["expected_behavior"] == "answer" and r.get("metrics")
                and not r["metrics"]["any_in_final"]]
    if not failures:
        lines.append("(none)")
    for r in failures[:10]:
        q = next((x for x in results if x["question_id"] == r["question_id"]), r)
        exp = (r.get("expected_chunks") or [{}])[0]
        got = (r.get("final_details") or [{}])[0]
        lines += [
            f"### {r['question_id']} ({r['type']})",
            f"- Question: {r['question']}",
            f"- Expected: source `{exp.get('source_id', '?')}` pages {exp.get('page_start')}–{exp.get('page_end')}",
            f"- Retrieved instead: source `{got.get('source_id', '?')}` pages {got.get('page_start')}–{got.get('page_end')} (sim {got.get('similarity')}, rerank {got.get('rerank_score')})",
            "",
        ]
    lines += [
        "## Run info", "",
        f"- Git commit: `{payload['run_info']['git_commit']}`",
        f"- Answer model: `{settings.llm_answer_model}`",
        f"- Embedding model: `{settings.embedding_model}`",
        f"- Reranker: `{settings.reranker_model}`",
        f"- Settings: top-k={settings.retrieval_hnsw_top_k}, ef_search={settings.retrieval_hnsw_ef_search}, "
        f"mmr_lambda={settings.retrieval_mmr_lambda}, mmr_top_k={settings.retrieval_mmr_top_k}, "
        f"final_top_k={settings.retrieval_final_top_k}",
        f"- Judge used: {judge_used}",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


# --- Main -------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--label", default="run")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    settings = get_settings()
    tenant_id = uuid.UUID(args.tenant_id)
    questions = usable(load_questions(Path(args.input)))
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        print("No approved/edited questions to run.")
        return 1

    scope = Scope(kind="user", user_id=None, tenant_ids=frozenset([tenant_id]))
    db = SessionLocal()
    results: list[dict] = []
    judge_cost = 0.0
    judge_client = None
    if args.judge:
        from app.llm.client import get_llm_client
        judge_client = get_llm_client()

    try:
        current_by_id = load_expected_chunks(db, questions)
        for i, question in enumerate(questions, start=1):
            print(f"[{i}/{len(questions)}] {question['id']} ({question['type']}): {question['question'][:70]}")
            subject_id = uuid.UUID(question["subject_id"]) if question.get("subject_id") else None
            result = run_pipeline(db, settings, scope, question, subject_id, None)
            # Fallback pool: every active chunk of each expected source.
            pool: list[dict] = []
            for e in question.get("expected_chunks") or []:
                src = e.get("source_id")
                if src:
                    pool.extend([
                        {"source_id": str(r.source_id), "page_start": r.page_start,
                         "page_end": r.page_end, "content": r.content or ""}
                        for r in db.execute(
                            select(RetrievalChunk).where(
                                RetrievalChunk.source_id == uuid.UUID(src),
                                RetrievalChunk.is_active == True,  # noqa: E712
                            )
                        ).scalars()
                    ])
            result["expected_chunks"] = question.get("expected_chunks") or []
            retrieval_metrics(question, result, current_by_id, pool)
            if args.judge and judge_client is not None:
                verdict = judge_answer(judge_client, settings.llm_answer_model, question, result)
                if verdict is not None:
                    result["judge"] = {
                        "correctness": verdict["correctness"],
                        "correctness_reason": verdict["correctness_reason"],
                        "faithfulness": verdict["faithfulness"],
                        "faithfulness_reason": verdict["faithfulness_reason"],
                    }
                    c, _ = price_call(
                        settings.llm_provider, settings.llm_answer_model, "answer",
                        verdict["usage"]["prompt"], verdict["usage"]["completion"],
                    )
                    judge_cost += c
                else:
                    result["judge"] = None
            results.append(result)
    finally:
        db.close()

    summary = summarize(results, settings, args.label, judge_cost)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = write_reports(out_dir, args.label, summary, results, settings, args.judge)

    print()
    print(f"=== {args.label} ===")
    print(f"Questions: {summary['counts']}")
    print(f"Recall @ final: {summary['recall']['at_final']}  MRR: {summary['mrr']}")
    print(f"Refusal rate: {summary['refusal_rate']}")
    print(f"Latency avg/p95: {summary['latency_ms']['avg']}/{summary['latency_ms']['p95']} ms")
    print(f"Cost: pipeline ${summary['cost_usd']['pipeline']}  judge ${summary['cost_usd']['judge']}")
    print(f"Lost at stage: {summary['lost_at_stage']}")
    print(f"Reports: {json_path.name}  {md_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())