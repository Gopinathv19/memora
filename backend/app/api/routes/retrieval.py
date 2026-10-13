"""The retrieval endpoint: ask Memora a question, get a grounded answer.

POST /query runs the full pipeline synchronously (query embedding -> HNSW ->
MMR -> reranker -> LLM). It is a read-only operation over the caller's own
knowledge: no rows are written, so there is nothing to run in the background
and nothing to poll -- the response carries the answer and its evidence.
"""

import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentScope, DbSession
from app.llm.client import LLMError
from app.schemas.retrieval import QueryRequest, QueryResponse
from app.services import retrieval_service

log = logging.getLogger(__name__)
router = APIRouter(tags=["retrieval"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
        retrieval_mode=payload.retrieval_mode,
    )


@router.post("/query/stream")
def ask_stream(
    payload: QueryRequest,
    db: DbSession,
    scope: CurrentScope,
):
    """Same pipeline as `POST /query`, but the answer streams as it is written.

    Server-sent events: `start` (evidence chunks + retrieval trace, sent as
    soon as retrieval finishes), then `delta` pieces of the answer, then
    `done` (token usage), or `error` if generation fails. Retrieval errors
    (validation, auth, embedding failures) are ordinary HTTP errors raised
    before the stream opens.
    """
    outcome = retrieval_service.retrieve_context(
        db,
        scope,
        payload.query,
        subject_id=payload.subject_id,
        source_id=payload.source_id,
        strategy_id=payload.strategy_id,
        retrieval_mode=payload.retrieval_mode,
    )
    start = retrieval_service.stream_start(outcome)
    # Retrieval is finished: give the pooled connection back before the long
    # LLM call, so a slow answer does not hold a database connection.
    db.close()

    def events():
        yield _sse("start", start)
        try:
            for kind, value in retrieval_service.stream_answer(outcome):
                if kind == "delta":
                    yield _sse("delta", {"text": value})
                else:
                    yield _sse(
                        "done",
                        {
                            "usage": {
                                "prompt_tokens": value.prompt_tokens,
                                "completion_tokens": value.completion_tokens,
                                "latency_ms": value.latency_ms,
                            }
                        },
                    )
        except LLMError as exc:
            log.warning("answer stream failed: %s", exc)
            yield _sse("error", {"message": "The answer could not be generated."})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
