"""Graph RAG techniques against a real FalkorDB, with deterministic embeddings.

Self-contained on purpose: it needs only a FalkorDB (TEST_FALKORDB_URL, default
redis://localhost:6390) and never touches PostgreSQL, the network or an LLM, so
it runs with `pytest --noconftest tests/test_graphrag_techniques.py`. Each test
works under a random graph prefix and drops its graphs afterwards.

The fixture graph (one tenant, two sources; confidence in brackets):

    Alice -WORKS_FOR(.9)-> Acme -OWNS(.9)-> Apollo -DEPENDS_ON(.8)-> Azure
    Bob   -WORKS_FOR(.9)-> Acme
    Carol -WORKS_FOR(.9)-> Acme          Acme is a hub (degree 4 + 1)
    Dave  -WORKS_FOR(.9)-> Initech       (source s2, disconnected from Acme)
"""

import math
import os
import re
import uuid

import pytest

pytest.importorskip("falkordb")

from app.graph.store import FalkorGraphStore  # noqa: E402
from app.graphrag import bridge, build  # noqa: E402
from app.graphrag.benchmark import aggregate, bootstrap_ci  # noqa: E402
from app.graphrag.compare import evidence_overlap  # noqa: E402
from app.graphrag.params import RetrievalParams, Technique  # noqa: E402
from app.graphrag.store import GraphRagStore, fact_id  # noqa: E402
from app.graphrag.techniques import community, dual_level, get_technique, paths, ppr, spreading  # noqa: E402
from app.graphrag.techniques.common import Ctx, combine  # noqa: E402

DIM = 64
S1, S2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
TENANT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SUBJECT = "33333333-3333-3333-3333-333333333333"

ENTITIES = {
    "alice": ("Alice", "PERSON"), "bob": ("Bob", "PERSON"), "carol": ("Carol", "PERSON"),
    "dave": ("Dave", "PERSON"), "acme": ("Acme", "COMPANY"), "apollo": ("Apollo", "PROJECT"),
    "azure": ("Azure", "TECHNOLOGY"), "initech": ("Initech", "COMPANY"),
}
FACTS = [  # (source, relation, target, confidence, source_id, chunk)
    ("alice", "WORKS_FOR", "acme", 0.9, S1, 0), ("bob", "WORKS_FOR", "acme", 0.9, S1, 0),
    ("carol", "WORKS_FOR", "acme", 0.9, S1, 1), ("acme", "OWNS", "apollo", 0.9, S1, 1),
    ("apollo", "DEPENDS_ON", "azure", 0.8, S1, 2), ("dave", "WORKS_FOR", "initech", 0.9, S2, 0),
]


def eid(key: str) -> str:
    return str(uuid.uuid5(TENANT, key))


def fake_embed(text: str) -> list[float]:
    """Bag-of-words hashed into DIM buckets, L2-normalized: texts sharing words are close."""
    v = [0.0] * DIM
    for word in re.findall(r"[a-z0-9]+", text.casefold()):
        v[hash_word(word) % DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def hash_word(word: str) -> int:
    h = 0
    for ch in word:
        h = (h * 131 + ord(ch)) % 1_000_003
    return h


@pytest.fixture(scope="module")
def falkor():
    from falkordb import FalkorDB

    url = os.environ.get("TEST_FALKORDB_URL", "redis://localhost:6390")
    try:
        db = FalkorDB.from_url(url, socket_connect_timeout=3)
        db.list_graphs()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no FalkorDB at {url}: {exc}")
    return db


@pytest.fixture
def rag(falkor):
    base = FalkorGraphStore(falkor, prefix=f"gr_test_{uuid.uuid4().hex[:8]}")
    store = GraphRagStore(base)
    for sid in (S1, S2):
        base.upsert_document(TENANT, {
            "id": sid, "application_id": "app", "subject_id": SUBJECT, "filename": f"{sid[:2]}.pdf",
            "extraction_id": "x", "extraction_version": 1, "build_id": "b",
        })
        base.upsert_chunks(TENANT, sid, [
            {"id": f"{sid}:1:{i}", "index": i, "text": f"chunk {i} of {sid[:2]}", "char_start": 0,
             "char_end": 10, "page_start": 1, "page_end": 1} for i in range(3)
        ])
    base.upsert_entities(TENANT, [
        {"id": eid(k), "entity_type": t, "canonical_key": k, "display_name": n,
         "aliases": [], "alias_keys": [], "raw_types": [t]} for k, (n, t) in ENTITIES.items()
    ])
    mentions: dict[str, list[dict]] = {S1: [], S2: []}
    for a, rel, b, conf, sid, chunk in FACTS:
        for key in (a, b):
            mentions[sid].append({"chunk_id": f"{sid}:1:{chunk}", "entity_id": eid(key), "description": None})
        base.upsert_relationships(TENANT, sid, rel, [{
            "source": eid(a), "target": eid(b), "chunk_ids": [f"{sid}:1:{chunk}"],
            "description": f"{ENTITIES[a][0]} {rel} {ENTITIES[b][0]}", "raw_relation": rel,
            "confidence": conf, "build_id": "b",
        }])
    for sid, rows in mentions.items():
        base.upsert_mentions(TENANT, sid, rows)
    yield store
    for tenant_id in base.tenant_ids():
        base.drop_tenant_graph(tenant_id)


class FakeCfg:
    dimension = DIM


@pytest.fixture
def indexed(rag, monkeypatch):
    monkeypatch.setattr(build.embedder, "embed_documents",
                        lambda cfg, texts: ([fake_embed(t) for t in texts], len(texts)))
    stats = build.build_index(rag, TENANT, SUBJECT, [S1, S2], FakeCfg(), communities=False)
    return rag, stats


def ctx(store, query, sources=(S1, S2), **params) -> Ctx:
    return Ctx(
        store=store, tenant_id=TENANT, source_ids=list(sources), subject_id=SUBJECT, query=query,
        query_vector=fake_embed(query), embedding_cfg=None,
        params=RetrievalParams(**params),
    )


def names(evidence) -> dict[str, float]:
    return {e.name: e.score for e in evidence.entities}


# --- index ------------------------------------------------------------------------------------


def test_build_embeds_entities_and_facts_and_is_incremental(indexed):
    rag, stats = indexed
    assert stats["entities_embedded"] == len(ENTITIES)
    assert stats["facts_embedded"] == len(FACTS)
    assert stats["coverage"]["entities_embedded"] == stats["coverage"]["entities"] == len(ENTITIES)
    again = build.build_index(rag, TENANT, SUBJECT, [S1, S2], FakeCfg(), communities=False)
    assert again["entities_embedded"] == 0 and again["facts_embedded"] == 0


def test_build_removes_facts_whose_edge_is_gone(indexed):
    rag, _ = indexed
    rag.base.delete_source(TENANT, S2)
    stats = build.build_index(rag, TENANT, SUBJECT, [S1], FakeCfg(), communities=False)
    assert stats["facts_removed"] >= 1
    stale = fact_id(S2, eid("dave"), "WORKS_FOR", eid("initech"))
    assert stale not in rag.existing_fact_ids(TENANT, [S1, S2])


def test_uniqueness_constraint_blocks_duplicate_entity_ids(indexed):
    rag, _ = indexed
    import time

    from redis.exceptions import ResponseError

    def operational() -> bool:
        rows = rag.base._read(TENANT, "CALL db.constraints()")
        return any(r["label"] == "Entity" and r["status"] == "OPERATIONAL" for r in rows)

    for _ in range(50):  # FalkorDB builds constraints asynchronously
        if operational():
            break
        time.sleep(0.2)
    assert operational()
    with pytest.raises(ResponseError):
        rag.base._query(TENANT, "CREATE (:Entity {id: $id})", {"id": eid("alice")})


def test_explain_shows_index_use(indexed):
    rag, _ = indexed
    plan = " ".join(rag.explain(TENANT, "MATCH (e:Entity {id: 'x'}) RETURN e"))
    assert "Index" in plan


# --- technique 1: spreading activation -------------------------------------------------------


def test_spreading_ranks_by_hop_and_decays(indexed):
    rag, _ = indexed
    ev = spreading.run(ctx(rag, "Alice", max_hops=3, decay=0.7, hub_damping=False))
    scores = names(ev)
    assert ev.seeds and ev.seeds[0].name == "Alice"
    # Alice -> Acme (hop 1) -> Apollo (hop 2) -> Azure (hop 3): strictly decaying
    assert scores["Acme"] > scores["Apollo"] > scores["Azure"] > 0
    assert "Dave" not in scores and "Initech" not in scores  # a different component
    hops = {e.name: e.hop for e in ev.entities}
    assert (hops["Acme"], hops["Apollo"], hops["Azure"]) == (1, 2, 3)
    # path product: 1.0 * (0.7*0.9) for Acme, then * (0.7*0.9) for Apollo
    assert scores["Acme"] == pytest.approx(0.7 * 0.9, rel=1e-3)
    assert scores["Apollo"] == pytest.approx((0.7 * 0.9) ** 2, rel=1e-3)


def test_spreading_hub_damping_lowers_the_hub(indexed):
    rag, _ = indexed
    plain = names(spreading.run(ctx(rag, "Alice", max_hops=2, hub_damping=False)))
    damped = names(spreading.run(ctx(rag, "Alice", max_hops=2, hub_damping=True)))
    assert damped["Acme"] < plain["Acme"]
    assert damped["Acme"] > 0


def test_spreading_multi_seed_sums_paths(indexed):
    rag, _ = indexed
    one = names(spreading.run(ctx(rag, "Alice", hub_damping=False)))
    two = names(spreading.run(ctx(rag, "Alice and Bob", hub_damping=False)))
    # Acme is one hop from both seeds, so its score is the sum of two paths
    assert two["Acme"] > one["Acme"]


def test_spreading_respects_scope(indexed):
    rag, _ = indexed
    ev = spreading.run(ctx(rag, "Dave", sources=(S1,)))
    assert "Initech" not in names(ev)  # S2's fact is out of scope
    ev = spreading.run(ctx(rag, "Dave", sources=(S2,)))
    assert "Initech" in names(ev)


def test_spreading_returns_chunks_and_facts(indexed):
    rag, _ = indexed
    ev = spreading.run(ctx(rag, "Alice"))
    assert ev.chunk_scores and max(ev.chunk_scores.values()) == pytest.approx(1.0)
    assert any(f.relation == "WORKS_FOR" for f in ev.facts)
    assert all(c.startswith(S1) for c in ev.chunk_scores)


def test_no_seed_gives_empty_evidence_not_an_error(indexed):
    rag, _ = indexed
    ev = spreading.run(ctx(rag, "zzzz qqqq"))
    assert ev.entities == [] and ev.chunk_scores == {}


def test_vector_seed_finds_entity_without_exact_name(indexed):
    rag, _ = indexed
    # no exact key, no full-text hit ("technology" is not in any entity name): vector only
    ev = spreading.run(ctx(rag, "azure"))
    assert ev.seeds and ev.seeds[0].name == "Azure"


# --- technique 2: personalized PageRank ------------------------------------------------------


def test_ppr_is_a_probability_distribution_and_decays_with_distance():
    edges = {}
    for a, b in (("s", "a"), ("a", "b"), ("b", "c")):
        ppr._add_edge(edges, a, b, 1.0)
    p = ppr.personalized_pagerank(edges, {"s": 1.0}, alpha=0.5, iterations=60)
    assert sum(p.values()) == pytest.approx(1.0, abs=1e-6)
    assert p["s"] > p["a"] > p["b"] > p["c"]


def test_ppr_restart_one_returns_the_seed_only():
    p = ppr.personalized_pagerank({"s": {"a": 1.0}, "a": {"s": 1.0}}, {"s": 1.0}, alpha=1.0, iterations=5)
    assert p["s"] == pytest.approx(1.0) and p["a"] == pytest.approx(0.0)


def test_ppr_technique_scores_chunks(indexed):
    rag, _ = indexed
    ev = ppr.run(ctx(rag, "Alice"))
    assert ev.technique == "ppr" and ev.chunk_scores
    assert max(ev.chunk_scores.values()) == pytest.approx(1.0)
    assert "Initech" not in names(ev)


# --- technique 3: dual-level ----------------------------------------------------------------


def test_dual_level_uses_local_entities_and_global_facts(indexed, monkeypatch):
    rag, _ = indexed
    monkeypatch.setattr(dual_level, "extract_keywords",
                        lambda q: (["Alice"], ["Apollo DEPENDS_ON Azure"], {"model": "m", "prompt": 10, "completion": 5, "purpose": "keyword extraction"}))
    monkeypatch.setattr(dual_level.embedder, "embed_query", lambda cfg, t: fake_embed(t))
    c = ctx(rag, "who is connected to the cloud dependency")
    c.embedding_cfg = object()
    ev = dual_level.run(c)
    scores = names(ev)
    assert "Alice" in scores and "Apollo" in scores and "Azure" in scores  # Azure only via the fact level
    assert ev.llm_usage and ev.llm_usage[0]["purpose"] == "keyword extraction"


def test_dual_level_survives_keyword_failure(indexed, monkeypatch):
    rag, _ = indexed
    from app.llm.client import LLMError

    class Boom:
        def chat_json(self, *a, **k):
            raise LLMError("down")

    monkeypatch.setattr(dual_level, "get_llm_client", lambda: Boom())
    local, global_, usage = dual_level.extract_keywords("Alice")
    assert (local, global_, usage) == (["Alice"], ["Alice"], None)


# --- technique 4: paths -----------------------------------------------------------------------


def test_paths_returns_the_chain_between_two_seeds(indexed):
    rag, _ = indexed
    ev = paths.run(ctx(rag, "How is Alice connected to Apollo?", path_max_len=3))
    assert ev.paths, ev.notes
    best = ev.paths[0]
    chain = [best["steps"][0]["from"]] + [s["to"] for s in best["steps"]]
    assert chain == ["Alice", "Acme", "Apollo"]
    assert [s["relation"] for s in best["steps"]] == ["WORKS_FOR", "OWNS"]
    assert ev.chunk_scores  # every edge on the path brought its chunk


def test_paths_never_connects_disjoint_components(indexed):
    rag, _ = indexed
    ev = paths.run(ctx(rag, "How is Alice connected to Dave?"))
    # Alice (Acme component) and Dave (Initech component) share no fact edge; the
    # hashed fake embedding also seeds Azure, which *is* reachable from Alice.
    assert not any({p["from"], p["to"]} == {"Alice", "Dave"} for p in ev.paths)
    assert {s.name for s in ev.seeds} >= {"Alice", "Dave"}


def test_paths_with_only_disconnected_seeds_falls_back(indexed):
    rag, _ = indexed
    ev = paths.run(ctx(rag, "Dave and Alice", seed_limit=2))
    assert ev.paths == []
    assert any("spreading" in n for n in ev.notes) and ev.entities


def test_paths_single_seed_falls_back(indexed):
    rag, _ = indexed
    ev = paths.run(ctx(rag, "Alice"))
    assert any("fewer than two seeds" in n for n in ev.notes)


# --- technique 5: communities -----------------------------------------------------------------


def test_community_build_and_retrieval(indexed, monkeypatch):
    rag, _ = indexed
    monkeypatch.setattr(build, "summarize_community",
                        lambda members, facts: ({"title": "Acme people", "summary": "Acme employees Alice Bob Carol and the Apollo project"},
                                                {"model": "m", "prompt": 5, "completion": 5, "purpose": "community summary"}))
    stats = {"entities_embedded": 0, "facts_embedded": 0, "facts_removed": 0, "communities": 0,
             "embedding_tokens": 0, "llm_usage": []}
    build.build_communities(rag, TENANT, SUBJECT, [S1, S2], FakeCfg(), stats)
    assert stats["communities"] >= 1 and stats["llm_usage"]
    ev = community.run(ctx(rag, "Acme employees Alice Bob"))
    assert ev.communities and ev.context and "Acme" in ev.context[0]
    assert ev.chunk_scores


def test_community_falls_back_without_communities(indexed):
    rag, _ = indexed
    ev = community.run(ctx(rag, "Alice"))
    assert any("no communities" in n for n in ev.notes) and ev.entities


# --- registry ---------------------------------------------------------------------------------


@pytest.mark.parametrize("technique", list(Technique))
def test_every_technique_is_registered_and_returns_evidence(indexed, technique, monkeypatch):
    rag, _ = indexed
    monkeypatch.setattr(dual_level, "extract_keywords", lambda q: ([q], [q], None))
    monkeypatch.setattr(dual_level.embedder, "embed_query", lambda cfg, t: fake_embed(t))
    c = ctx(rag, "Alice Acme")
    c.embedding_cfg = object()
    ev = get_technique(technique)(c)
    assert ev.technique in (str(technique), "paths", "community") and ev.entities


# --- pure helpers -----------------------------------------------------------------------------


def test_combine_rewards_multiple_supports_without_letting_them_dominate():
    assert combine([0.8]) == 0.8
    assert combine([0.8, 0.4]) == pytest.approx(0.9)
    assert combine([0.8, 0.8, 0.8, 0.8]) < 1.6


def test_bridge_maps_units_to_chunks_by_containment():
    unit_text = "The audit found three issues. Vendor risk was high. Payment terms are net 30 days."
    chunks = [
        {"id": "c1", "source_id": "s1", "content": "Vendor risk was high. Payment terms are net 30 days."},
        {"id": "c2", "source_id": "s1", "content": "Completely unrelated text about weather and rain."},
        {"id": "c3", "source_id": "s2", "content": "Vendor risk was high. Payment terms are net 30 days."},
    ]
    out = bridge.map_units_to_chunks({"u1": 0.8}, {"u1": unit_text}, {"u1": "s1"}, chunks)
    assert [m.chunk_id for m in out] == ["c1"]  # c2 shares no text, c3 is another source
    assert out[0].score == pytest.approx(0.8)


def test_bridge_partial_overlap_scales_score():
    unit = "alpha beta gamma delta epsilon zeta eta theta"
    chunk = {"id": "c", "source_id": "s", "content": "alpha beta gamma delta epsilon one two three"}
    out = bridge.map_units_to_chunks({"u": 1.0}, {"u": unit}, {"u": "s"}, [chunk], min_containment=0.2)
    assert 0 < out[0].score < 1


def test_evidence_overlap():
    o = evidence_overlap({"vector": ["a", "b", "c"], "graph": ["b", "c", "d"]})
    pair = o["pairs"][0]
    assert pair["only_a"] == ["a"] and pair["only_b"] == ["d"] and pair["both"] == ["b", "c"]
    assert pair["jaccard"] == 0.5


def test_bootstrap_ci_brackets_the_mean():
    lo, hi = bootstrap_ci([1.0, 0.0, 1.0, 1.0, 0.5, 0.0, 1.0, 1.0])
    assert lo <= 0.6875 <= hi
    assert bootstrap_ci([1.0]) is None


def _row(correct, ms, retrieved, status="ok"):
    return {"status": status, "correctness": correct, "total_ms": ms, "retrieved_ids": retrieved,
            "cost_usd": 0.001, "prompt_tokens": 10, "completion_tokens": 5, "recall_at_5": None}


def test_aggregate_per_type_and_win_loss_vs_vector():
    qs = [
        {"query_type": "factoid", "relevant_ids": ["a"], "pipelines": {"vector": _row(1.0, 100, ["a"]), "graph:spreading": _row(1.0, 300, ["a"])}},
        {"query_type": "multi_hop", "relevant_ids": ["b"], "pipelines": {"vector": _row(0.0, 110, ["x"]), "graph:spreading": _row(1.0, 320, ["b"])}},
        {"query_type": "multi_hop", "relevant_ids": ["c"], "pipelines": {"vector": _row(0.5, 90, ["c"]), "graph:spreading": _row(0.0, 280, ["z"])}},
    ]
    for q in qs:
        for r in q["pipelines"].values():
            r["recall_at_5"] = 1.0 if set(q["relevant_ids"]) & set(r["retrieved_ids"]) else 0.0
    agg, by_type = aggregate(qs, ["vector", "graph:spreading"])
    assert agg["vector"]["latency_p50_ms"] == 100 and agg["graph:spreading"]["latency_p50_ms"] == 300
    assert agg["graph:spreading"]["vs_vector"] == {"wins": 1, "ties": 1, "losses": 1}
    assert by_type["graph:spreading"]["multi_hop"]["correctness"] == 0.5
    assert by_type["vector"]["multi_hop"]["recall_at_5"] == 0.5


# --- seeds: a name anchors a question, similarity alone does not -----------------------------


def test_a_named_entity_anchors_the_question(indexed):
    rag, _ = indexed
    ev = spreading.run(ctx(rag, "Alice"))
    assert ev.anchored is True and ev.seed_counts["key"] >= 1
    assert ev.seeds[0].score == pytest.approx(1.0)


def test_vector_only_seeds_do_not_anchor_and_are_not_promoted_to_a_perfect_match(indexed):
    rag, _ = indexed
    # "azure" has no exact key match in this phrasing and no full-text hit, so only the
    # vector signal fires: it must not be scaled up to 1.0 and must not count as anchoring
    ev = spreading.run(ctx(rag, "tell me about cloud platforms"))
    if ev.seeds:
        assert ev.anchored is False
        assert all(s.score <= 0.5 for s in ev.seeds), [(s.name, s.score) for s in ev.seeds]
