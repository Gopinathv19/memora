"""The build orchestrator end to end on a real FalkorDB, with a fake extractor and embedder.

    TEST_FALKORDB_URL=redis://localhost:6390 .venv/bin/python -m pytest tests/test_graphrag_ingest.py --noconftest
"""

import hashlib
import math
import os

import pytest

from app.graphrag.extract import WindowResult, parse_reply
from app.graphrag.ingest import build_windows
from tests.test_graphrag_write import ONT, RAW1, RAW2, S1, S2, T1, T2, TENANT, store, window  # noqa: F401  (store is a fixture)

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_FALKORDB_URL"), reason="set TEST_FALKORDB_URL")


def embed(texts):
    out = []
    for t in texts:
        h = hashlib.sha1(t.encode()).digest()
        v = [b / 255 + 0.05 for b in h[:4]]
        n = math.sqrt(sum(x * x for x in v))
        out.append([x / n for x in v])
    return out, len(texts)


class FakeExtractor:
    def __init__(self, raws, fail=()):
        self.raws, self.fail = raws, set(fail)

    def extract(self, win):
        if win.source_id in self.fail:
            return WindowResult(win, None, [], "boom")
        return WindowResult(win, parse_reply(self.raws[win.source_id], win, ONT))


def q(store, cypher, **params):
    return store._read(TENANT, cypher, params)


def run(store, extractor=None, windows=None, **kw):
    windows = windows or [window(S1, T1), window(S2, T2)]
    return build_windows(store, TENANT, windows, extractor=extractor or FakeExtractor({S1: RAW1, S2: RAW2}),
                         embed=embed, dimension=4, **kw)


def test_a_build_extracts_resolves_writes_and_embeds(store):
    report = run(store)
    assert report.windows == 2 and report.failed_windows == 0 and not report.errors
    assert report.written["sources_written"] == 2 and report.written["atomic_facts"] == 1
    assert report.resolution["merged_exact"] == 1                       # Acme (COMPANY) and ACME (ORGANIZATION)
    assert set(report.timings_s) == {"extract", "resolve", "write", "embed"}

    n_entities = q(store, "MATCH (e:Entity) RETURN count(e) AS n")[0]["n"]
    assert n_entities == 3                                              # Acme, Beta Ltd, Gamma Inc
    assert q(store, "MATCH (e:Entity) WHERE e.embedding IS NULL RETURN count(e) AS n")[0]["n"] == 0
    assert q(store, "MATCH (f:Fact {kind: 'atom'}) WHERE f.embedding IS NULL RETURN count(f) AS n")[0]["n"] == 0
    assert report.embedded["entities"] == 3 and report.embedded["atomic_facts"] == 1


def test_atomic_facts_are_found_by_meaning_with_the_chunks_that_state_them(store):
    w1 = window(S1, T1)
    run(store, windows=[w1, window(S2, T2)])
    text = "Acme Corp must deliver within 14 days of the order."
    hit = store.vector_atoms(TENANT, embed([text])[0][0], [S1, S2], 3)[0]
    assert hit["text"] == text and hit["evidence"] == "Delivery is due within 14 days of the order"
    assert hit["chunk_ids"] == [w1.chunk_ids[1]] and hit["similarity"] == pytest.approx(1.0, abs=1e-4)
    assert '"duration": "14 days"' in hit["qualifiers"]
    assert store.vector_atoms(TENANT, embed([text])[0][0], [S2], 3) == []          # outside the scope


def test_edge_facts_and_atomic_facts_do_not_leak_into_each_others_searches(store):
    run(store)
    vec = embed(["anything"])[0][0]
    assert all(h["id"] for h in store.vector_atoms(TENANT, vec, [S1, S2], 5))
    assert all("evidence" not in h for h in store.vector_facts(TENANT, vec, [S1, S2], 5))      # edge facts only


def test_building_twice_leaves_the_same_graph_fully_embedded(store):
    run(store)
    totals = {l: q(store, f"MATCH (n:{l}) RETURN count(n) AS n")[0]["n"] for l in ("Document", "Chunk", "Entity", "Fact", "Merge")}
    run(store)
    assert {l: q(store, f"MATCH (n:{l}) RETURN count(n) AS n")[0]["n"] for l in totals} == totals
    assert q(store, "MATCH (e:Entity) WHERE e.embedding IS NULL RETURN count(e) AS n")[0]["n"] == 0


def test_one_failed_source_does_not_stop_the_other(store):
    report = run(store, extractor=FakeExtractor({S1: RAW1, S2: RAW2}, fail={S2}))
    assert report.failed_windows == 1 and report.written["sources_written"] == 1
    assert report.errors and "boom" in report.errors[0]
    assert q(store, "MATCH (c:Chunk {source_id: $s}) RETURN count(c) AS n", s=S2)[0]["n"] == 0


def test_if_every_window_fails_nothing_is_written(store):
    report = run(store, extractor=FakeExtractor({}, fail={S1, S2}))
    assert report.failed_windows == 2 and "every window failed" in report.errors[-1]
    assert q(store, "MATCH (n) RETURN count(n) AS n")[0]["n"] == 0


def test_without_an_embedder_the_graph_is_built_but_not_embedded(store):
    report = build_windows(store, TENANT, [window(S1, T1)], extractor=FakeExtractor({S1: RAW1}), dimension=4)
    assert report.written["sources_written"] == 1 and "embed" not in report.timings_s
    assert q(store, "MATCH (e:Entity) WHERE e.embedding IS NOT NULL RETURN count(e) AS n")[0]["n"] == 0


def test_no_windows_is_an_empty_report(store):
    report = build_windows(store, TENANT, [], extractor=FakeExtractor({}), embed=embed, dimension=4)
    assert report.windows == 0 and not report.errors


def test_the_report_counts_what_the_extractor_proposed_outside_the_ontology(store):
    raw = {"entities": RAW1["entities"], "facts": [], "relationships": [
        {"source": "Acme Corp", "relation": "OTHER:REFUNDS", "target": "Beta Ltd", "quote": "Acme Corp supplies steel to Beta Ltd"}]}
    report = build_windows(store, TENANT, [window(S1, T1)], extractor=FakeExtractor({S1: raw}), dimension=4)
    assert report.proposed_relations["REFUNDS"] == 1
    assert q(store, "MATCH ()-[r:RELATED_TO]->() RETURN count(r) AS n")[0]["n"] == 1      # stored under the fallback


class CountingJudge:
    calls = 0

    def judge(self, pairs):
        CountingJudge.calls += 1
        return ["different"] * len(pairs)


def test_the_judge_is_used_and_its_calls_are_reported(store):
    raw = {"entities": [{"name": "Acme Holdings", "type": "COMPANY"}, {"name": "Acme Corp", "type": "COMPANY"}],
           "relationships": [], "facts": []}
    wins = [window(S1, ["Acme Holdings and Acme Corp are both parties."])]
    judge = CountingJudge()
    report = build_windows(store, TENANT, wins, extractor=FakeExtractor({S1: raw}), embed=embed, dimension=4, judge=judge)
    assert report.judge_calls == CountingJudge.calls and report.resolution["input"] == 2
    assert report.resolution["judged"] == 1 and report.resolution["verdict_different"] == 1


def test_build_sources_closes_its_session_before_the_long_work_starts(monkeypatch):
    from app.graphrag import ingest

    events = []

    class Session:
        def __enter__(self):
            events.append("open")
            return self

        def __exit__(self, *exc):
            events.append("closed")

    monkeypatch.setattr(ingest, "load_windows", lambda db, tenant, sources: events.append("load") or ["w"])
    monkeypatch.setattr(ingest, "build_windows", lambda store, tenant, windows, **kw: events.append("build") or windows)
    assert ingest.build_sources(Session, None, TENANT, extractor=None) == ["w"]
    assert events == ["open", "load", "closed", "build"]          # the session is gone before the build
