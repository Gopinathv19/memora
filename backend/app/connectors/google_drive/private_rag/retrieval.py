"""In-memory retrieval over an ephemeral corpus.

The same stages as the persistent pipeline (embed -> ANN -> MMR -> rerank ->
answer), but the "index" is a list in this process's memory: the ANN stage is
an exact cosine scan over the request's own chunks, and everything is
garbage-collected when the request ends. No pgvector, no embeddings table,
no retrieval_chunks rows -- the corpus never exists outside this request.

The embedding provider is called directly (no strategy row exists in private
mode: a strategy is a *stored* embedding configuration, and nothing is stored
here). The model comes from settings; the dimensions default to the model's
native size, and the provider sheds the parameter if its deployment rejects
it -- the same fallback the persistent path relies on.
"""

import logging
import math

from app.core.config import Settings, get_settings
from app.embeddings.provider import get_embedding_provider
from app.retrieval.answer import generate_answer, query_fingerprint
from app.retrieval.mmr import mmr_select
from app.retrieval.reranker import RerankerError, get_reranker

log = logging.getLogger(__name__)


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity in pure Python: exact, and fast enough for a corpus
    capped at a few hundred chunks."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = norm_a = norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def answer_private_query(
    query: str, chunks: list, settings: Settings | None = None
) -> dict:
    """Run the full ephemeral pipeline and return answer + evidence + usage.

    `chunks` are `PrivateChunk`s; their `vector`/`similarity`/`rerank_score`
    fields are filled in place as the stages run.
    """
    settings = settings or get_settings()
    if not chunks:
        answer, usage = generate_answer(query, [])
        return {"answer": answer, "evidence": [], "usage": _usage_payload(usage)}

    provider = get_embedding_provider()
    model = settings.embedding_model
    dimensions = settings.private_rag_embedding_dimensions

    # --- Stage 0: embeddings (query + corpus, in batches) ---------------------
    query_vector, _ = provider.embed_query(model, query, dimensions)
    texts = [c.embedding_text or c.content for c in chunks]
    batch = max(1, settings.embedding_batch_size)
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch):
        batch_vectors, _ = provider.embed_documents(
            model, texts[i : i + batch], dimensions
        )
        vectors.extend(batch_vectors)
    for chunk, vector in zip(chunks, vectors):
        chunk.vector = vector
        chunk.similarity = _cosine(query_vector, vector)

    # --- Stage 1: exact cosine scan (the ephemeral stand-in for HNSW) ---------
    pool = sorted(chunks, key=lambda c: c.similarity, reverse=True)[
        : settings.retrieval_hnsw_top_k
    ]

    # --- Stage 2: MMR (the same pure module the persistent pipeline uses) -----
    mmr_picked = mmr_select(
        query_vector,
        pool,
        top_k=settings.retrieval_mmr_top_k,
        lambda_=settings.retrieval_mmr_lambda,
    )

    # --- Stage 3: reranker, with the same MMR fallback -------------------------
    passages = [c.embedding_text or c.content for c in mmr_picked]
    try:
        ranked = get_reranker().rerank(query, passages)
    except RerankerError as exc:
        # The reranker is an enhancement, not a dependency.
        log.warning("reranker unavailable, using MMR order: %s", exc.message)
        final = mmr_picked[: settings.retrieval_final_top_k]
    else:
        final = []
        for result in ranked[: settings.retrieval_final_top_k]:
            candidate = mmr_picked[result.index]
            candidate.rerank_score = result.score
            final.append(candidate)

    # --- Stage 4: the shared grounded-answer stage -----------------------------
    answer, usage = generate_answer(query, final)
    log.info(
        "private retrieval: %s corpus=%d final=%d",
        query_fingerprint(query),
        len(chunks),
        len(final),
    )
    return {"answer": answer, "evidence": final, "usage": _usage_payload(usage)}


def _usage_payload(usage) -> dict:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "latency_ms": usage.latency_ms,
    }
