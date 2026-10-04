"""The retrieval pipeline: query embedding -> HNSW -> MMR -> reranker -> LLM.

Each stage lives in its own module with a clean interface, so any single
stage can be replaced without touching the others (strategy §7):

* ``app.retrieval.mmr``       -- pure result diversification (no I/O)
* ``app.retrieval.reranker``  -- the reranking provider boundary
* ``app.services.retrieval_service`` -- orchestration + the HNSW SQL
"""

from app.retrieval.mmr import mmr_select
from app.retrieval.reranker import (
    RerankerError,
    RerankerProvider,
    get_reranker,
    rerank,
)

__all__ = [
    "mmr_select",
    "RerankerError",
    "RerankerProvider",
    "get_reranker",
    "rerank",
]
