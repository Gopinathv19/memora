"""Candidate fusion for hybrid retrieval (docs/embedding-retrieval.md).

Reciprocal Rank Fusion (RRF) combines ranked lists by rank position rather
than by raw score, so it never tries to put cosine similarity and BM25-style
lexical scores on the same scale. The conventional smoothing constant is
k=60 (Cormack et al., "Reciprocal Rank Fusion Outperforms Condorcet and
Individual Rank Learning Methods", SIGIR 2009).
"""

from collections import defaultdict


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse ranked id lists into one ranked (id, rrf_score) list.

    Each list's item at rank r (1-based) contributes 1 / (k + r) to its id's
    score; scores are summed across lists. Ties are broken by id so the
    output order is deterministic.
    """
    scores: dict[str, float] = defaultdict(float)

    for ranked_ids in ranked_lists:
        for rank, chunk_id in enumerate(ranked_ids, start=1):
            scores[chunk_id] += 1.0 / (k + rank)

    return sorted(
        scores.items(),
        key=lambda item: (-item[1], item[0]),
    )