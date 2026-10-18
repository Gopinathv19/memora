"""Hierarchical communities, summaries and query-time profiles on a real FalkorDB (fake model).

    TEST_FALKORDB_URL=redis://localhost:6390 .venv/bin/python -m pytest tests/test_graphrag_communities_build.py --noconftest
"""

import os
import uuid

import pytest

from app.core.config import get_settings
from app.graphrag import community_build as cb
from app.llm.client import LLMError
from tests.test_graphrag_ingest import embed
from tests.test_graphrag_write import APP, SUBJECT, TENANT, store  # noqa: F401  (store is a fixture)

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_FALKORDB_URL"), reason="set TEST_FALKORDB_URL")
SETTINGS = get_settings()
SUBJECT_B = str(uuid.uuid4())


class FakeModel:
    """Stands in for cached_chat_json: records every prompt, answers with a numbered title."""

    def __init__(self, monkeypatch, fail=False):
        self.prompts, self.fail = [], fail
        monkeypatch.setattr(cb, "cached_chat_json", self)

    def __call__(self, client, model, system, user):
        self.prompts.append(user)
        if self.fail:
            raise LLMError("boom")
        return {"title": f"Group {len(self.prompts)}", "summary": f"Summary {len(self.prompts)} of the group."}, None, False


def seed(store, source_id, names, *, subject=SUBJECT, fact=None, descriptions=None, ids=None):
    """A document with one chunk, `names` as fully connected entities, optionally one atomic fact about two of them."""
    store.base.upsert_document(TENANT, {"id": source_id, "application_id": APP, "subject_id": subject,
                                        "filename": "x.pdf", "extraction_id": "e", "extraction_version": 1, "build_id": "b"})
    chunk = str(uuid.uuid4())
    store.base.upsert_chunks(TENANT, source_id, [{"id": chunk, "index": 0, "text": "text", "char_start": 0, "char_end": 1,
                                                  "page_start": 1, "page_end": 1}])
    ids = ids or {n: str(uuid.uuid5(uuid.NAMESPACE_DNS, n)) for n in names}
    store.base.upsert_entities(TENANT, [{"id": ids[n], "entity_type": "COMPANY", "canonical_key": n.lower(),
                                         "display_name": n, "aliases": [], "alias_keys": [], "raw_types": []} for n in names])
    store.base.upsert_mentions(TENANT, source_id, [
        {"chunk_id": chunk, "entity_id": ids[n], "description": (descriptions or {}).get(n)} for n in names])
    rows = [{"source": ids[a], "target": ids[b], "chunk_ids": [chunk], "description": "d", "raw_relation": None,
             "confidence": 1.0, "build_id": "b"} for i, a in enumerate(names) for b in names[i + 1:]]
    store.base.upsert_relationships(TENANT, source_id, "PROVIDES_TO", rows)
    if fact:
        store.upsert_atomic_facts(TENANT, source_id, [{"id": uuid.uuid4().hex, "text": fact, "evidence": fact, "qualifiers": "",
                                                       "entity_ids": [ids[names[0]], ids[names[1]]], "chunk_ids": [chunk]}])
    return ids


def two_clusters(store, source, subject=SUBJECT, extra=()):
    a = seed(store, source, [f"Alpha{i}" for i in range(5)], subject=subject, fact="Alpha0 supplies Alpha1 with steel.")
    b = seed(store, source, [f"Beta{i}" for i in range(5)] + list(extra), subject=subject, fact="Beta0 pays Beta1 monthly.")
    return a, b


def build(store, source_ids, subject=SUBJECT, **kw):
    return cb.build_hierarchy(store, TENANT, subject, source_ids, client=None, settings=SETTINGS, embed=embed, **kw)


def hier(store, subject=SUBJECT):
    return store.hier_communities(TENANT, subject)


def test_builds_communities_summarises_embeds_and_links_members(store, monkeypatch):
    model = FakeModel(monkeypatch)
    src = str(uuid.uuid4())
    two_clusters(store, src)
    stats = build(store, [src])
    rows = hier(store)
    assert stats["communities"] == 2 and stats["summarized"] == 2 and stats["left_lazy"] == 0 and len(model.prompts) == 2
    assert sorted(r["size"] for r in rows) == [5, 5] and all(len(r["members"]) == 5 for r in rows)
    assert all(r["title"].startswith("Group") and r["embedding"] and r["summary_hash"] for r in rows)
    assert all(len(r["embedding"]) == 4 for r in rows)
    prompt = next(p for p in model.prompts if "Alpha0" in p)
    assert "Alpha0 supplies Alpha1 with steel." in prompt and "Beta0 pays" not in prompt     # its own facts only


def test_a_second_build_reuses_every_summary_and_calls_no_model(store, monkeypatch):
    model = FakeModel(monkeypatch)
    src = str(uuid.uuid4())
    two_clusters(store, src)
    build(store, [src])
    before = {r["id"]: r["title"] for r in hier(store)}
    model.prompts.clear()
    stats = build(store, [src])
    assert stats["reused"] == 2 and stats["summarized"] == 0 and model.prompts == []
    assert {r["id"]: r["title"] for r in hier(store)} == before


def test_a_small_change_keeps_the_summary_even_though_the_community_id_changes(store, monkeypatch):
    model = FakeModel(monkeypatch)
    src = str(uuid.uuid4())
    two_clusters(store, src)
    build(store, [src])
    old_ids = {r["id"] for r in hier(store)}
    model.prompts.clear()
    # one more member joins the Beta group: 5 of 6 overlap, above the 0.8 threshold
    seed(store, src, [f"Beta{i}" for i in range(5)] + ["Beta5"], fact="Beta0 pays Beta1 monthly.")
    stats = build(store, [src])
    assert stats["reused"] == 2 and model.prompts == []
    assert {r["id"] for r in hier(store)} != old_ids                        # the grown community has a new id
    assert sorted(r["size"] for r in hier(store)) == [5, 6]


def test_lazy_communities_get_a_summary_the_first_time_one_is_asked_for(store, monkeypatch):
    model = FakeModel(monkeypatch)
    src = str(uuid.uuid4())
    two_clusters(store, src)
    stats = build(store, [src], summarize_eagerly=False)
    assert stats["left_lazy"] == 2 and model.prompts == []
    assert all(r["summary"] is None and r["embedding"] is None for r in hier(store))

    target = hier(store)[0]["id"]
    got = cb.ensure_summary(store, TENANT, SUBJECT, target, [src], client=None, settings=SETTINGS, embed=embed)
    assert got["title"].startswith("Group") and len(model.prompts) == 1
    done = next(r for r in hier(store) if r["id"] == target)
    assert done["summary"] and len(done["embedding"]) == 4
    assert cb.ensure_summary(store, TENANT, SUBJECT, target, [src], client=None, settings=SETTINGS)["title"] == got["title"]
    assert len(model.prompts) == 1                                          # already summarised: no second call
    assert cb.ensure_summary(store, TENANT, SUBJECT, "no-such-community", [src], client=None, settings=SETTINGS) is None
    assert sum(1 for r in hier(store) if r["summary"]) == 1                 # the other one is still lazy


def test_a_failing_model_leaves_the_communities_lazy_instead_of_failing_the_build(store, monkeypatch):
    FakeModel(monkeypatch, fail=True)
    src = str(uuid.uuid4())
    two_clusters(store, src)
    stats = build(store, [src])
    assert stats["summary_failed"] == 2 and stats["left_lazy"] == 2 and len(hier(store)) == 2


def test_subjects_do_not_share_or_overwrite_communities(store, monkeypatch):
    FakeModel(monkeypatch)
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    seed(store, s1, [f"Alpha{i}" for i in range(5)], subject=SUBJECT)
    seed(store, s2, [f"Alpha{i}" for i in range(5)], subject=SUBJECT_B)       # the same entities, another subject
    build(store, [s1], subject=SUBJECT)
    build(store, [s2], subject=SUBJECT_B)
    a, b = hier(store, SUBJECT), hier(store, SUBJECT_B)
    assert len(a) == len(b) == 1 and a[0]["id"] != b[0]["id"]               # same members, scoped ids
    build(store, [s1], subject=SUBJECT)                                      # rebuilding one leaves the other alone
    assert hier(store, SUBJECT_B)[0]["id"] == b[0]["id"]


def test_the_old_index_build_leaves_hierarchical_communities_alone(store, monkeypatch):
    FakeModel(monkeypatch)
    src = str(uuid.uuid4())
    two_clusters(store, src)
    build(store, [src])
    store.replace_communities(TENANT, SUBJECT, [])                           # what the v1 build does first
    assert len(hier(store)) == 2


def test_profiles_carry_only_the_scopes_own_descriptions(store):
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    ids = {"Acme": str(uuid.uuid5(uuid.NAMESPACE_DNS, "Acme"))}
    seed(store, s1, ["Acme", "Beta"], descriptions={"Acme": "A steel supplier."}, ids={**ids, "Beta": str(uuid.uuid4())})
    seed(store, s2, ["Acme", "Gamma"], subject=SUBJECT_B, descriptions={"Acme": "SECRET: subject B's private note."},
         ids={**ids, "Gamma": str(uuid.uuid4())})
    only_s1 = store.entity_profiles(TENANT, [ids["Acme"]], [s1])[ids["Acme"]]
    assert only_s1["descriptions"] == ["A steel supplier."]
    both = store.entity_profiles(TENANT, [ids["Acme"]], [s1, s2])[ids["Acme"]]
    assert set(both["descriptions"]) == {"A steel supplier.", "SECRET: subject B's private note."}
    assert store.entity_profiles(TENANT, [ids["Acme"]], []) == {} and store.entity_profiles(TENANT, [], [s1]) == {}


def test_a_summary_prompt_never_contains_another_subjects_description(store, monkeypatch):
    model = FakeModel(monkeypatch)
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    names = [f"Alpha{i}" for i in range(5)]
    ids = {n: str(uuid.uuid5(uuid.NAMESPACE_DNS, n)) for n in names}
    seed(store, s1, names, ids=ids, descriptions={"Alpha0": "Public supplier of steel."})
    seed(store, s2, names, ids=ids, subject=SUBJECT_B, descriptions={"Alpha0": "SECRET note from subject B."})
    build(store, [s1], subject=SUBJECT)
    assert model.prompts and all("SECRET" not in p for p in model.prompts)
    assert any("Public supplier of steel." in p for p in model.prompts)


def test_atoms_about_ranks_facts_involving_more_members_first(store):
    src = str(uuid.uuid4())
    ids = seed(store, src, ["A", "B", "C"])
    store.upsert_atomic_facts(TENANT, src, [
        {"id": "f1", "text": "A alone.", "evidence": "A alone", "qualifiers": "", "entity_ids": [ids["A"]], "chunk_ids": []},
        {"id": "f2", "text": "A and B together.", "evidence": "x", "qualifiers": "", "entity_ids": [ids["A"], ids["B"]], "chunk_ids": []}])
    facts = store.atoms_about(TENANT, [ids["A"], ids["B"]], [src], 10)
    assert [f["text"] for f in facts] == ["A and B together.", "A alone."] and facts[0]["involved"] == 2
    assert store.atoms_about(TENANT, [ids["A"]], [str(uuid.uuid4())], 10) == []


def test_the_scope_graph_counts_typed_edges_and_shared_facts(store):
    src = str(uuid.uuid4())
    ids = seed(store, src, ["A", "B", "C"], fact="A and B are linked.")
    nodes, edges, degree = cb.scope_weights(store, TENANT, [src], 1.0, 0.5)
    assert sorted(nodes) == sorted(ids.values())
    weights = {tuple(sorted(e[:2])): e[2] for e in edges}
    assert weights[tuple(sorted((ids["A"], ids["B"])))] == 1.5               # a typed edge plus a shared fact
    assert weights[tuple(sorted((ids["A"], ids["C"])))] == 1.0
    assert degree[ids["A"]] == 2.5


@pytest.mark.parametrize("reply", [[], "text", None, 5, {"title": ["x"], "summary": {"y": 1}}, {"title": "", "summary": "s"}])
def test_a_community_summary_of_the_wrong_shape_is_a_failure_not_an_exception(monkeypatch, reply):
    monkeypatch.setattr(cb, "cached_chat_json", lambda *a: (reply, None, False))
    got = cb.summarize(None, SETTINGS, [{"name": "A", "type": "COMPANY", "descriptions": []}], [])
    assert got == (None, None, None) or (got[0] and got[1])
