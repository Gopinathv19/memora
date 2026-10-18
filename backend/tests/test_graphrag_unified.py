"""The parts of the unified runner that need no database or model (the full run is checked against real data)."""

import uuid
from types import SimpleNamespace

import pytest

from app.graphrag import pipelines, unified
from app.graphrag.params import Pipeline
from app.retrieval.reranker import RerankerError
from app.services.retrieval_service import Candidate


def cand(chunk_id=None, text="text"):
    return Candidate(
        chunk_id=chunk_id or uuid.uuid4(), source_id=uuid.uuid4(), subject_id=uuid.uuid4(), extraction_id=uuid.uuid4(),
        content=text, embedding_text=text, token_count=5, content_type="text", section_path=[], page_start=1, page_end=1,
    )


class BrokenReranker:
    def rerank(self, query, texts):
        raise RerankerError("down")


class OrderedReranker:
    """Scores the second text highest."""

    def rerank(self, query, texts):
        return [SimpleNamespace(index=i, score=float(i)) for i in range(len(texts))][::-1]


def result():
    return pipelines.PipelineResult(pipeline=Pipeline.UNIFIED, technique="unified", query="q")


def test_rerank_scores_and_orders_the_pool(monkeypatch):
    monkeypatch.setattr(unified, "get_reranker", lambda: OrderedReranker())
    pool = [cand(text="a"), cand(text="b"), cand(text="c")]
    final = unified.rerank("q", pool, 3, result())
    assert [c.content for c in final][:3] == ["c", "b", "a"] and final[0].rerank_score == 2.0


def test_rerank_only_looks_at_the_first_n_of_the_pool(monkeypatch):
    seen = []

    class Spy:
        def rerank(self, query, texts):
            seen.append(len(texts))
            return [SimpleNamespace(index=i, score=0.0) for i in range(len(texts))]

    monkeypatch.setattr(unified, "get_reranker", lambda: Spy())
    unified.rerank("q", [cand() for _ in range(10)], 4, result())
    assert seen == [4]


def test_a_failing_reranker_falls_back_to_the_fused_order(monkeypatch):
    monkeypatch.setattr(unified, "get_reranker", lambda: BrokenReranker())
    pool = [cand(text=t) for t in "abcdefghij"]
    final = unified.rerank("q", pool, 10, result())
    assert [c.content for c in final][:3] == ["a", "b", "c"] and all(c.rerank_score is None for c in final)


class FakeDb:
    """Returns the given rows for any select, like a database holding exactly them."""

    def __init__(self, rows):
        self.rows = rows
        self.queries = 0

    def execute(self, stmt):
        self.queries += 1
        return SimpleNamespace(scalars=lambda: iter(self.rows))


def row(chunk_id, source_id):
    return SimpleNamespace(id=chunk_id, source_id=source_id, subject_id=uuid.uuid4(), extraction_id=uuid.uuid4(),
                           content="from db", embedding_text="from db", token_count=5, content_type="text",
                           section_path=[], page_start=1, page_end=1)


def test_load_candidates_reuses_known_reads_the_rest_keeps_order_and_counts_stale_ids():
    known_c = cand()
    db_id, source = uuid.uuid4(), uuid.uuid4()
    gone = uuid.uuid4()
    ids = [str(db_id), str(known_c.chunk_id), str(gone), "not-a-uuid"]
    db = FakeDb([row(db_id, source)])
    out, stale = unified.load_candidates(db, ids, {str(known_c.chunk_id): known_c}, {str(db_id): 0.5}, [str(source)])
    assert [str(c.chunk_id) for c in out] == [str(db_id), str(known_c.chunk_id)]      # order of `ids`, stale ones dropped
    assert out[1] is known_c and out[0].content == "from db" and out[0].similarity == 0.5
    assert stale == 2 and db.queries == 1


def test_load_candidates_makes_no_query_when_everything_is_already_loaded():
    c = cand()
    db = FakeDb([])
    out, stale = unified.load_candidates(db, [str(c.chunk_id)], {str(c.chunk_id): c}, {}, [str(uuid.uuid4())])
    assert out == [c] and stale == 0 and db.queries == 0


def test_the_compare_request_accepts_the_unified_pipeline_alongside_the_others():
    from app.schemas.graphrag import CompareRequest

    req = CompareRequest(query="q", subject_id=uuid.uuid4(), pipelines=["vector", "graph", "hybrid", "unified"])
    assert Pipeline.UNIFIED in req.pipelines and len(req.pipelines) == 4
    with pytest.raises(Exception):
        CompareRequest(query="q", subject_id=uuid.uuid4(), pipelines=["vector", "graph", "hybrid", "unified", "unified"])


def test_the_abstention_text_says_it_could_not_find_the_answer():
    from app.graphrag.benchmark import is_abstention

    assert is_abstention(unified.ABSTAIN_TEXT)       # the benchmark's abstention scoring recognises it


def test_a_reranker_that_is_down_is_not_taken_for_missing_evidence():
    from app.graphrag.unified import assess

    unranked = [cand(), cand()]
    got = assess(unranked)
    assert got["weak"] is False and got["reranker_unavailable"] is True
    assert assess([])["weak"] is True                                                    # nothing retrieved is still weak
    scored = cand()
    scored.rerank_score = -9.0
    assert assess([scored])["weak"] is True and "reranker_unavailable" not in assess([scored])
    scored.rerank_score = 0.3
    assert assess([scored])["weak"] is False
