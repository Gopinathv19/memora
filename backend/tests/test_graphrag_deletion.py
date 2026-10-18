"""Deleting a source cleans up everything the unified build added for it (plan Step 7).

    TEST_FALKORDB_URL=redis://localhost:6390 .venv/bin/python -m pytest tests/test_graphrag_deletion.py --noconftest
"""

import os
import uuid

import pytest

from app.graphrag import community_build as cb
from app.graphrag.extract import WindowResult, parse_reply
from app.graphrag.graph_write import chunk_fingerprint, current_signature
from app.graphrag.store import GraphRagStore
from tests.test_graphrag_communities_build import FakeModel, SETTINGS, hier, seed
from tests.test_graphrag_communities_build import build as build_communities
from tests.test_graphrag_write import ONT, RAW1, RAW2, S1, S2, SUBJECT, T1, T2, TENANT, build, q, store, two_sources, window  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_FALKORDB_URL"), reason="set TEST_FALKORDB_URL")


def count(store, cypher):
    return q(store, cypher)[0]["n"]


def test_deleting_a_source_removes_its_atomic_facts_through_the_stores_own_hook(store):
    _, _, results = two_sources()
    build(store, results)
    assert count(store, "MATCH (f:Fact {kind: 'atom'}) RETURN count(f) AS n") == 1
    store.base.delete_source(TENANT, S2)                       # the other source: its facts are not touched
    assert count(store, "MATCH (f:Fact {kind: 'atom'}) RETURN count(f) AS n") == 1
    store.base.delete_source(TENANT, S1)                       # the plain base-store call, as the post-commit hook makes it
    assert count(store, "MATCH (f:Fact {kind: 'atom'}) RETURN count(f) AS n") == 0


def test_a_merge_only_the_deleted_source_justified_is_unwound_with_its_name(store):
    _, _, results = two_sources()                                # S1: "Acme Corp" (COMPANY); S2: "ACME Corporation" (ORGANIZATION)
    build(store, results)
    merge = q(store, "MATCH (m:Merge) RETURN m.source_ids AS sources, m.names AS names, m.survivor_id AS surv")[0]
    assert len(merge["sources"]) == 1 and merge["names"]
    merged_source, merged_name = merge["sources"][0], merge["names"][0]
    entity = lambda: q(store, "MATCH (e:Entity {id: $id}) RETURN e.display_name AS name, e.aliases AS aliases, e.search_text AS text", id=merge["surv"])[0]
    before = entity()
    assert merged_name in [before["name"], *before["aliases"]]      # the merged name is the display name or an alias

    store.base.delete_source(TENANT, merged_source)
    assert count(store, "MATCH (m:Merge) RETURN count(m) AS n") == 0
    after = entity()
    assert merged_name != after["name"] and merged_name not in after["aliases"] and merged_name not in after["text"]
    assert after["name"] in [before["name"], *before["aliases"]]    # the name that remains is one a remaining source uses


def three_company_two_org_sources(store):
    """"Acme Corp" (COMPANY) in S1-S3 and "ACME Corporation" (ORGANIZATION) in S4-S5: one entity, the ORG name merged in."""
    sources = [str(uuid.uuid4()) for _ in range(5)]
    results = []
    for n, source in enumerate(sources):
        if n < 3:
            raw = {"entities": [{"name": "Acme Corp", "type": "COMPANY"}], "relationships": [], "facts": []}
            text = ["Acme Corp is a supplier of steel parts."]
        else:
            raw = {"entities": [{"name": "ACME Corporation", "type": "ORGANIZATION"}], "relationships": [], "facts": []}
            text = ["ACME Corporation is a supplier of steel parts."]
        w = window(source, text)
        results.append(WindowResult(w, parse_reply(raw, w, ONT)))
    build(store, results)
    return sources


def merge_state(store):
    rows = q(store, "MATCH (m:Merge) RETURN m.source_ids AS sources, m.names AS names")
    return rows[0] if rows else None


def test_a_merge_stays_while_any_source_that_saw_the_merged_name_remains(store):
    sources = three_company_two_org_sources(store)
    start = merge_state(store)
    assert sorted(start["sources"]) == sorted(sources[3:]) and start["names"] == ["ACME Corporation"]
    acme = lambda: q(store, "MATCH (e:Entity) WHERE e.canonical_key = 'acme' RETURN e.aliases AS aliases")[0]["aliases"]
    assert "ACME Corporation" in acme()

    store.base.delete_source(TENANT, sources[3])                  # one of the two sources: the merge is still evidenced
    assert merge_state(store)["sources"] == [sources[4]] and "ACME Corporation" in acme()

    store.base.delete_source(TENANT, sources[4])                  # the last one: unwound
    assert merge_state(store) is None and "ACME Corporation" not in acme()
    assert count(store, "MATCH (e:Entity) WHERE e.canonical_key = 'acme' RETURN count(e) AS n") == 1     # S1-S3 still mention it


def test_an_alias_another_merge_still_explains_is_kept(store):
    sources = three_company_two_org_sources(store)
    # a second merge record that explains the same name, from a source that still exists
    survivor = q(store, "MATCH (e:Entity) WHERE e.canonical_key = 'acme' RETURN e.id AS id")[0]["id"]
    store.upsert_merges(TENANT, [{"id": "other", "merged_id": "x", "survivor_id": survivor, "stage": "exact", "score": 1.0,
                                  "note": "", "source_ids": [sources[0]], "names": ["ACME Corporation"], "keys": ["acme"]}])
    store.base.delete_source(TENANT, sources[3])
    store.base.delete_source(TENANT, sources[4])
    aliases = q(store, "MATCH (e:Entity {id: $id}) RETURN e.aliases AS aliases", id=survivor)[0]["aliases"]
    assert "ACME Corporation" in aliases                           # the surviving record still accounts for it
    assert count(store, "MATCH (m:Merge) RETURN count(m) AS n") == 1


def two_sources_sharing_clusters(store):
    names_a, names_b = [f"Alpha{i}" for i in range(5)], [f"Beta{i}" for i in range(5)]
    ids = {n: str(uuid.uuid5(uuid.NAMESPACE_DNS, n)) for n in names_a + names_b}
    src_a, src_b = str(uuid.uuid4()), str(uuid.uuid4())
    for src in (src_a, src_b):                                      # both sources mention all ten entities
        seed(store, src, names_a, ids=ids, fact="Alpha0 supplies Alpha1 with steel.")
        seed(store, src, names_b, ids=ids, fact="Beta0 pays Beta1 monthly.")
    return src_a, src_b


def test_communities_a_deleted_source_touched_are_marked_stale_and_not_reused(store, monkeypatch):
    model = FakeModel(monkeypatch)
    src_a, src_b = two_sources_sharing_clusters(store)
    build_communities(store, [src_a, src_b])
    assert len(hier(store)) == 2 and not any(r["stale"] for r in hier(store)) and len(model.prompts) == 2

    store.base.delete_source(TENANT, src_a)
    assert all(r["stale"] for r in hier(store)) and len(hier(store)) == 2        # still there, flagged

    model.prompts.clear()
    stats = build_communities(store, [src_b])
    assert stats["reused"] == 0 and stats["summarized"] == 2 and len(model.prompts) == 2    # re-summarised from what is left
    assert not any(r["stale"] for r in hier(store))


def test_communities_the_source_did_not_touch_keep_their_summaries_and_empty_shells_are_pruned(store, monkeypatch):
    FakeModel(monkeypatch)
    src_a, src_b = str(uuid.uuid4()), str(uuid.uuid4())
    names_a, names_b = [f"Alpha{i}" for i in range(5)], [f"Beta{i}" for i in range(5)]
    ids = {n: str(uuid.uuid5(uuid.NAMESPACE_DNS, n)) for n in names_a + names_b}
    seed(store, src_a, names_a, ids=ids, fact="Alpha0 supplies Alpha1 with steel.")
    seed(store, src_b, names_b, ids=ids, fact="Beta0 pays Beta1 monthly.")
    build_communities(store, [src_a, src_b])
    assert len(hier(store)) == 2
    store.base.delete_source(TENANT, src_a)                      # the Alpha entities went with it
    left = hier(store)
    assert len(left) == 1 and set(left[0]["members"]) == {ids[n] for n in names_b}      # the Alpha shell is gone
    assert left[0]["stale"] is False and left[0]["summary"]                             # the Beta group was never touched


def test_a_stale_community_is_not_served_by_the_community_search(store, monkeypatch):
    FakeModel(monkeypatch)
    src_a, src_b = two_sources_sharing_clusters(store)
    build_communities(store, [src_a, src_b])
    vec = [0.5, 0.5, 0.5, 0.5]
    assert len(store.vector_communities(TENANT, vec, [SUBJECT], 5)) == 2
    store.base.delete_source(TENANT, src_a)                      # both groups lose evidence: stale, still present
    assert len(hier(store)) == 2 and store.vector_communities(TENANT, vec, [SUBJECT], 5) == []
    build_communities(store, [src_b])                            # re-summarised from what remains: served again
    assert len(store.vector_communities(TENANT, vec, [SUBJECT], 5)) == 2


def test_a_failing_cleanup_hook_never_stops_a_deletion(store):
    _, _, results = two_sources()
    build(store, results)

    def broken(tenant, source):
        raise RuntimeError("boom")

    store.base.before_delete_source.insert(0, broken)
    store.base.delete_source(TENANT, S1)
    assert count(store, "MATCH (d:Document {id: $s}) RETURN count(d) AS n".replace("$s", f"'{S1}'")) == 0


def test_the_cleanup_hooks_are_registered_once_however_many_wrappers_are_made(store):
    before = (len(store.base.before_delete_source), len(store.base.after_delete_source))
    GraphRagStore(store.base)
    GraphRagStore(store.base)
    assert (len(store.base.before_delete_source), len(store.base.after_delete_source)) == before == (1, 1)


def test_a_built_document_records_its_signature_and_chunk_fingerprint(store):
    w1, w2, results = two_sources()
    build(store, results)
    docs = {d["id"]: d for d in store.documents_state(TENANT)}
    assert docs[S1]["signature"] == current_signature(ONT) and docs[S1]["chunk_fingerprint"] == chunk_fingerprint(w1.chunk_ids)
    assert docs[S2]["chunk_fingerprint"] == chunk_fingerprint(w2.chunk_ids)
    assert chunk_fingerprint(["b", "a"]) == chunk_fingerprint(["a", "b"]) != chunk_fingerprint(["a"])


# --- findings from an independent review ---------------------------------------------------------


def doc_state(store, source):
    return {d["id"]: d for d in store.documents_state(TENANT)}.get(source)


def test_a_source_with_a_failed_window_is_written_but_not_marked_as_built(store):
    eid = str(uuid.uuid4())
    w_ok, w_bad = window(S1, T1, extraction_id=eid), window(S1, T2, extraction_id=eid)
    results = [WindowResult(w_ok, parse_reply(RAW1, w_ok, ONT)), WindowResult(w_bad, None, [], "boom")]
    _, stats = build(store, results)
    assert stats["sources_written"] == 1 and stats["sources_partial"] == 1
    assert count(store, "MATCH (e:Entity) RETURN count(e) AS n") >= 1                  # what was extracted is kept
    assert doc_state(store, S1)["chunk_fingerprint"] is None                            # ... but a rebuild is still due
    assert doc_state(store, S1)["signature"] is None


def test_a_write_that_fails_midway_leaves_the_document_unmarked(store, monkeypatch):
    w = window(S1, T1)
    result = WindowResult(w, parse_reply(RAW1, w, ONT))

    def boom(*a, **kw):
        raise RuntimeError("falkordb timed out")

    monkeypatch.setattr(store, "upsert_atomic_facts", boom)
    with pytest.raises(RuntimeError):
        build(store, [result])
    state = doc_state(store, S1)
    assert state is not None and state["chunk_fingerprint"] is None        # chunks are there, the document is not marked built


def test_a_fully_successful_source_is_marked_once_everything_is_written(store):
    w = window(S1, T1)
    build(store, [WindowResult(w, parse_reply(RAW1, w, ONT))])
    assert doc_state(store, S1)["chunk_fingerprint"] == chunk_fingerprint(w.chunk_ids)


def test_facts_that_differ_only_by_a_minus_sign_are_different_facts(store):
    from app.graphrag.graph_write import atom_id

    assert atom_id(S1, "The balance is -5000.") != atom_id(S1, "The balance is 5000.")
    assert atom_id(S1, "The  Balance is\n5000.") == atom_id(S1, "the balance is 5000.")        # spacing and case still fold
    w = window(S1, ["The balance is -5000 and the limit is 5000 in the ledger today."])
    raw = {"entities": [], "relationships": [], "facts": [
        {"text": "The balance is -5000.", "entities": [], "quote": "The balance is -5000"},
        {"text": "The balance is 5000.", "entities": [], "quote": "the limit is 5000 in the ledger"}]}
    assert len(parse_reply(raw, w, ONT).facts) == 2


def test_unwinding_removes_every_dead_merge_of_one_survivor_in_one_go(store):
    src = str(uuid.uuid4())
    ids = seed(store, src, ["Keeper"])
    survivor = ids["Keeper"]
    store.base.upsert_entities(TENANT, [{"id": survivor, "entity_type": "COMPANY", "canonical_key": "keeper", "display_name": "Keeper",
                                         "aliases": ["N1", "N2", "Stays"], "alias_keys": ["n1", "n2", "stays"], "raw_types": []}])
    store.upsert_merges(TENANT, [
        {"id": "m1", "merged_id": "x1", "survivor_id": survivor, "stage": "exact", "score": 1.0, "note": "", "source_ids": [src], "names": ["N1"], "keys": ["n1"]},
        {"id": "m2", "merged_id": "x2", "survivor_id": survivor, "stage": "llm", "score": 0.9, "note": "", "source_ids": [src], "names": ["N2"], "keys": ["n2"]}])
    assert store.unwind_merges(TENANT, src) == 2
    row = q(store, "MATCH (e:Entity {id: $id}) RETURN e.aliases AS aliases, e.alias_keys AS keys", id=survivor)[0]
    assert row["aliases"] == ["Stays"] and "n1" not in row["keys"] and "n2" not in row["keys"] and "stays" in row["keys"]


def test_a_merge_that_never_had_sources_is_not_unwound_by_an_unrelated_delete(store):
    src = str(uuid.uuid4())
    ids = seed(store, src, ["Keeper"])
    store.upsert_merges(TENANT, [{"id": "known", "merged_id": "k1", "survivor_id": ids["Keeper"], "stage": "exact", "score": 1.0,
                                  "note": "", "source_ids": [], "names": ["Old Name"], "keys": ["old_name"]}])
    assert store.unwind_merges(TENANT, src) == 0
    assert count(store, "MATCH (m:Merge) RETURN count(m) AS n") == 1


def test_an_alias_the_survivors_own_sources_state_is_not_removed_when_a_merged_source_goes(store):
    s_one, s_two = str(uuid.uuid4()), str(uuid.uuid4())
    w1, w2 = window(s_one, ["Acme Holdings, also called Acme, supplies steel parts."]), window(s_two, ["Acme ships goods to Beta."])
    raw1 = {"entities": [{"name": "Acme Holdings", "type": "COMPANY", "aliases": ["Acme"]}], "relationships": [], "facts": []}
    raw2 = {"entities": [{"name": "Acme", "type": "ORGANIZATION"}], "relationships": [], "facts": []}
    build(store, [WindowResult(w1, parse_reply(raw1, w1, ONT)), WindowResult(w2, parse_reply(raw2, w2, ONT))])
    assert count(store, "MATCH (e:Entity) RETURN count(e) AS n") == 1
    store.base.delete_source(TENANT, s_two)
    row = q(store, "MATCH (e:Entity) RETURN e.display_name AS name, e.aliases AS aliases, e.alias_keys AS keys")[0]
    assert "Acme" in [row["name"], *row["aliases"]]                           # source one still says "Acme"
    assert "acme" in row["keys"] or row["name"] == "Acme"


def test_a_merge_record_lists_only_what_the_merge_added_to_the_survivor():
    from app.graphrag.resolve import make_cand, resolve

    survivor = make_cand(TENANT, "Acme Holdings", "COMPANY", ["Acme"], source_id="s1")
    survivor.mentions = 3
    other = make_cand(TENANT, "Acme", "ORGANIZATION", source_id="s2")
    res = resolve([survivor, other])
    (merge,) = res.merges
    assert merge.survivor_id == survivor.id and merge.names == [] and merge.keys == []     # "Acme" was already the survivor's
