"""The unified pipeline: vector + atomic facts + passage-aware walk, reranked, answered (plan Step 5).

    embed the question; run the *vector* search (read-only, the existing function)
    analyse the question (kind and names): sets channel weights, never gates
    gather the atom and walk channels and fuse all three         `retrieve.gather`
    rerank the pool with the shared reranker
    if even the best chunk scores below the weak-evidence line: widen once (3x more of every channel)
    if it is still weak: say so and show the closest passages, instead of answering from them
    otherwise answer, with the graph's facts and community summaries ahead of the chunks

Chunk ids are real `retrieval_chunks` ids, so no bridge is needed: a fused id is loaded from Postgres
directly (active chunks of the scope's sources only; a graph chunk that was re-chunked away is skipped
and counted). It is reached only through the graph endpoints (`/compare/*`); `/query` is not touched.

The answer step is `answer.py` (numbered evidence, required citations, deterministic checks). The analyser's model call is not metered in `cost_usd` (the cache hides
its usage); it is a few dozen tokens.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import RetrievalChunk, Source
from app.graphrag import answer, embedder, pipelines, retrieve
from app.graphrag.analyse import analyse
from app.graphrag.params import Pipeline, RetrievalParams
from app.graphrag.store import GraphRagStore
from app.llm.client import get_llm_client
from app.retrieval.reranker import RerankerError, get_reranker
from app.services import embedding_service, retrieval_service
from app.services.retrieval_service import Candidate
from app.services.scope import Scope

log = logging.getLogger(__name__)

ABSTAIN_TEXT = (
    "I couldn't find this in your documents. The closest passages I found are listed below, "
    "but none of them clearly answers the question."
)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def load_candidates(
    db: Session, ids: list[str], known: dict[str, Candidate], scores: dict[str, float], source_ids: list[str],
) -> tuple[list[Candidate], int]:
    """Candidates in the order of `ids`. Ids the vector search already loaded are reused; the rest are read
    from Postgres, active chunks of the scope's sources only. Returns (candidates, ids that no longer exist)."""
    missing = [i for i in ids if i not in known]
    rows: dict[str, RetrievalChunk] = {}
    if missing:
        wanted = []
        for i in missing:
            try:
                wanted.append(uuid.UUID(i))
            except ValueError:
                continue
        if wanted:
            found = db.execute(
                select(RetrievalChunk).where(
                    RetrievalChunk.id.in_(wanted), RetrievalChunk.is_active.is_(True),
                    RetrievalChunk.source_id.in_([uuid.UUID(s) for s in source_ids]),
                )
            ).scalars()
            rows = {str(r.id): r for r in found}
    out, stale = [], 0
    for i in ids:
        if i in known:
            out.append(known[i])
        elif i in rows:
            out.append(pipelines._candidate_from_row(rows[i], scores.get(i, 0.0)))
        else:
            stale += 1
    return out, stale


def rerank(query: str, pool: list[Candidate], n_in: int, result: pipelines.PipelineResult) -> list[Candidate]:
    settings = get_settings()
    pool = pool[:n_in]
    started = time.monotonic()
    try:
        ranked = get_reranker().rerank(query, [c.embedding_text or c.content for c in pool])
    except RerankerError as exc:
        log.warning("reranker unavailable, using fused order: %s", exc.message)
        result.timings_ms["rerank_ms"] = result.timings_ms.get("rerank_ms", 0) + _ms(started)
        return pool[: settings.retrieval_final_top_k]
    final = []
    for r in ranked[: settings.retrieval_final_top_k]:
        cand = pool[r.index]
        cand.rerank_score = r.score
        final.append(cand)
    result.timings_ms["rerank_ms"] = result.timings_ms.get("rerank_ms", 0) + _ms(started)
    return final


def source_titles(db: Session, source_ids: list[str]) -> dict[str, str]:
    """Document names for the evidence headers (one query for the scope's sources)."""
    if not source_ids:
        return {}
    rows = db.execute(
        select(Source.id, Source.filename).where(Source.id.in_([uuid.UUID(s) for s in source_ids]))
    ).all()
    return {str(i): name for i, name in rows}


def assess(final: list[Candidate]) -> dict:
    """Is the evidence weak? A reranker that is down gives no scores; that says nothing about the evidence, so it is
    not weak (the pipeline then answers from the fused order, as the vector pipeline does when its reranker fails)."""
    if final and all(c.rerank_score is None for c in final):
        return {"best_rerank": None, "weak": False, "anchored": None, "reranker_unavailable": True}
    return pipelines.assess_evidence([{"rerank_score": c.rerank_score} for c in final])


@dataclass
class Retrieved:
    """What retrieval produced for one question, before any answer is written."""

    result: pipelines.PipelineResult        # timings so far; the caller fills in the answer
    final: list[Candidate]                  # reranked, best first
    ret: retrieve.Retrieval
    evidence: dict                          # `pipelines.assess_evidence` of `final`
    widened: bool
    stale: int                              # fused graph chunk ids that no longer exist
    pool_size: int
    vtrace: dict
    subject: object
    source_ids: list[str]
    vector_final: list[Candidate] | None = None   # vector search alone, same reranker (only when asked for)
    started: float = 0.0


def retrieve_chunks(
    db: Session, scope: Scope, rag_store: GraphRagStore, query: str, subject_id: uuid.UUID,
    params: RetrievalParams | None = None, *, client=None, vector_baseline: bool = False,
) -> Retrieved:
    """The retrieval half of the pipeline: vector search, analysis, the three channels, fusion, rerank, and one
    widening if the evidence is weak. `run_unified` writes the answer on top of it; the offline benchmark uses it
    alone. `vector_baseline` also reranks the vector candidates by themselves (one more reranker call), the
    comparison the benchmark needs."""
    params = params or RetrievalParams()
    settings = get_settings()
    started = time.monotonic()
    query, subject, source_ids = pipelines._prepare(db, scope, query, subject_id)
    result = pipelines.PipelineResult(pipeline=Pipeline.UNIFIED, technique="unified", query=query)
    tenant_id = subject.tenant_id

    t0 = time.monotonic()
    strategy = embedding_service.prepare_query_strategy(db)
    cfg = strategy.config
    qvec = embedder.embed_query(cfg, query)
    result.timings_ms["embed_ms"] = _ms(t0)

    t0 = time.monotonic()
    vector_cands, vtrace = retrieval_service.hybrid_candidate_search(
        db, query, qvec, strategy.id, scope, subject_id=subject_id
    )
    result.timings_ms["vector_search_ms"] = _ms(t0)
    known = {str(c.chunk_id): c for c in vector_cands}
    vector_ids = [str(c.chunk_id) for c in vector_cands]

    t0 = time.monotonic()
    analysis = analyse(client or get_llm_client(), settings, query, use_llm=params.unified_analyser)
    result.timings_ms["analyse_ms"] = _ms(t0)

    def attempt(wide: bool):
        t = time.monotonic()
        ret = retrieve.gather(
            rag_store, tenant_id, source_ids, str(subject.id), query, qvec, vector_ids, analysis, params,
            embed_query=lambda text: embedder.embed_query(cfg, text), wide=wide,
        )
        result.timings_ms["gather_ms" if not wide else "gather_wide_ms"] = _ms(t)
        n_in = settings.retrieval_rerank_top_k * (retrieve.WIDE_FACTOR if wide else 1)
        pool, stale = load_candidates(db, ret.fused[:n_in], known, ret.fused_scores, source_ids)
        final = rerank(query, pool, n_in, result) if pool else []
        return ret, final, stale, len(pool)

    ret, final, stale, pool_size = attempt(wide=False)
    evidence = assess(final)
    widened = False
    if evidence["weak"] and params.unified_widen:
        widened = True
        ret, final, stale, pool_size = attempt(wide=True)
        evidence = assess(final)

    vector_final = None
    if vector_baseline:
        scratch = pipelines.PipelineResult(pipeline=Pipeline.VECTOR, technique=None, query=query)
        vector_final = rerank(query, vector_cands, settings.retrieval_rerank_top_k, scratch) if vector_cands else []
    return Retrieved(result, final, ret, evidence, widened, stale, pool_size, vtrace, subject, source_ids,
                     vector_final, started)


def run_unified(
    db: Session, scope: Scope, rag_store: GraphRagStore, query: str, subject_id: uuid.UUID,
    params: RetrievalParams | None = None, *, client=None,
) -> pipelines.PipelineResult:
    params = params or RetrievalParams()
    settings = get_settings()
    got = retrieve_chunks(db, scope, rag_store, query, subject_id, params, client=client)
    result, final, ret, evidence = got.result, got.final, got.ret, got.evidence
    query = result.query
    source_ids = got.source_ids

    scores = ret.fused_scores
    result.chunks = [
        {**retrieval_service._chunk_payload(c), "graph_score": round(scores.get(str(c.chunk_id), 0.0), 5),
         "channels": ret.channels_of(str(c.chunk_id))}
        for c in final
    ]
    answered = None
    if evidence["weak"]:
        result.answer = ABSTAIN_TEXT
        result.timings_ms["llm_ms"] = 0
        evidence["abstained"] = True
    else:
        t0 = time.monotonic()
        evidence_blocks = answer.build_evidence(
            final, ret.atoms, ret.communities, source_titles(db, source_ids),
            load_chunk=lambda chunk_id: next(iter(load_candidates(db, [chunk_id], {}, {}, source_ids)[0]), None),
        )
        gen = answer.generate(client or get_llm_client(), settings, query, evidence_blocks, check=params.answer_llm_check)
        result.answer, result.citations, result.verification = gen.answer, gen.citations, gen.verification
        result.timings_ms["llm_ms"] = _ms(t0)
        pipelines._add_llm(result, settings.llm_answer_model, gen.usage.prompt_tokens, gen.usage.completion_tokens,
                           "answer", gen.usage.latency_ms)
        answered = {"blocks": len(evidence_blocks.blocks), "dropped_facts": evidence_blocks.dropped_facts,
                    "pulled_chunks": evidence_blocks.pulled_chunks, "llm_check": gen.llm_check}
    result.evidence = evidence
    result.trace = {
        **ret.trace(), "widened": got.widened, "pool": got.pool_size, "stale_graph_chunks": got.stale,
        "answer": answered,
        "vector_stages": {k: v for k, v in got.vtrace.items() if k.endswith("_count") or k == "fusion_method"},
    }
    result.total_ms = _ms(got.started)
    return result
