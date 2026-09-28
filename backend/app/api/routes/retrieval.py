"""The retrieval endpoint: ask Memora a question, get a grounded answer.

POST /query runs the full pipeline synchronously (query embedding -> HNSW ->
MMR -> reranker -> LLM). It is a read-only operation over the caller's own
knowledge: no rows are written, so there is nothing to run in the background
and nothing to poll -- the response carries the answer and its evidence.
"""

from fastapi import APIRouter

from app.api.deps import CurrentScope, DbSession
from app.schemas.retrieval import QueryRequest, QueryResponse
from app.services import retrieval_service

router = APIRouter(tags=["retrieval"])


@router.post("/query", response_model=QueryResponse)
def ask(
    payload: QueryRequest,
    db: DbSession,
    scope: CurrentScope,
):
    """Answer a question from the caller's embedded chunks.

    The scope decides what knowledge exists to answer from: a console user
    queries every tenant they own, a credential queries its own application.
    Optional `subject_id`/`source_id` narrow the search further.
    """
    return retrieval_service.answer_query(
        db,
        scope,
        payload.query,
        subject_id=payload.subject_id,
        source_id=payload.source_id,
        strategy_id=payload.strategy_id,
    )
