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

Cost: the redundancy term only changes for the chunk picked in the previous
round, so each round computes one cosine per remaining candidate against
that newest pick and keeps a running maximum, with every norm computed once
up front. For 50 candidates of 2048 dimensions that is ~450 dot products
instead of recomputing every pair every round, which is what made this stage
take seconds on a small CPU. Same selection, same order: the running maximum
is exactly ``max(...)`` over the selected set, and ties still go to the
earlier candidate.
"""

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Candidate is defined in the service layer; importing it at runtime
    # would create a cycle, and MMR only needs its shape.
    from app.services.retrieval_service import Candidate


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(a: list[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. On L2-normalized vectors this is the dot product;
    the explicit norm keeps the math correct for unnormalized inputs too."""
    na = _norm(a)
    nb = _norm(b)
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

    norms = [_norm(c.vector) for c in candidates]
    # Highest cosine similarity seen so far between each candidate and any
    # selected chunk. None until something has been selected (redundancy 0).
    redundancy: list[float | None] = [None] * len(candidates)
    remaining = list(range(len(candidates)))
    selected: list["Candidate"] = []

    while remaining and len(selected) < top_k:
        best_index = None
        best_score = -math.inf
        for i in remaining:
            relevance = candidates[i].similarity  # cosine(query, chunk), from HNSW
            red = redundancy[i]
            score = lambda_ * relevance - (1.0 - lambda_) * (red if red is not None else 0.0)
            if score > best_score:
                best_score = score
                best_index = i
        if best_index is None:
            break
        picked = candidates[best_index]
        selected.append(picked)
        remaining.remove(best_index)

        # Only the newest pick can raise anyone's redundancy.
        pn = norms[best_index]
        for i in remaining:
            ni = norms[i]
            sim = 0.0 if ni == 0.0 or pn == 0.0 else _dot(candidates[i].vector, picked.vector) / (ni * pn)
            if redundancy[i] is None or sim > redundancy[i]:
                redundancy[i] = sim

    return selected
