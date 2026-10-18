"""The unified graph writer against a real FalkorDB (set TEST_FALKORDB_URL; skipped otherwise).

Run without the repository conftest, which wants PostgreSQL:
    TEST_FALKORDB_URL=redis://localhost:6390 .venv/bin/python -m pytest tests/test_graphrag_write.py --noconftest
"""

import os
import uuid

import pytest

from app.graph.ontology import get_ontology
from app.graphrag.extract import WindowResult, parse_reply
from app.graphrag.graph_write import atom_id, known_candidates, write_batch
from app.graphrag.resolve import candidates_from_extractions, resolve
from app.graphrag.store import GraphRagStore
from app.graphrag.windows import ChunkRef, pack_windows

URL = os.environ.get("TEST_FALKORDB_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set TEST_FALKORDB_URL to a throwaway FalkorDB")

ONT = get_ontology()
TENANT = uuid.uuid4()
S1, S2 = str(uuid.uuid4()), str(uuid.uuid4())
SUBJECT, APP = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture
def store():
    from falkordb import FalkorDB

    from app.graph.store import FalkorGraphStore

    base = FalkorGraphStore(FalkorDB.from_url(URL), prefix=f"memora_test_{uuid.uuid4().hex[:8]}")
    rag = GraphRagStore(base)
    rag.ensure(TENANT, 4)
    yield rag
    for tenant_id in base.tenant_ids():
        base.drop_tenant_graph(tenant_id)


def window(source_id, texts, extraction_id=None):
    chunks = [ChunkRef(id=str(uuid.uuid4()), index=i, text=t, tokens=max(1, len(t) // 4), page_start=1, page_end=1)
              for i, t in enumerate(texts)]
    block = {"id": str(uuid.uuid4()), "section_path": ["Supply", "Terms"], "title": None, "chunks": chunks}
    return pack_windows([block], source_id=source_id, extraction_id=extraction_id or str(uuid.uuid4()),
                        source_version=1, filename=f"{source_id[:4]}.pdf", subject_id=SUBJECT, application_id=APP)[0]


T1 = ["Acme Corp supplies steel to Beta Ltd.", "Delivery is due within 14 days of the order."]
T2 = ["ACME Corporation also supplies parts to Gamma Inc."]
RAW1 = {
    "entities": [{"name": "Acme Corp", "type": "COMPANY", "description": "A steel supplier."},
                 {"name": "Beta Ltd", "type": "COMPANY"}],
    "relationships": [{"source": "Acme Corp", "relation": "PROVIDES_TO", "target": "Beta Ltd",
                       "quote": "Acme Corp supplies steel to Beta Ltd"}],
    "facts": [{"text": "Acme Corp must deliver within 14 days of the order.", "entities": ["Acme Corp"],
               "quote": "Delivery is due within 14 days of the order", "qualifiers": {"duration": "14 days"}}],
}
RAW2 = {   # typed ORGANIZATION: a different id from S1's COMPANY "Acme", merged by resolution
    "entities": [{"name": "ACME Corporation", "type": "ORGANIZATION"}, {"name": "Gamma Inc", "type": "COMPANY"}],
    "relationships": [{"source": "ACME Corporation", "relation": "PROVIDES_TO", "target": "Gamma Inc",
                       "quote": "ACME Corporation also supplies parts to Gamma Inc"}],
    "facts": [],
}


def result(win, raw):
    return WindowResult(win, parse_reply(raw, win, ONT))


def build(store, results, **kw):
    cands = candidates_from_extractions(TENANT, results, known_candidates(store, TENANT))
    resolution = resolve(cands)
    return resolution, write_batch(store, TENANT, results, resolution, build_id="b1", **kw)


def q(store, cypher, **params):
    return store._read(TENANT, cypher, params)


def two_sources():
    w1, w2 = window(S1, T1), window(S2, T2)
    return w1, w2, [result(w1, RAW1), result(w2, RAW2)]


def test_writes_documents_chunks_entities_edges_atoms_and_the_merge_record(store):
    w1, w2, results = two_sources()
    resolution, stats = build(store, results)

    assert stats["sources_written"] == 2 and stats["chunks"] == 3 and stats["merges"] == 1
    chunks = q(store, "MATCH (d:Document)-[:CONTAINS]->(c:Chunk) RETURN d.id AS d, c.id AS c, c.kind AS kind, "
                      "c.subject_id AS subject, c.section_path AS path")
    assert {r["c"] for r in chunks} == set(w1.chunk_ids) | set(w2.chunk_ids)          # real retrieval chunk ids
    assert {r["kind"] for r in chunks} == {"retrieval"} and {r["subject"] for r in chunks} == {SUBJECT}
    assert chunks[0]["path"] == ["Supply", "Terms"]

    acme = q(store, "MATCH (e:Entity) WHERE e.canonical_key = 'acme' RETURN e.id AS id, e.aliases AS aliases")
    assert len(acme) == 1                                                              # COMPANY + ORGANIZATION: one node
    ments = q(store, "MATCH (c:Chunk)-[:MENTIONS]->(e:Entity {id: $id}) RETURN c.source_id AS s", id=acme[0]["id"])
    assert {m["s"] for m in ments} == {S1, S2}

    edges = q(store, "MATCH (a:Entity)-[r:PROVIDES_TO]->(b:Entity) RETURN b.display_name AS b, r.source_id AS s, "
                     "r.confidence AS conf, r.description AS quote, r.chunk_ids AS chunks")
    assert {(e["b"], e["s"]) for e in edges} == {("Beta Ltd", S1), ("Gamma Inc", S2)}
    assert all(e["conf"] == 1.0 and e["chunks"] for e in edges)
    assert any(e["quote"] == "Acme Corp supplies steel to Beta Ltd" for e in edges)

    atom = q(store, "MATCH (f:Fact {kind: 'atom'})-[:SUPPORTED_BY]->(c:Chunk) "
                    "OPTIONAL MATCH (f)-[:ABOUT]->(e:Entity) "
                    "RETURN f.text AS text, f.evidence AS ev, f.qualifiers AS qual, c.id AS chunk, e.canonical_key AS about")
    assert len(atom) == 1 and atom[0]["chunk"] == w1.chunk_ids[1] and atom[0]["about"] == "acme"
    assert atom[0]["ev"] == "Delivery is due within 14 days of the order" and '"duration": "14 days"' in atom[0]["qual"]

    merge = q(store, "MATCH (m:Merge) RETURN m.stage AS stage, m.source_ids AS sources, m.merged_id AS merged, m.survivor_id AS surv")
    assert len(merge) == 1 and merge[0]["stage"] == "exact" and merge[0]["sources"] and merge[0]["merged"] != merge[0]["surv"]


def count(store, label):
    return q(store, f"MATCH (n:{label}) RETURN count(n) AS n")[0]["n"]


def test_writing_the_same_batch_twice_changes_nothing(store):
    _, _, results = two_sources()
    build(store, results)
    before = {l: count(store, l) for l in ("Document", "Chunk", "Entity", "Fact", "Merge")}
    rels = q(store, "MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
    build(store, results)
    assert {l: count(store, l) for l in before} == before
    assert q(store, "MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"] == rels


def test_rebuilding_a_source_replaces_its_old_chunks_and_facts(store):
    w1, w2, results = two_sources()
    build(store, results)
    new_w1 = window(S1, T1)                                     # re-chunked: new chunk ids, new extraction
    only_entities = {"entities": RAW1["entities"], "relationships": [], "facts": []}
    build(store, [result(new_w1, only_entities), results[1]])

    chunk_ids = {r["c"] for r in q(store, "MATCH (c:Chunk {source_id: $s}) RETURN c.id AS c", s=S1)}
    assert chunk_ids == set(new_w1.chunk_ids) and not chunk_ids & set(w1.chunk_ids)
    assert q(store, "MATCH (f:Fact {kind: 'atom'}) RETURN count(f) AS n")[0]["n"] == 0        # S1's atom is gone
    assert q(store, "MATCH (:Entity)-[r:PROVIDES_TO {source_id: $s}]->(:Entity) RETURN count(r) AS n", s=S1)[0]["n"] == 0
    assert q(store, "MATCH (:Entity)-[r:PROVIDES_TO {source_id: $s}]->(:Entity) RETURN count(r) AS n", s=S2)[0]["n"] == 1  # S2 untouched


def test_a_source_whose_windows_all_failed_is_left_as_it_was(store):
    w1, w2, results = two_sources()
    build(store, results)
    before = count(store, "Chunk")
    failed = WindowResult(window(S2, T2), None, [], "boom")
    _, stats = build(store, [results[0], failed])
    assert stats["sources_skipped_all_windows_failed"] == 1 and count(store, "Chunk") == before
    assert q(store, "MATCH (c:Chunk {source_id: $s}) RETURN count(c) AS n", s=S2)[0]["n"] == 1


def test_relations_merged_into_a_self_loop_are_skipped_and_counted(store):
    w = window(S1, ["Acme Corp is also called ACME Corporation in this contract."])
    raw = {"entities": [{"name": "Acme Corp", "type": "COMPANY"}, {"name": "ACME Corporation", "type": "ORGANIZATION"}],
           "relationships": [{"source": "Acme Corp", "relation": "PART_OF", "target": "ACME Corporation",
                              "quote": "Acme Corp is also called ACME Corporation"}], "facts": []}
    _, stats = build(store, [result(w, raw)])
    assert stats["relations_skipped_self_after_merge"] == 1 and stats["relations"] == 0


def test_existing_techniques_read_the_new_graph_and_respect_scope(store):
    _, _, results = two_sources()
    build(store, results)
    acme = q(store, "MATCH (e:Entity {canonical_key: 'acme'}) RETURN e.id AS id")[0]["id"]
    both = store.activation(TENANT, [{"id": acme, "w": 1.0}], [S1, S2], max_hops=2, decay=0.7, min_confidence=0.5,
                            relation_weights={}, hub_damping=False, limit=10)
    assert {r["name"] for r in both} == {"Beta Ltd", "Gamma Inc"}
    only_s1 = store.activation(TENANT, [{"id": acme, "w": 1.0}], [S1], max_hops=2, decay=0.7, min_confidence=0.5,
                               relation_weights={}, hub_damping=False, limit=10)
    assert {r["name"] for r in only_s1} == {"Beta Ltd"}


def test_the_old_index_build_cannot_see_or_delete_atomic_facts(store):
    _, _, results = two_sources()
    build(store, results)
    ids = store.existing_fact_ids(TENANT, [S1, S2])
    assert atom_id(S1, "Acme Corp must deliver within 14 days of the order.") not in ids
    assert store.prune_orphan_facts(TENANT) == 0                      # their Document exists
    assert store.delete_facts(TENANT, sorted(ids)) == 0
    assert q(store, "MATCH (f:Fact {kind: 'atom'}) RETURN count(f) AS n")[0]["n"] == 1


def test_atomic_facts_have_a_fulltext_index_and_take_embeddings(store):
    _, _, results = two_sources()
    build(store, results)
    hits = q(store, "CALL db.idx.fulltext.queryNodes('Fact', 'deliver') YIELD node RETURN node.text AS text")
    assert [h["text"] for h in hits] == ["Acme Corp must deliver within 14 days of the order."]
    missing = store.atoms_missing_embedding(TENANT, [S1, S2], 10)
    assert len(missing) == 1
    store.set_atom_embeddings(TENANT, [{"id": missing[0]["id"], "embedding": [0.1, 0.2, 0.3, 0.4]}])
    assert store.atoms_missing_embedding(TENANT, [S1, S2], 10) == []


def test_delete_atomic_facts_removes_only_atoms_of_that_source(store):
    _, _, results = two_sources()
    build(store, results)
    assert store.delete_atomic_facts(TENANT, S2) == 0
    assert store.delete_atomic_facts(TENANT, S1) == 1


# --- adaptive over-fetch ----------------------------------------------------------------


def _embedded_entity(store, source_id, name, vector, chunk_text):
    store.base.upsert_document(TENANT, {"id": source_id, "application_id": APP, "subject_id": SUBJECT,
                                        "filename": "x.pdf", "extraction_id": "e", "extraction_version": 1, "build_id": "b"})
    chunk_id = str(uuid.uuid4())
    store.base.upsert_chunks(TENANT, source_id, [{"id": chunk_id, "index": 0, "text": chunk_text, "char_start": 0,
                                                  "char_end": 1, "page_start": 1, "page_end": 1}])
    eid = str(uuid.uuid4())
    store.base.upsert_entities(TENANT, [{"id": eid, "entity_type": "CONCEPT", "canonical_key": name.lower(),
                                         "display_name": name, "aliases": [], "alias_keys": [], "raw_types": []}])
    store.base.upsert_mentions(TENANT, source_id, [{"chunk_id": chunk_id, "entity_id": eid, "description": None}])
    store.set_entity_embeddings(TENANT, [{"id": eid, "embedding": vector, "text": name}])
    return eid


def test_a_small_subject_in_a_large_tenant_still_gets_its_vector_hits(store):
    big, small = str(uuid.uuid4()), str(uuid.uuid4())
    # 40 entities of another subject sit closer to the query than the 2 that are in scope
    for i in range(40):
        _embedded_entity(store, big, f"Near{i}", [1.0, 0.001 * i, 0.0, 0.0], "near")
    wanted = {_embedded_entity(store, small, f"Far{i}", [0.6, 0.8, 0.0, 0.0], "far") for i in range(2)}
    hits = store.vector_entities(TENANT, [1.0, 0.0, 0.0, 0.0], [small], 2)       # first window is 12 hits wide
    assert {h["id"] for h in hits} == wanted


def test_an_empty_scope_and_a_scope_with_nothing_embedded_return_nothing_without_looping(store):
    _embedded_entity(store, str(uuid.uuid4()), "Elsewhere", [1.0, 0.0, 0.0, 0.0], "x")
    assert store.vector_entities(TENANT, [1.0, 0.0, 0.0, 0.0], [], 3) == []
    assert store.vector_entities(TENANT, [1.0, 0.0, 0.0, 0.0], [str(uuid.uuid4())], 3) == []


def test_without_widening_the_fixed_window_misses_them(store, monkeypatch):
    """The same data with the window capped at its first size: what the old fixed 6x over-fetch did."""
    from app.graphrag import store as store_module

    big, small = str(uuid.uuid4()), str(uuid.uuid4())
    for i in range(40):
        _embedded_entity(store, big, f"Near{i}", [1.0, 0.001 * i, 0.0, 0.0], "near")
    for i in range(2):
        _embedded_entity(store, small, f"Far{i}", [0.6, 0.8, 0.0, 0.0], "far")
    monkeypatch.setattr(store_module, "MAX_FETCH", 12)
    assert store.vector_entities(TENANT, [1.0, 0.0, 0.0, 0.0], [small], 2) == []
