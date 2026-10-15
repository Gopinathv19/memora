"""Seed entities: where a graph traversal starts.

Three signals, all answered by FalkorDB, merged into one weight per entity:

* **vector**   the question against `Entity.embedding` (semantic match, catches
               paraphrases and aliases the text signals miss)
* **key**      exact canonical-key / alias match on 1-4 word phrases of the question
* **fulltext** the entity full-text index

An entity found by more than one signal is boosted. Weights are scaled so the
strongest seed is 1.0; hop decay and PPR then start from these weights.
"""

from redis.exceptions import ResponseError

from app.graph.normalization import canonical_key
from app.graph.retrieval import fulltext_query, query_keys
from app.graphrag.params import RetrievalParams
from app.graphrag.types import ScoredEntity

KEY_WEIGHT = 1.0
FULLTEXT_WEIGHT = 0.7
MULTI_SIGNAL_BONUS = 0.15
MIN_VECTOR_SIMILARITY = 0.15
# Entity-name embeddings are a weak, uncalibrated signal here (measured: the best
# entity for a question the documents cannot answer is as "similar" as the best for
# one they can), so a vector seed counts for at most this much next to a name match.
VECTOR_WEIGHT_CAP = 0.5
# A vector hit must also be close to the best vector hit: with a weak absolute
# floor alone, every entity of a dense graph is "somewhat similar" to any
# question, and each spurious seed starts its own spread (or, for paths, its
# own pair to connect).
VECTOR_RELATIVE_FLOOR = 0.6


def gather_seeds(
    rag_store,
    tenant_id,
    source_ids: list[str],
    query: str,
    query_vector: list[float] | None,
    params: RetrievalParams,
    extra_keys: list[str] | None = None,
) -> tuple[list[ScoredEntity], dict[str, int]]:
    """Returns (seeds best first, how many each signal contributed)."""
    base = rag_store.base
    weights: dict[str, float] = {}
    hits: dict[str, int] = {}
    counts = {"vector": 0, "key": 0, "fulltext": 0}

    def add(entity_id: str, weight: float, signal: str) -> None:
        counts[signal] += 1
        hits[entity_id] = hits.get(entity_id, 0) + 1
        weights[entity_id] = max(weights.get(entity_id, 0.0), weight)

    limit = params.seed_limit
    if query_vector is not None:
        rows = rag_store.vector_entities(tenant_id, query_vector, source_ids, limit * 2)
        best = max((float(r["similarity"]) for r in rows), default=0.0)
        floor = max(MIN_VECTOR_SIMILARITY, VECTOR_RELATIVE_FLOOR * best)
        for row in rows:
            sim = float(row["similarity"])
            if sim >= floor:
                add(row["id"], min(sim, 1.0) * VECTOR_WEIGHT_CAP, "vector")

    keys = query_keys(query)
    for name in extra_keys or []:
        for key in {canonical_key(name), canonical_key(name, "COMPANY")}:
            if key and key not in keys:
                keys.append(key)
    for row in base.find_entities_by_keys(tenant_id, keys, source_ids, limit):
        add(row["id"], KEY_WEIGHT, "key")

    text = fulltext_query(query)
    if text:
        try:
            rows = base.search_entities(tenant_id, text, source_ids, limit)
        except ResponseError:  # a query the full-text syntax rejects
            rows = []
        for row in rows:
            add(row["id"], FULLTEXT_WEIGHT, "fulltext")

    for entity_id, n in hits.items():
        if n > 1:
            weights[entity_id] += MULTI_SIGNAL_BONUS * (n - 1)
    if not weights:
        return [], counts

    top = sorted(weights, key=lambda e: (-weights[e], e))[:limit]
    # Scale down only when a bonus pushed a weight past 1. Scaling the best seed *up*
    # to 1.0 (as before) turned a noise match into a perfect one.
    peak = max(1.0, max(weights[e] for e in top))
    info = rag_store.entities_info(tenant_id, top)
    seeds = [
        ScoredEntity(
            id=e,
            name=info.get(e, {}).get("name", e),
            entity_type=info.get(e, {}).get("entity_type", "CONCEPT"),
            score=round(weights[e] / peak, 4),
            hop=0,
            seed=True,
        )
        for e in top
    ]
    return seeds, counts
