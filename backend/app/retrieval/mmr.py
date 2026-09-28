"""Maximal Marginal Relevance: pure result diversification, no I/O.

Stage 2 of the retrieval pipeline (strategy §3). MMR re-selects from the
HNSW candidates by balancing two terms for every candidate:

    MMR(d) = lambda * sim(d, query) - (1 - lambda) * max(sim(d, selected))

A chunk that says the same thing as an already-selected chunk loses ground
even when it is individually relevant, which is what removes near-duplicates
from the final context. ``lambda`` comes from settings
(``retrieval_mmr_lambda``), never a hard-coded constant.

The vectors here are the *stored* chunk embeddings -- the same ones the
HNSW scan returned -- so this stage costs no model calls. With L2-normalized
vectors, cosine similarity is a dot product.
"""

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Candidate is defined in the service layer; importing it at runtime
    # would create a cycle, and MMR only needs its shape.
    from app.services.retrieval_service import Candidate


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. On L2-normalized vectors this is the dot product;
    the explicit norm keeps the math correct for unnormalized inputs too."""
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return _dot(a, b) / (na * nb)


def mmr_select(
    query_vector: list[float],
    candidates: list["Candidate"],
    top_k: int,
    lambda_: float,
) -> list["Candidate"]:
    """Greedy MMR over the HNSW candidates.

    ``candidates`` carry their own vectors (``.vector``) and their HNSW
    cosine similarity to the query (``.similarity``). Returns up to
    ``top_k`` candidates, highest MMR first. Pure function: same inputs,
    same outputs, no DB, no provider.
    """
    if top_k <= 0 or not candidates:
        return []

    remaining = list(candidates)
    selected: list["Candidate"] = []

    while remaining and len(selected) < top_k:
        best = None
        best_score = -math.inf
        for cand in remaining:
            relevance = cand.similarity  # cosine(query, chunk), from HNSW
            redundancy = max(
                (_cosine(cand.vector, sel.vector) for sel in selected),
                default=0.0,
            )
            score = lambda_ * relevance - (1.0 - lambda_) * redundancy
            if score > best_score:
                best_score = score
                best = cand
        if best is None:
            break
        selected.append(best)
        remaining.remove(best)

    return selected
