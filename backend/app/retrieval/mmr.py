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

    # Each candidate's redundancy is the max cosine to the selected set. That
    # max only changes when a new chunk is selected, so keep it as a running
    # value and compare each candidate to the newest pick only -- instead of
    # re-comparing against every selected chunk on every round (cubic in the
    # pool size, in pure Python over 2048-dim vectors). Same values, same
    # order, same result as recomputing from scratch.
    norms = {id(c): math.sqrt(sum(x * x for x in c.vector)) for c in remaining}
    redundancy: dict[int, float] = {id(c): 0.0 for c in remaining}

    def cosine_to(cand: "Candidate", other: "Candidate") -> float:
        na, nb = norms[id(cand)], norms[id(other)]
        if na == 0.0 or nb == 0.0:
            return 0.0
        return _dot(cand.vector, other.vector) / (na * nb)

    while remaining and len(selected) < top_k:
        best = None
        best_score = -math.inf
        for cand in remaining:
            relevance = cand.similarity  # cosine(query, chunk), from HNSW
            score = lambda_ * relevance - (1.0 - lambda_) * redundancy[id(cand)]
            if score > best_score:
                best_score = score
                best = cand
        if best is None:
            break
        selected.append(best)
        remaining.remove(best)
        for cand in remaining:
            sim = cosine_to(cand, best)
            if len(selected) == 1 or sim > redundancy[id(cand)]:
                redundancy[id(cand)] = sim

    return selected
