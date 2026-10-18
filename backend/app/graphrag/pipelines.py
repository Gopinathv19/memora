"""The three pipelines the dashboard compares.

    vector   the existing pipeline, unchanged: dense + lexical, RRF, MMR, rerank, LLM
    graph    one Graph RAG technique finds evidence in FalkorDB; the shared
             reranker picks the final chunks; the same answer LLM writes the answer
    hybrid   vector and graph candidate lists fused by RRF, then the same MMR,
             reranker and LLM as `vector`

**Same footing.** All three end in the same reranker, the same `retrieval_final_top_k`
and the same answer model and system prompt, and all return retrieval chunks
(`retrieval_chunks`), so Recall@k against an eval set's `supporting_chunk_ids`
means the same thing for each. The only deliberate differences: `graph` and
`hybrid` put a short list of knowledge-graph facts (and community summaries)
before the chunks in the answer prompt, because carrying relationships is the
point of Graph RAG; and `graph` skips MMR, because its candidates are already
chosen for relevance to connected entities rather than diversity.
"""

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.pricing import price_call
from app.db.models import RagRun, RetrievalChunk
from app.graphrag import embedder
from app.graphrag.bridge import map_units_to_chunks
from app.graphrag.params import WEAK_EVIDENCE_LOGIT, Pipeline, RetrievalParams
from app.graphrag.store import GraphRagStore
from app.graphrag.techniques import get_technique
from app.graphrag.techniques.common import Ctx
from app.graphrag.types import GraphEvidence
from app.llm.client import LLMError, get_llm_client
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.mmr import mmr_select
from app.retrieval.reranker import RerankerError, get_reranker
from app.services import embedding_service, graph_service, retrieval_service
from app.services.retrieval_service import Candidate
from app.services.scope import Scope

log = logging.getLogger(__name__)

MAX_FACTS_IN_PROMPT = 25
_GRAPH_SYSTEM_NOTE = (
    "\n6. Some context is given as knowledge-graph facts (relationships extracted from the "
    "documents) and community summaries. Treat them as leads that connect the chunks: "
    "rely on the chunks for specifics, and say so if a fact is not backed by a chunk."
)


@dataclass
class PipelineResult:
    pipeline: str
    technique: str | None
    query: str
    answer: str = ""
    chunks: list[dict] = field(default_factory=list)
    timings_ms: dict = field(default_factory=dict)
    total_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    llm_calls: list[dict] = field(default_factory=list)
    trace: dict = field(default_factory=dict)
    # How much to trust what was retrieved: {"best_rerank", "weak", "anchored"}
    evidence: dict = field(default_factory=dict)
    # Graph answers with numbered evidence (`answer.py`): each marker the answer cites, resolved, and the
    # deterministic checks run on it. Empty for the vector, graph and hybrid pipelines.
    citations: list[dict] = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    status: str = "ok"
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def assess_evidence(chunks: list[dict], anchored: bool | None = None) -> dict:
    """Did this pipeline find anything relevant?

    `best_rerank` is the reranker's score for its best chunk, the one calibrated
    relevance signal in the pipeline (see `WEAK_EVIDENCE_LOGIT`); `weak` is true
    when it is below that threshold or nothing was retrieved. `anchored` is for
    graph pipelines: whether a name in the question matched an entity (None for vector).
    """
    scores = [c["rerank_score"] for c in chunks if c.get("rerank_score") is not None]
    best = max(scores) if scores else None
    return {
        "best_rerank": round(best, 2) if best is not None else None,
        "weak": best is None or best < WEAK_EVIDENCE_LOGIT,
        "anchored": anchored,
    }


def llm_cost(model: str, prompt: int, completion: int) -> float:
    cost, _ = price_call(get_settings().llm_provider_for("answer"), model, "answer", prompt, completion)
    return cost


def _add_llm(result: PipelineResult, model: str, prompt: int, completion: int, purpose: str,
             latency_ms: int = 0) -> None:
    cost = llm_cost(model, prompt, completion)
    result.prompt_tokens += prompt
    result.completion_tokens += completion
    result.cost_usd = round(result.cost_usd + cost, 6)
    result.llm_calls.append(
        {"purpose": purpose, "model": model, "prompt_tokens": prompt,
         "completion_tokens": completion, "cost_usd": cost, "latency_ms": latency_ms}
    )


# --- vector -------------------------------------------------------------------------------


def run_vector(db: Session, scope: Scope, query: str, subject_id: uuid.UUID | None) -> PipelineResult:
    started = time.monotonic()
    out = retrieval_service.answer_query(
        db, scope, query, subject_id=subject_id, retrieval_mode="hybrid"
    )
    result = PipelineResult(pipeline=Pipeline.VECTOR, technique=None, query=query, answer=out["answer"])
    retrieval = out.get("retrieval", {})
    result.chunks = [{**c, "graph_score": None} for c in out["chunks"]]
    result.timings_ms = dict(retrieval.get("timings_ms", {}))
    usage = out.get("usage", {})
    if usage.get("prompt_tokens") or usage.get("completion_tokens"):
        _add_llm(result, get_settings().llm_answer_model, usage["prompt_tokens"],
                 usage["completion_tokens"], "answer", usage.get("latency_ms", 0))
    result.trace = {
        "stages": {
            k: retrieval.get(k) for k in (
                "dense_candidate_count", "lexical_candidate_count", "fused_candidate_count",
                "overlap_candidate_count", "mmr_candidate_count", "reranked_candidate_count",
                "final_candidate_count", "reranker_used", "fusion_method",
            )
        }
    }
    result.evidence = assess_evidence(result.chunks)
    result.total_ms = _ms(started)
    return result


# --- graph --------------------------------------------------------------------------------


def _candidate_from_row(row: RetrievalChunk, score: float) -> Candidate:
    return Candidate(
        chunk_id=row.id, source_id=row.source_id, subject_id=row.subject_id,
        extraction_id=row.extraction_id, content=row.content, embedding_text=row.embedding_text,
        token_count=row.token_count, content_type=row.content_type,
        section_path=row.section_path or [], page_start=row.page_start, page_end=row.page_end,
        similarity=score,
    )


def _graph_candidates(
    db: Session, rag_store: GraphRagStore, tenant_id, source_ids: list[str],
    evidence: GraphEvidence,
) -> tuple[list[Candidate], dict]:
    """Graph text units -> retrieval chunks (see `bridge`), best first."""
    unit_ids = evidence.ranked_chunk_ids()
    if not unit_ids:
        return [], {"units": 0, "mapped": 0}
    units = rag_store.base.chunks(tenant_id, unit_ids, source_ids)
    unit_texts = {u["id"]: u["text"] for u in units}
    unit_sources = {u["id"]: u["source_id"] for u in units}
    unit_scores = {u: s for u, s in evidence.chunk_scores.items() if u in unit_texts}
    involved = sorted({unit_sources[u] for u in unit_scores})
    rows = db.execute(
        select(RetrievalChunk).where(
            RetrievalChunk.source_id.in_([uuid.UUID(s) for s in involved]),
            RetrievalChunk.is_active.is_(True),
        )
    ).scalars().all()
    chunk_dicts = [{"id": r.id, "source_id": str(r.source_id), "content": r.content} for r in rows]
    mapped = map_units_to_chunks(unit_scores, unit_texts, unit_sources, chunk_dicts)
    by_id = {str(r.id): r for r in rows}
    candidates = [_candidate_from_row(by_id[m.chunk_id], m.score) for m in mapped if m.chunk_id in by_id]
    info = {"units": len(unit_scores), "mapped": len(candidates), "retrieval_chunks_in_sources": len(rows)}
    return candidates, info


def _graph_context(evidence: GraphEvidence) -> str:
    lines = list(evidence.context)
    facts = [f.sentence() for f in evidence.facts[:MAX_FACTS_IN_PROMPT]]
    if facts:
        lines.append("Knowledge-graph facts:\n" + "\n".join(f"- {f}" for f in facts))
    return "\n\n".join(lines)


def _answer(result: PipelineResult, query: str, chunks: list[Candidate], graph_context: str) -> None:
    t0 = time.monotonic()
    if not chunks and not graph_context:
        result.answer = retrieval_service._NO_CONTEXT_TEXT
        result.timings_ms["llm_ms"] = 0
        return
    user = retrieval_service._answer_prompt(query, chunks)
    if graph_context:
        user = graph_context + "\n\n" + user
    client = get_llm_client()
    model = get_settings().llm_answer_model
    answer, usage = client.chat_text(
        model, retrieval_service._ANSWER_SYSTEM + _GRAPH_SYSTEM_NOTE, user, interactive=True
    )
    result.answer = answer
    result.timings_ms["llm_ms"] = _ms(t0)
    _add_llm(result, model, usage.prompt_tokens, usage.completion_tokens, "answer", usage.latency_ms)


def _rerank(query: str, pool: list[Candidate], result: PipelineResult) -> list[Candidate]:
    settings = get_settings()
    pool = pool[: settings.retrieval_rerank_top_k]
    t0 = time.monotonic()
    try:
        ranked = get_reranker().rerank(query, [c.embedding_text or c.content for c in pool])
    except RerankerError as exc:
        log.warning("reranker unavailable, using retrieval order: %s", exc.message)
        result.timings_ms["rerank_ms"] = _ms(t0)
        return pool[: settings.retrieval_final_top_k]
    final = []
    for r in ranked[: settings.retrieval_final_top_k]:
        cand = pool[r.index]
        cand.rerank_score = r.score
        final.append(cand)
    result.timings_ms["rerank_ms"] = _ms(t0)
    return final


def _trace(evidence: GraphEvidence, info: dict, params: RetrievalParams) -> dict:
    nodes = [
        {"id": e.id, "name": e.name, "type": e.entity_type, "score": e.score,
         "hop": e.hop, "seed": e.seed}
        for e in evidence.entities
    ]
    known = {n["id"] for n in nodes}
    edges = [
        {"source": f.source_entity_id, "target": f.target_entity_id, "relation": f.relation,
         "score": f.score, "confidence": f.confidence, "description": f.description,
         "chunk_ids": f.chunk_ids}
        for f in evidence.facts
        if f.source_entity_id in known and f.target_entity_id in known
    ]
    model = get_settings().llm_graph_model
    llm = [
        {**u, "cost_usd": llm_cost(u.get("model", model), u["prompt"], u["completion"])}
        for u in evidence.llm_usage
    ]
    return {
        "technique": evidence.technique,
        "anchored": evidence.anchored,
        "seed_counts": evidence.seed_counts,
        "params": {
            "max_hops": params.max_hops, "decay": params.decay, "hub_damping": params.hub_damping,
            "seed_limit": params.seed_limit, "ppr_alpha": params.ppr_alpha,
        },
        "seeds": [{"id": s.id, "name": s.name, "type": s.entity_type, "score": s.score}
                  for s in evidence.seeds],
        "nodes": nodes,
        "edges": edges,
        "facts": [{"text": f.sentence(), "score": f.score, "source_id": f.source_id,
                   "chunk_ids": f.chunk_ids} for f in evidence.facts[:MAX_FACTS_IN_PROMPT]],
        "paths": evidence.paths[:6],
        "communities": evidence.communities,
        "notes": evidence.notes,
        "technique_timings_ms": evidence.timings_ms,
        "technique_llm_calls": llm,
        "bridge": info,
    }


def _prepare(db: Session, scope: Scope, query: str, subject_id: uuid.UUID):
    query = (query or "").strip()
    if not query:
        raise ValidationError("Query must not be empty")
    if len(query) > 4000:
        raise ValidationError("Query is too long (max 4000 characters)")
    subject, source_ids = graph_service.subject_source_ids(db, subject_id, scope, True)
    return query, subject, source_ids


def run_graph(
    db: Session, scope: Scope, rag_store: GraphRagStore, query: str, subject_id: uuid.UUID,
    params: RetrievalParams | None = None, *, hybrid: bool = False,
) -> PipelineResult:
    params = params or RetrievalParams()
    started = time.monotonic()
    query, subject, source_ids = _prepare(db, scope, query, subject_id)
    result = PipelineResult(
        pipeline=Pipeline.HYBRID if hybrid else Pipeline.GRAPH,
        technique=str(params.technique), query=query,
    )
    tenant_id = subject.tenant_id
    settings = get_settings()

    t0 = time.monotonic()
    strategy = embedding_service.prepare_query_strategy(db)
    qvec = embedder.embed_query(strategy.config, query)
    result.timings_ms["embed_ms"] = _ms(t0)

    ctx = Ctx(
        store=rag_store, tenant_id=tenant_id, source_ids=source_ids, subject_id=str(subject.id),
        query=query, query_vector=qvec, embedding_cfg=strategy.config, params=params,
    )
    t0 = time.monotonic()
    evidence = get_technique(params.technique)(ctx)
    result.timings_ms["graph_ms"] = _ms(t0)
    for u in evidence.llm_usage:
        _add_llm(result, u.get("model", settings.llm_graph_model), u["prompt"], u["completion"],
                 u.get("purpose", "graph"))

    t0 = time.monotonic()
    graph_cands, info = _graph_candidates(db, rag_store, tenant_id, source_ids, evidence)
    result.timings_ms["bridge_ms"] = _ms(t0)
    graph_score = {str(c.chunk_id): c.similarity for c in graph_cands}

    if hybrid:
        t0 = time.monotonic()
        vector_cands, vtrace = retrieval_service.hybrid_candidate_search(
            db, query, qvec, strategy.id, scope, subject_id=subject_id
        )
        result.timings_ms["vector_search_ms"] = _ms(t0)
        by_id = {str(c.chunk_id): c for c in vector_cands}
        for c in graph_cands:
            by_id.setdefault(str(c.chunk_id), c)
        vector_ids = [str(c.chunk_id) for c in vector_cands]
        graph_ids = [str(c.chunk_id) for c in graph_cands]
        fused = reciprocal_rank_fusion(
            [vector_ids] + [graph_ids] * max(1, params.hybrid_graph_weight), k=settings.retrieval_rrf_k
        )
        pool = [by_id[cid] for cid, _ in fused if cid in by_id][: settings.retrieval_hybrid_top_k]
        t0 = time.monotonic()
        retrieval_service._load_vectors(db, pool)
        pool = mmr_select(qvec, pool, top_k=settings.retrieval_mmr_top_k,
                          lambda_=settings.retrieval_mmr_lambda)
        result.timings_ms["mmr_ms"] = _ms(t0)
        info["vector_candidates"] = len(vector_cands)
        info["overlap_with_vector"] = len(set(vector_ids) & set(graph_ids))
        info.update({f"vector_{k}": v for k, v in vtrace.items()})
    else:
        pool = graph_cands

    final = _rerank(query, pool, result) if pool else []
    result.chunks = [
        {**retrieval_service._chunk_payload(c), "graph_score": graph_score.get(str(c.chunk_id))}
        for c in final
    ]
    _answer(result, query, final, _graph_context(evidence))
    result.evidence = assess_evidence(result.chunks, evidence.anchored)
    result.trace = _trace(evidence, info, params)
    result.total_ms = _ms(started)
    return result


# --- persistence --------------------------------------------------------------------------


def record_run(
    db: Session, result: PipelineResult, *, tenant_id, subject_id, compare_id: uuid.UUID | None = None
) -> RagRun:
    run = RagRun(
        tenant_id=tenant_id, subject_id=subject_id, compare_id=compare_id,
        pipeline=str(result.pipeline), technique=result.technique, query=result.query,
        answer=result.answer, status=result.status, error=result.error,
        total_ms=result.total_ms, prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens, cost_usd=result.cost_usd,
        timings_ms=result.timings_ms, chunk_ids=[c["chunk_id"] for c in result.chunks],
        trace=result.trace,
    )
    db.add(run)
    db.commit()
    return run


def failed(pipeline: Pipeline, technique: str | None, query: str, exc: Exception) -> PipelineResult:
    message = exc.message if isinstance(exc, (LLMError, RerankerError)) else str(exc)
    log.warning("pipeline %s failed: %s", pipeline, message)
    return PipelineResult(
        pipeline=pipeline, technique=technique, query=query, status="error",
        error=(message or type(exc).__name__)[:500],
    )
