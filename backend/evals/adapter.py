"""The evaluation adapter: runs the real pipeline stages for one query.

``run_eval.py`` (the standalone script) and ``evals.runner`` (the package
CLI) both go through here, so there is exactly one place that knows how
to execute Memora's retrieval for evaluation -- importing the app's own
stage functions, in the same order and with the same settings-driven
parameters as ``retrieval_service.answer_query``:

    query embedding -> HNSW top-50 -> MMR top-10 -> reranker top-5 -> LLM

The adapter never changes production behavior: it only *calls* the
production stage functions. The database is only read (SELECT only).
"""

from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import os  # noqa: E402

os.environ.setdefault("API_SECRET", "eval-adapter")

from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.pricing import price_call  # noqa: E402
from app.retrieval.mmr import mmr_select  # noqa: E402
from app.retrieval.reranker import RerankerError, get_reranker  # noqa: E402
from app.services import embedding_service, retrieval_service  # noqa: E402
from app.services.scope import Scope  # noqa: E402


@dataclass
class StageResult:
    """Ranked chunk IDs at each stage, for retrieval metrics."""

    hnsw_ids: list[str] = field(default_factory=list)
    mmr_ids: list[str] = field(default_factory=list)
    final_ids: list[str] = field(default_factory=list)


@dataclass
class RetrievalResult:
    """One query's retrieval outcome."""

    stages: StageResult = field(default_factory=StageResult)
    # Full details of the final chunks (source, pages, scores).
    final_details: list[dict] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=lambda: {"prompt": 0, "completion": 0})
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class FullResult(RetrievalResult):
    """Retrieval plus the generated answer."""

    answer: str = ""
    refusal: bool = False
    refusal_rule: str | None = None
    reranker_used: bool = False
    rerank_scores: list[float | None] = field(default_factory=list)
    llm_calls: int = 0


def scope_for(tenant_id: uuid.UUID | str) -> Scope:
    """A console-style scope limited to one tenant (evaluation only)."""
    return Scope(kind="user", user_id=None, tenant_ids=frozenset([uuid.UUID(str(tenant_id))]))


def run_retrieval(
    db: Session,
    settings: Settings,
    scope: Scope,
    query: str,
    subject_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    strategy_id: uuid.UUID | None = None,
) -> RetrievalResult:
    """Run stages 0-3 (embedding through reranker) for one query.

    Same order, same settings-driven parameters as answer_query; stops
    before generation. Records per-stage chunk IDs, timings, usage, cost.
    """
    result = RetrievalResult()

    def _price(model, prompt, completion):
        c, _ = price_call(settings.llm_provider, model, "answer", prompt, completion)
        result.cost_usd += c

    try:
        # Stage 0: query embedding.
        t0 = time.monotonic()
        embedded = embedding_service.embed_query(db, query, strategy_id=strategy_id)
        query_vector = embedded["vector"]
        strategy = embedding_service.get_strategy(db, uuid.UUID(embedded["strategy_id"]))
        result.timings_ms["embed_query"] = (time.monotonic() - t0) * 1000
        result.usage["prompt"] += embedded["usage"]["total_tokens"]
        _price(settings.embedding_model, embedded["usage"]["total_tokens"], 0)

        # Stage 1: HNSW top-k.
        t0 = time.monotonic()
        candidates = retrieval_service.hnsw_search(
            db, query_vector, strategy.id, scope,
            subject_id=subject_id, source_id=source_id,
        )
        result.timings_ms["hnsw"] = (time.monotonic() - t0) * 1000
        result.stages.hnsw_ids = [str(c.chunk_id) for c in candidates]
        if not candidates:
            return result

        # Stage 2: MMR.
        retrieval_service._load_vectors(db, candidates)
        t0 = time.monotonic()
        mmr_picked = mmr_select(
            query_vector, candidates,
            top_k=settings.retrieval_mmr_top_k,
            lambda_=settings.retrieval_mmr_lambda,
        )
        result.timings_ms["mmr"] = (time.monotonic() - t0) * 1000
        result.stages.mmr_ids = [str(c.chunk_id) for c in mmr_picked]

        # Stage 3: reranker (same fallback as answer_query).
        passages = [c.embedding_text or c.content for c in mmr_picked]
        t0 = time.monotonic()
        try:
            ranked = get_reranker().rerank(query, passages)
            final = []
            for r in ranked[: settings.retrieval_final_top_k]:
                cand = mmr_picked[r.index]
                cand.rerank_score = r.score
                final.append(cand)
        except RerankerError:
            final = mmr_picked[: settings.retrieval_final_top_k]
        result.timings_ms["rerank"] = (time.monotonic() - t0) * 1000
        result.stages.final_ids = [str(c.chunk_id) for c in final]
        result.final_details = [
            {
                "chunk_id": str(c.chunk_id),
                "source_id": str(c.source_id),
                "page_start": c.page_start,
                "page_end": c.page_end,
                "section_path": c.section_path,
                "similarity": round(c.similarity, 4),
                "rerank_score": round(c.rerank_score, 4) if c.rerank_score is not None else None,
            }
            for c in final
        ]
    except Exception as exc:  # a failed example is recorded, never dropped
        result.error = f"{type(exc).__name__}: {exc}"[:500]
    return result


def run_full(
    db: Session,
    settings: Settings,
    scope: Scope,
    query: str,
    subject_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    strategy_id: uuid.UUID | None = None,
) -> FullResult:
    """Run the whole pipeline including answer generation."""
    result = FullResult()

    def _price(model, prompt, completion):
        c, _ = price_call(settings.llm_provider, model, "answer", prompt, completion)
        result.cost_usd += c

    try:
        # Stage 0: query embedding.
        t0 = time.monotonic()
        embedded = embedding_service.embed_query(db, query, strategy_id=strategy_id)
        query_vector = embedded["vector"]
        strategy = embedding_service.get_strategy(db, uuid.UUID(embedded["strategy_id"]))
        result.timings_ms["embed_query"] = (time.monotonic() - t0) * 1000
        result.usage["prompt"] += embedded["usage"]["total_tokens"]
        _price(settings.embedding_model, embedded["usage"]["total_tokens"], 0)

        # Stage 1: HNSW.
        t0 = time.monotonic()
        candidates = retrieval_service.hnsw_search(
            db, query_vector, strategy.id, scope,
            subject_id=subject_id, source_id=source_id,
        )
        result.timings_ms["hnsw"] = (time.monotonic() - t0) * 1000
        result.stages.hnsw_ids = [str(c.chunk_id) for c in candidates]
        if not candidates:
            result.answer = retrieval_service._no_context_answer(query, strategy)["answer"]
            result.refusal, result.refusal_rule = _refusal(result.answer)
            return result

        # Stage 2: MMR.
        retrieval_service._load_vectors(db, candidates)
        t0 = time.monotonic()
        mmr_picked = mmr_select(
            query_vector, candidates,
            top_k=settings.retrieval_mmr_top_k,
            lambda_=settings.retrieval_mmr_lambda,
        )
        result.timings_ms["mmr"] = (time.monotonic() - t0) * 1000
        result.stages.mmr_ids = [str(c.chunk_id) for c in mmr_picked]

        # Stage 3: reranker.
        passages = [c.embedding_text or c.content for c in mmr_picked]
        t0 = time.monotonic()
        rerank_scores: list[float | None] = []
        try:
            ranked = get_reranker().rerank(query, passages)
            result.reranker_used = True
            final = []
            for r in ranked[: settings.retrieval_final_top_k]:
                cand = mmr_picked[r.index]
                cand.rerank_score = r.score
                final.append(cand)
            rerank_scores = [c.rerank_score for c in mmr_picked]
        except RerankerError:
            final = mmr_picked[: settings.retrieval_final_top_k]
        result.timings_ms["rerank"] = (time.monotonic() - t0) * 1000
        result.rerank_scores = [
            round(s, 4) if s is not None else None for s in rerank_scores
        ]
        result.stages.final_ids = [str(c.chunk_id) for c in final]
        result.final_details = [
            {
                "chunk_id": str(c.chunk_id),
                "source_id": str(c.source_id),
                "page_start": c.page_start,
                "page_end": c.page_end,
                "section_path": c.section_path,
                "similarity": round(c.similarity, 4),
                "rerank_score": round(c.rerank_score, 4) if c.rerank_score is not None else None,
            }
            for c in final
        ]

        # Stage 4: generation.
        t0 = time.monotonic()
        answer, usage = retrieval_service._generate_answer(query, final)
        result.timings_ms["generate"] = (time.monotonic() - t0) * 1000
        result.llm_calls += 1
        result.usage["prompt"] += usage.prompt_tokens
        result.usage["completion"] += usage.completion_tokens
        _price(settings.llm_answer_model, usage.prompt_tokens, usage.completion_tokens)
        result.answer = answer
        result.refusal, result.refusal_rule = _refusal(answer)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"[:500]
    return result


def _refusal(answer: str) -> tuple[bool, str | None]:
    from evals.metrics import is_refusal

    return is_refusal(answer)
