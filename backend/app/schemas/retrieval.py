"""Pydantic schemas for the retrieval pipeline (POST /query)."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class QueryRequest(BaseModel):
    """What the console or an API consumer sends to ask Memora a question."""

    query: str
    # Optional scope narrowing. Null means "everything I own".
    subject_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    strategy_id: uuid.UUID | None = None


class RetrievedChunk(BaseModel):
    """One supporting chunk, with its per-stage scores."""

    chunk_id: str
    source_id: str
    subject_id: str
    content: str
    content_type: str
    section_path: list = []
    page_start: int | None = None
    page_end: int | None = None
    token_count: int
    similarity: float
    rerank_score: float | None = None


class RetrievalTrace(BaseModel):
    """Pipeline observability: how many candidates each stage produced."""

    hnsw_candidates: int
    mmr_candidates: int
    final_chunks: int
    reranker_used: bool


class QueryUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class QueryResponse(BaseModel):
    """The answer plus everything the frontend needs to show its evidence."""

    query: str
    answer: str
    strategy_id: str
    strategy_name: str
    chunks: list[RetrievedChunk]
    retrieval: RetrievalTrace
    usage: QueryUsage
