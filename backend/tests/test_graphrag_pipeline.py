"""`run_graph` end to end: a real FalkorDB, fakes for Postgres, the reranker and the LLM.

Self-contained like `test_graphrag_techniques.py` (run with `--noconftest`).
"""

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("falkordb")

from tests.test_graphrag_techniques import (  # noqa: E402,F401
    DIM, S1, S2, SUBJECT, TENANT, falkor, fake_embed, indexed, rag,
)
from app.graphrag import pipelines  # noqa: E402
from app.graphrag.params import Pipeline, RetrievalParams, Technique  # noqa: E402
from app.retrieval.reranker import RerankerError  # noqa: E402


def rc(source: str, index: int, content: str | None = None) -> SimpleNamespace:
    """A fake `retrieval_chunks` row whose text matches graph unit `{source}:1:{index}`."""
    return SimpleNamespace(
        id=uuid.uuid4(), source_id=uuid.UUID(source), subject_id=uuid.UUID(SUBJECT),
        extraction_id=uuid.uuid4(), content=content or f"chunk {index} of {source[:2]}",
        embedding_text=f"chunk {index} of {source[:2]}", token_count=5, content_type="text",
        section_path=[], page_start=1, page_end=1,
    )


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, stmt):
        rows = self.rows
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


class FakeLLM:
    def __init__(self):
        self.users: list[str] = []
        self.systems: list[str] = []

    def chat_text(self, model, system, user, interactive=False):
        self.users.append(user)
        self.systems.append(system)
        return "Alice works for Acme.", SimpleNamespace(prompt_tokens=100, completion_tokens=20, latency_ms=7)


class FakeReranker:
    def rerank(self, query, passages):
        # score by passage length so the order is deterministic and not retrieval order
        order = sorted(range(len(passages)), key=lambda i: -len(passages[i]))
        return [SimpleNamespace(index=i, score=float(len(passages[i]))) for i in order]


@pytest.fixture
def wired(indexed, monkeypatch):
    rag, _ = indexed
    rows = [rc(S1, i) for i in range(3)] + [rc(S2, i) for i in range(3)]
    llm = FakeLLM()
    monkeypatch.setattr(pipelines, "get_llm_client", lambda: llm)
    monkeypatch.setattr(pipelines, "get_reranker", lambda: FakeReranker())
    monkeypatch.setattr(pipelines, "llm_cost", lambda model, p, c: round((p + c) * 1e-6, 6))
    monkeypatch.setattr(
        pipelines, "_prepare",
        lambda db, scope, query, subject_id: (query.strip(), SimpleNamespace(id=uuid.UUID(SUBJECT), tenant_id=TENANT), [S1, S2]),
    )
    monkeypatch.setattr(
        pipelines.embedding_service, "prepare_query_strategy",
        lambda db: SimpleNamespace(id=uuid.uuid4(), config=SimpleNamespace(dimension=DIM)),
    )
    monkeypatch.setattr(pipelines.embedder, "embed_query", lambda cfg, t: fake_embed(t))
    return rag, FakeDB(rows), rows, llm


def run(wired, query="Alice", **params):
    rag, db, rows, llm = wired
    result = pipelines.run_graph(db, SimpleNamespace(), rag, query, uuid.UUID(SUBJECT), RetrievalParams(**params))
    return result, rows, llm


def test_run_graph_returns_retrieval_chunks_with_graph_scores(wired):
    result, rows, llm = run(wired, "Alice")
    assert result.status == "ok" and result.pipeline == Pipeline.GRAPH and result.technique == "spreading"
    ids = {str(r.id) for r in rows}
    assert result.chunks and all(c["chunk_id"] in ids for c in result.chunks)
    assert all(c["graph_score"] is not None and 0 < c["graph_score"] <= 1 for c in result.chunks)
    # Alice's facts live in S1; the other source is never cited
    assert all(c["source_id"] == S1 for c in result.chunks)
    assert result.answer == "Alice works for Acme."


def test_run_graph_prompt_carries_facts_and_the_graph_note(wired):
    result, _, llm = run(wired, "Alice")
    prompt = llm.users[0]
    assert "Knowledge-graph facts:" in prompt and "Alice --WORKS_FOR--> Acme" in prompt
    assert "Context chunks:" in prompt
    assert "knowledge-graph facts" in llm.systems[0]


def test_run_graph_reports_timings_cost_and_trace(wired):
    result, _, _ = run(wired, "Alice")
    for key in ("embed_ms", "graph_ms", "bridge_ms", "rerank_ms", "llm_ms"):
        assert key in result.timings_ms
    assert result.prompt_tokens == 100 and result.completion_tokens == 20
    assert result.cost_usd == pytest.approx(0.00012)
    assert result.llm_calls[0]["purpose"] == "answer"
    trace = result.trace
    assert trace["technique"] == "spreading" and trace["seeds"][0]["name"] == "Alice"
    assert {n["name"] for n in trace["nodes"]} >= {"Alice", "Acme"}
    assert all(e["source"] in {n["id"] for n in trace["nodes"]} for e in trace["edges"])
    assert trace["bridge"]["mapped"] >= 1


def test_no_seed_means_no_chunks_and_an_honest_answer(wired):
    result, _, llm = run(wired, "zzzz qqqq")
    assert result.chunks == [] and result.status == "ok"
    assert llm.users == []  # nothing to answer from: the LLM is not called
    assert "does not contain enough information" in result.answer
    assert result.cost_usd == 0


def test_reranker_failure_falls_back_to_graph_order(wired, monkeypatch):
    class Down:
        def rerank(self, q, p):
            raise RerankerError("down")

    monkeypatch.setattr(pipelines, "get_reranker", lambda: Down())
    result, _, _ = run(wired, "Alice")
    scores = [c["graph_score"] for c in result.chunks]
    assert result.status == "ok" and scores == sorted(scores, reverse=True)


def test_hybrid_fuses_vector_and_graph_candidates(wired, monkeypatch):
    rag, db, rows, llm = wired
    only_vector = rc(S2, 2, "an unrelated chunk only the vector search found")
    only_vector.embedding_text = only_vector.content
    from app.services.retrieval_service import Candidate

    def cand(r, sim):
        return Candidate(
            chunk_id=r.id, source_id=r.source_id, subject_id=r.subject_id, extraction_id=r.extraction_id,
            content=r.content, embedding_text=r.embedding_text, token_count=5, content_type="text",
            section_path=[], page_start=1, page_end=1, similarity=sim,
        )

    monkeypatch.setattr(
        pipelines.retrieval_service, "hybrid_candidate_search",
        lambda *a, **k: ([cand(only_vector, 0.9)], {"dense_candidate_count": 1}),
    )
    monkeypatch.setattr(pipelines.retrieval_service, "_load_vectors", lambda db, pool: None)
    monkeypatch.setattr(pipelines, "mmr_select", lambda q, pool, top_k, lambda_: pool)
    result = pipelines.run_graph(
        db, SimpleNamespace(), rag, "Alice", uuid.UUID(SUBJECT), RetrievalParams(), hybrid=True
    )
    ids = {c["chunk_id"] for c in result.chunks}
    assert str(only_vector.id) in ids  # from the vector list
    assert any(c["graph_score"] for c in result.chunks)  # and graph-found ones
    assert result.pipeline == Pipeline.HYBRID and "vector_search_ms" in result.timings_ms


def test_community_context_goes_into_the_prompt(wired, monkeypatch):
    from app.graphrag import build

    rag, db, rows, llm = wired
    monkeypatch.setattr(build, "summarize_community", lambda m, f: (
        {"title": "Acme people", "summary": "Acme employees Alice Bob Carol and the Apollo project"},
        {"model": "m", "prompt": 1, "completion": 1, "purpose": "community summary"}))
    stats = {"entities_embedded": 0, "facts_embedded": 0, "facts_removed": 0, "communities": 0,
             "embedding_tokens": 0, "llm_usage": []}
    class Cfg: dimension = DIM
    build.build_communities(rag, TENANT, SUBJECT, [S1, S2], Cfg(), stats)
    result, _, llm = run(wired, "Acme employees Alice Bob", technique=Technique.COMMUNITY)
    assert "Community \"Acme people\"" in llm.users[0]
    assert result.trace["communities"][0]["title"] == "Acme people"


def test_vector_lens_marks_entities_covered_by_vector_chunks(indexed):
    from app.graphrag.compare import vector_lens

    rag, _ = indexed
    entities = [eid for eid in rag.scope_entity_ids(TENANT, [S1, S2])]
    # a vector chunk whose text is exactly graph unit S1:1:0 ("chunk 0 of 11"),
    # which mentions Alice, Bob and Acme
    chunk = {"source_id": S1, "content": "chunk 0 of 11"}
    seen = vector_lens(rag, TENANT, [S1, S2], [chunk], entities)
    info = rag.entities_info(TENANT, seen)
    assert {i["name"] for i in info.values()} == {"Alice", "Bob", "Acme"}
    # a chunk from the other source covers nothing in S1's unit
    assert vector_lens(rag, TENANT, [S1, S2], [{"source_id": S2, "content": "chunk 0 of 11"}], entities) == []
    assert vector_lens(rag, TENANT, [S1, S2], [], entities) == []


def test_inspect_source_returns_only_that_sources_graph(indexed):
    rag, _ = indexed
    s1 = rag.inspect_source(TENANT, S1)
    assert [c["index"] for c in s1["chunks"]] == [0, 1, 2]  # in reading order
    names = {e["name"] for e in s1["entities"]}
    assert names == {"Alice", "Bob", "Carol", "Acme", "Apollo", "Azure"}  # Dave and Initech are S2's
    assert {(r["source_name"], r["relation"], r["target_name"]) for r in s1["relationships"]} == {
        ("Alice", "WORKS_FOR", "Acme"), ("Bob", "WORKS_FOR", "Acme"), ("Carol", "WORKS_FOR", "Acme"),
        ("Acme", "OWNS", "Apollo"), ("Apollo", "DEPENDS_ON", "Azure"),
    }
    # what each passage produced: unit 0 stated Alice and Bob working for Acme
    first = {c["index"]: c for c in s1["chunks"]}[0]
    by_id = {e["id"]: e["name"] for e in s1["entities"]}
    assert {by_id[i] for i in first["entity_ids"]} == {"Alice", "Bob", "Acme"}
    # every fact points at passages that exist in this source
    chunk_ids = {c["id"] for c in s1["chunks"]}
    assert all(set(r["chunk_ids"]) <= chunk_ids for r in s1["relationships"])
    s2 = rag.inspect_source(TENANT, S2)
    assert {e["name"] for e in s2["entities"]} == {"Dave", "Initech"} and len(s2["relationships"]) == 1
    assert rag.inspect_source(TENANT, "00000000-0000-0000-0000-000000000000") == {
        "chunks": [], "entities": [], "relationships": []}


def test_assess_evidence_flags_weak_retrieval():
    from app.graphrag.params import WEAK_EVIDENCE_LOGIT

    assert pipelines.assess_evidence([{"rerank_score": 2.0}, {"rerank_score": -7.0}]) == {
        "best_rerank": 2.0, "weak": False, "anchored": None}
    weak = pipelines.assess_evidence([{"rerank_score": WEAK_EVIDENCE_LOGIT - 0.1}], anchored=False)
    assert weak["weak"] is True and weak["anchored"] is False
    assert pipelines.assess_evidence([])["weak"] is True  # nothing retrieved
    assert pipelines.assess_evidence([{"rerank_score": None}])["weak"] is True  # reranker was down


def test_run_graph_reports_anchoring_and_weakness(wired, monkeypatch):
    result, _, _ = run(wired, "Alice")
    assert result.evidence["anchored"] is True and result.evidence["weak"] is False
    assert result.trace["anchored"] is True and result.trace["seed_counts"]["key"] >= 1

    class Poor:
        def rerank(self, query, passages):
            return [SimpleNamespace(index=i, score=-7.0) for i in range(len(passages))]

    monkeypatch.setattr(pipelines, "get_reranker", lambda: Poor())
    weak, _, _ = run(wired, "Alice")
    assert weak.evidence["weak"] is True and weak.evidence["best_rerank"] == -7.0
