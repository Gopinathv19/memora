"""Question analysis and the unified retrieval core (plan Step 5).

The pure parts need nothing. The `gather` tests need a throwaway FalkorDB:
    TEST_FALKORDB_URL=redis://localhost:6390 .venv/bin/python -m pytest tests/test_graphrag_retrieve.py --noconftest
"""

import os
import uuid

import pytest

from app.core.config import get_settings
from app.graphrag import analyse as A
from app.graphrag import retrieve as R
from app.graphrag.analyse import Analysis, analyse, rule_analysis, rule_entities
from app.graphrag.params import RetrievalParams
from app.llm.client import LLMError

# --- the analyser -------------------------------------------------------------------------------


@pytest.mark.parametrize("question,kind", [
    ("What is the notice period for terminating the agreement?", "lookup"),
    ("How long is the refund window?", "lookup"),
    ("Who supplies the company that Beta Ltd acquired?", "relational"),
    ("What is the relationship between Acme Corp and Gamma Inc?", "relational"),
    ("How does Acme Corp relate to Beta Ltd?", "relational"),          # two named entities
    ("What are the main risks across our supplier contracts?", "corpus"),
    ("Give me an overview of all the projects", "corpus"),
    ("Summarise the key themes", "corpus"),
])
def test_rule_analysis_kinds(question, kind):
    assert rule_analysis(question).kind == kind


def test_rule_entities_find_quoted_capitalised_camelcase_and_identifier_names():
    got = rule_entities('What did "Project Alpha" say about PromptConfig and INV-1021 for Acme Corp in GPT-4o?')
    assert {"Project Alpha", "PromptConfig", "INV-1021", "Acme Corp", "GPT-4o"} <= set(got)
    assert "What" not in got and "did" not in got
    assert rule_entities("what is the notice period") == []


def fake_model(monkeypatch, reply):
    calls = []

    def fake(client, model, system, user):
        calls.append(user)
        if isinstance(reply, Exception):
            raise reply
        return reply, None, False

    monkeypatch.setattr(A, "cached_chat_json", fake)
    return calls


def test_the_model_analysis_is_used_and_sub_questions_only_for_relational(monkeypatch):
    calls = fake_model(monkeypatch, {"kind": "relational", "entities": ["Beta Ltd"], "sub_questions": ["Who did Beta acquire?", "Who supplies it?"]})
    got = analyse(object(), get_settings(), "Who supplies the company Beta Ltd acquired?")
    assert (got.kind, got.entities, got.source) == ("relational", ["Beta Ltd"], "llm") and len(got.sub_questions) == 2 and len(calls) == 1
    fake_model(monkeypatch, {"kind": "lookup", "entities": [], "sub_questions": ["ignored"]})
    got = analyse(object(), get_settings(), "What is the notice period?")
    assert got.kind == "lookup" and got.sub_questions == []


def test_an_unusable_model_answer_or_failure_falls_back_to_the_rules(monkeypatch):
    fake_model(monkeypatch, {"kind": "banana"})
    assert analyse(object(), get_settings(), "What are the main risks across all contracts?").kind == "corpus"
    fake_model(monkeypatch, LLMError("down"))
    got = analyse(object(), get_settings(), "What are the main risks across all contracts?")
    assert got.kind == "corpus" and got.source == "rules"


def test_switched_off_or_without_a_client_no_model_is_called(monkeypatch):
    calls = fake_model(monkeypatch, {"kind": "corpus"})
    assert analyse(object(), get_settings(), "What is X?", use_llm=False).source == "off"
    assert analyse(None, get_settings(), "What is X?").source == "rules"
    assert calls == []


# --- fusion -------------------------------------------------------------------------------------


def test_weighted_rrf_orders_by_weighted_reciprocal_rank():
    fused = R.weighted_rrf([(["a", "b", "c"], 1.0), (["c", "b"], 2.0)], k=60)
    assert [d for d, _ in fused] == ["c", "b", "a"]              # c: 1/63+2/61, b: 1/62+2/62, a: 1/61
    assert dict(fused)["a"] == pytest.approx(1 / 61)


def test_a_zero_weight_list_contributes_nothing_and_ties_break_on_id():
    assert R.weighted_rrf([(["x", "y"], 0.0), (["b", "a"], 1.0)]) == [("b", 1 / 61), ("a", 1 / 62)]
    tied = R.weighted_rrf([(["b"], 1.0), (["a"], 1.0)])
    assert [d for d, _ in tied] == ["a", "b"]
    assert R.weighted_rrf([]) == []


def test_chunks_from_atoms_rank_by_the_best_supporting_fact_with_a_bonus_for_more():
    atoms = [{"similarity": 0.9, "chunk_ids": ["c1"]}, {"similarity": 0.8, "chunk_ids": ["c2", "c1"]},
             {"similarity": 0.7, "chunk_ids": ["c3"]}]
    ranked = R.chunks_from_atoms(atoms)
    assert [c for c, _ in ranked] == ["c1", "c2", "c3"]
    assert dict(ranked)["c1"] == pytest.approx(0.9 + 0.25 * 0.8)         # supported twice
    assert R.chunks_from_atoms([]) == []


def test_weights_follow_the_question_kind_and_can_be_overridden_per_channel():
    params = RetrievalParams()
    assert R.weights_for(Analysis("lookup"), params)["walk"] == 0.4
    assert R.weights_for(Analysis("relational"), params)["walk"] == 1.0
    assert R.weights_for(Analysis("corpus"), params)["vector"] == 0.5
    assert R.weights_for(Analysis("mystery"), params) == R.DEFAULT_WEIGHTS["lookup"]
    over = RetrievalParams(unified_weights={"walk": 0.0, "bogus": 9})
    assert R.weights_for(Analysis("relational"), over) == {"vector": 1.0, "atoms": 1.0, "walk": 0.0}


# --- gather, on a real graph ----------------------------------------------------------------------

URL = os.environ.get("TEST_FALKORDB_URL")
needs_graph = pytest.mark.skipif(not URL, reason="set TEST_FALKORDB_URL")

if URL:
    from tests.test_graphrag_communities_build import seed
    from tests.test_graphrag_write import SUBJECT, TENANT, store  # noqa: F401  (store is a fixture)

ALPHA = [f"Alpha{i}" for i in range(5)]
BETA = [f"Beta{i}" for i in range(5)]
VEC_A, VEC_B = [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]


def chunk_ids(store, source):
    return [r["id"] for r in store._read(TENANT, "MATCH (c:Chunk {source_id: $s}) RETURN c.id AS id", {"s": source})]


@pytest.fixture
def world(store):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    seed(store, a, ALPHA, fact="Alpha0 supplies Alpha1 with steel.")
    seed(store, b, BETA, fact="Beta0 pays Beta1 monthly.")
    for atom in store.atoms_missing_embedding(TENANT, [a, b], 10):
        store.set_atom_embeddings(TENANT, [{"id": atom["id"], "embedding": VEC_A if "Alpha" in atom["text"] else VEC_B}])
    return store, a, b, chunk_ids(store, a)[0], chunk_ids(store, b)[0]


def run(store, sources, vector_ids=(), analysis=None, vec=VEC_A, query="a question", **kw):
    params = kw.pop("params", RetrievalParams())
    return R.gather(store, TENANT, sources, SUBJECT, query, vec, list(vector_ids), analysis or Analysis("lookup"), params, **kw)


@needs_graph
def test_atoms_lead_the_fusion_and_the_walk_stays_inside_the_connected_cluster(world):
    store, a, b, ca, cb = world
    got = run(store, [a, b])
    assert got.fused[0] == ca and got.lists["atoms"][0] == ca
    assert ca in got.lists["walk"] and cb not in got.lists["walk"]      # the clusters are not connected
    assert got.channels_of(ca) == ["atoms", "walk"] and got.atoms[0]["entity_ids"]


@needs_graph
def test_a_chunk_dense_search_liked_becomes_a_walk_node(world):
    store, a, b, ca, cb = world
    got = run(store, [a, b], vector_ids=[cb])
    assert cb in got.lists["walk"] and got.lists["vector"] == [cb]
    assert got.seeds["passage_seeds"] == 1


@needs_graph
def test_scope_limits_every_channel(world):
    store, a, b, ca, cb = world
    got = run(store, [b])
    assert ca not in got.fused and all(cb == c for c in got.lists["atoms"] + got.lists["walk"])
    assert run(store, []).lists["atoms"] == [] and run(store, [], vector_ids=["v1"]).fused == ["v1"]


@needs_graph
def test_a_name_in_the_question_seeds_the_walk_even_when_the_embedding_points_elsewhere(world):
    store, a, b, ca, cb = world
    elsewhere = [0.1, 0.0, 1.0, 0.0]          # far from both facts, but slightly nearer the Alpha one
    without = run(store, [a, b], vec=elsewhere, params=RetrievalParams(atom_k=1))
    named = run(store, [a, b], vec=elsewhere, analysis=Analysis("lookup", ["Beta0"]), params=RetrievalParams(atom_k=1))
    assert cb not in without.lists["walk"] and ca in without.lists["walk"]      # one fact fetched: Alpha's
    assert cb in named.lists["walk"] and named.seeds["entity_seeds"] > without.seeds["entity_seeds"]


@needs_graph
def test_sub_question_seeds_reach_a_cluster_the_question_alone_does_not(world):
    store, a, b, ca, cb = world
    analysis = Analysis("relational", [], ["the second hop"])
    plain = run(store, [a, b], analysis=analysis, params=RetrievalParams(atom_k=1))
    hop = run(store, [a, b], analysis=analysis, params=RetrievalParams(atom_k=1, unified_multi_hop=True), embed_query=lambda q: VEC_B)
    assert cb not in plain.lists["walk"] and cb in hop.lists["walk"]


@needs_graph
def test_widening_asks_for_more_atoms(world):
    store, a, b, ca, cb = world
    params = RetrievalParams(atom_k=1)
    assert len(run(store, [a, b], params=params).atoms) == 1
    wide = run(store, [a, b], params=params, wide=True)
    assert len(wide.atoms) == 2 and wide.wide and cb in wide.lists["atoms"]


@needs_graph
def test_communities_are_fetched_only_for_corpus_questions(world):
    store, a, b, ca, cb = world
    store.replace_hier_communities(TENANT, SUBJECT, [{
        "id": "cm1-x", "level": 1, "parent_id": None, "size": 5, "members": [], "title": "Alpha group",
        "summary": "About alpha.", "summary_hash": "h", "embedding": VEC_A}])
    assert run(store, [a, b], analysis=Analysis("lookup")).communities == []
    got = run(store, [a, b], analysis=Analysis("corpus"))
    assert [c["title"] for c in got.communities] == ["Alpha group"]
    assert got.trace()["communities"][0]["title"] == "Alpha group" and got.trace()["analysis"]["kind"] == "corpus"


@needs_graph
def test_the_trace_is_json_ready_and_nothing_is_found_in_an_empty_scope(store):
    got = run(store, [str(uuid.uuid4())], vector_ids=["v1", "v2"])
    assert got.fused == ["v1", "v2"] and got.atoms == []
    import json
    json.dumps(got.trace())


@pytest.mark.parametrize("reply", [
    {"kind": "relational", "entities": "Acme", "sub_questions": 5}, {"kind": "relational", "entities": {"a": 1}, "sub_questions": {"b": 2}},
    {"kind": ["lookup"]}, [], "relational", None,
])
def test_the_analyser_survives_replies_of_the_wrong_shape(monkeypatch, reply):
    fake_model(monkeypatch, reply)
    got = analyse(object(), get_settings(), "Who supplies Acme Corp and Beta Ltd?")
    assert got.kind in ("lookup", "relational", "corpus") and isinstance(got.entities, list) and isinstance(got.sub_questions, list)


@needs_graph
def test_a_fact_found_for_the_question_and_a_sub_question_appears_once_best_first(world):
    store, a, b, ca, cb = world
    analysis = Analysis("relational", [], ["first hop", "second hop"])
    got = run(store, [a, b], analysis=analysis, params=RetrievalParams(unified_multi_hop=True), embed_query=lambda q: VEC_A)
    ids = [x["id"] for x in got.atoms]
    assert len(ids) == len(set(ids))                                           # the Alpha fact came back three times: kept once
    sims = [x["similarity"] for x in got.atoms]
    assert sims == sorted(sims, reverse=True)
