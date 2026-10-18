"""Index runs: the lease, the plan, the job and its endpoints (plan Step 7).

Real PostgreSQL (the repo's test database) and a throwaway FalkorDB; the model providers are fakes. The `rag`
fixture points the application's graph store at the throwaway one, so nothing here can reach a configured FalkorDB.
"""

import os
import socket
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.db.models import GraphRagIndexRun, RetrievalChunk
from app.graph.ontology import get_ontology
from app.graphrag import index_job, runs
from app.graphrag.extract import WindowResult, parse_reply
from app.graphrag.graph_write import chunk_fingerprint, current_signature
from app.graphrag.index_job import make_plan
from app.graphrag.store import GraphRagStore
from tests.test_graph_api import _setup, _upload
from tests.test_graphrag_ingest import FakeExtractor, embed

ONT = get_ontology()
DOC = "Acme Corp supplies steel to Beta Ltd.\n\nDelivery is due within 14 days of the order."
RAW = {
    "entities": [{"name": "Acme Corp", "type": "COMPANY"}, {"name": "Beta Ltd", "type": "COMPANY"}],
    "relationships": [{"source": "Acme Corp", "relation": "PROVIDES_TO", "target": "Beta Ltd",
                       "quote": "Acme Corp supplies steel to Beta Ltd"}],
    "facts": [{"text": "Delivery is due within 14 days of the order.", "entities": ["Acme Corp"],
               "quote": "Delivery is due within 14 days of the order"}],
}


# --- the plan (pure) ----------------------------------------------------------------------------


def doc(source, fingerprint="fp", signature="sig", subject="subj"):
    return {"id": source, "subject_id": subject, "extraction_id": "e", "signature": signature, "chunk_fingerprint": fingerprint}


def plan(active, docs, **kw):
    kw.setdefault("subject_id", "subj")
    kw.setdefault("all_source_ids", set(active) | {d["id"] for d in docs})
    return make_plan(active, docs, "sig", **kw)


def test_a_source_with_no_graph_document_is_new():
    p = plan({"s1": "fp"}, [])
    assert p.to_build == [{"source_id": "s1", "reason": "new"}] and p.summary()["reasons"] == {"new": 1}


def test_a_different_chunk_fingerprint_means_changed_whatever_the_extraction():
    p = plan({"s1": "fp-new"}, [doc("s1", fingerprint="fp-old")])
    assert p.to_build == [{"source_id": "s1", "reason": "changed"}]


def test_an_unchanged_source_is_up_to_date():
    p = plan({"s1": "fp"}, [doc("s1")])
    assert p.to_build == [] and p.up_to_date == ["s1"] and p.outdated == []


def test_other_versions_make_a_document_outdated_and_it_is_rebuilt_only_on_request():
    p = plan({"s1": "fp"}, [doc("s1", signature="older")])
    assert p.to_build == [] and p.outdated == ["s1"] and p.summary()["outdated_not_rebuilt"] == 1
    p = plan({"s1": "fp"}, [doc("s1", signature="older")], rebuild_outdated=True)
    assert p.to_build == [{"source_id": "s1", "reason": "outdated"}]


def test_requested_sources_are_rebuilt_even_when_up_to_date():
    p = plan({"s1": "fp", "s2": "fp"}, [doc("s1"), doc("s2")], requested=["s2"])
    assert p.to_build == [{"source_id": "s2", "reason": "requested"}] and p.up_to_date == ["s1"]


def test_orphans_are_this_subjects_documents_whose_source_postgres_no_longer_has():
    docs = [doc("gone", subject="subj"), doc("elsewhere", subject="other"), doc("kept")]
    p = make_plan({"kept": "fp"}, docs, "sig", subject_id="subj", all_source_ids={"kept"})
    assert p.orphans == ["gone"]                          # "elsewhere" belongs to another subject: not ours to remove


# --- fixtures ----------------------------------------------------------------------------------

pytestmark_graph = pytest.mark.skipif(not os.environ.get("TEST_FALKORDB_URL"), reason="set TEST_FALKORDB_URL")


@pytest.fixture
def rag(graph_store, monkeypatch):
    from app.graph import store as store_module

    monkeypatch.setattr(store_module, "get_graph_store", lambda: graph_store)
    return GraphRagStore(graph_store)


def make_source(client, fake_llm, text=DOC):
    tenant_id, _, subject_id = _setup(client)
    response = _upload(client, subject_id, text, extract=True)
    assert response.status_code == 201, response.text
    return uuid.UUID(tenant_id), uuid.UUID(subject_id), response.json()["id"]


def active_chunk_ids(source_id):
    with SessionLocal() as db:
        return sorted(str(i) for i in db.execute(
            select(RetrievalChunk.id).where(RetrievalChunk.source_id == uuid.UUID(source_id), RetrievalChunk.is_active.is_(True))).scalars())


class Counting(FakeExtractor):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls = 0

    def extract(self, win):
        self.calls += 1
        return super().extract(win)


def new_run(tenant_id, subject_id, **kw):
    with SessionLocal() as db:
        run, created = runs.start_run(db, tenant_id, subject_id, **kw)
        return run.id, created


def execute(run_id, rag, extractor, **kw):
    return index_job.run_index(run_id, store=rag, extractor=extractor, embed=embed, dimension=4, client=object(), **kw)


def get_run(run_id):
    with SessionLocal() as db:
        return db.get(GraphRagIndexRun, run_id)


def graph_chunks(rag, tenant_id, source_id):
    return sorted(r["id"] for r in rag._read(tenant_id, "MATCH (c:Chunk {source_id: $s}) RETURN c.id AS id", {"s": source_id}))


# --- the lease and the run state -----------------------------------------------------------------


def test_one_active_run_per_subject_and_a_new_one_after_it_finishes(client, fake_llm):
    tenant_id, _, subject_id = _setup(client)
    tenant_id, subject_id = uuid.UUID(tenant_id), uuid.UUID(subject_id)
    first, created = new_run(tenant_id, subject_id)
    again, created_again = new_run(tenant_id, subject_id)
    assert created and not created_again and again == first
    runs.finish(first, "done")
    third, created_third = new_run(tenant_id, subject_id)
    assert created_third and third != first


def test_the_database_itself_refuses_a_second_active_run(client, fake_llm):
    tenant_id, _, subject_id = _setup(client)
    with SessionLocal() as db:
        db.add(GraphRagIndexRun(tenant_id=uuid.UUID(tenant_id), subject_id=uuid.UUID(subject_id), status="running"))
        db.commit()
        db.add(GraphRagIndexRun(tenant_id=uuid.UUID(tenant_id), subject_id=uuid.UUID(subject_id), status="queued"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_a_run_whose_worker_died_stops_blocking_its_subject(client, fake_llm):
    tenant_id, _, subject_id = _setup(client)
    tenant_id, subject_id = uuid.UUID(tenant_id), uuid.UUID(subject_id)
    first, _ = new_run(tenant_id, subject_id)
    assert runs.claim(first) is not None
    old = datetime.now(UTC) - timedelta(seconds=get_settings().graphrag_run_lease_seconds + 60)
    with SessionLocal() as db:
        db.execute(update(GraphRagIndexRun).where(GraphRagIndexRun.id == first).values(heartbeat_at=old))
        db.commit()
    second, created = new_run(tenant_id, subject_id)
    assert created and second != first and get_run(first).status == "interrupted"
    assert "stopped reporting" in get_run(first).error


def test_a_fresh_heartbeat_keeps_the_lease(client, fake_llm):
    tenant_id, _, subject_id = _setup(client)
    tenant_id, subject_id = uuid.UUID(tenant_id), uuid.UUID(subject_id)
    first, _ = new_run(tenant_id, subject_id)
    runs.claim(first)
    runs.heartbeat(first, {"windows_done": 3})
    again, created = new_run(tenant_id, subject_id)
    assert not created and again == first and get_run(first).stats == {"windows_done": 3}


def test_a_run_is_claimed_once_and_finish_records_the_outcome(client, fake_llm):
    tenant_id, _, subject_id = _setup(client)
    rid, _ = new_run(uuid.UUID(tenant_id), uuid.UUID(subject_id))
    assert runs.claim(rid) is not None and runs.claim(rid) is None
    runs.finish(rid, "done", stats={"a": 1}, cost_usd=0.25)
    done = get_run(rid)
    assert (done.status, done.stats, done.cost_usd, done.attempts) == ("done", {"a": 1}, 0.25, 1) and done.finished_at


def test_the_daily_cost_cap_counts_only_the_last_day_and_zero_means_no_cap(client, fake_llm, monkeypatch):
    tenant_id, _, subject_id = _setup(client)
    tenant, subject = uuid.UUID(tenant_id), uuid.UUID(subject_id)
    with SessionLocal() as db:
        db.add(GraphRagIndexRun(tenant_id=tenant, subject_id=subject, status="done", cost_usd=1.5))
        db.add(GraphRagIndexRun(tenant_id=tenant, subject_id=subject, status="done", cost_usd=9.0,
                                created_at=datetime.now(UTC) - timedelta(days=2)))
        db.commit()
        monkeypatch.setattr(get_settings(), "graphrag_tenant_daily_cost_cap_usd", 1.0)
        assert runs.cost_cap_status(db, tenant) == {"cap": 1.0, "spent": 1.5, "blocked": True}
        monkeypatch.setattr(get_settings(), "graphrag_tenant_daily_cost_cap_usd", 2.0)
        assert runs.cost_cap_status(db, tenant)["blocked"] is False
        monkeypatch.setattr(get_settings(), "graphrag_tenant_daily_cost_cap_usd", 0.0)
        assert runs.cost_cap_status(db, tenant)["blocked"] is False


def test_at_startup_only_runs_whose_process_is_gone_are_marked_interrupted(client, fake_llm):
    import subprocess

    dead = subprocess.Popen(["true"])
    dead.wait()                                                                 # a pid that existed and is gone
    host = socket.gethostname()
    owners = {"gone": f"{host}:{dead.pid}", "alive": f"{host}:{os.getpid()}", "elsewhere": "another-host:123", "legacy": None}
    ids = {}
    for name, owner in owners.items():
        tenant_id, _, subject_id = _setup(client, name=f"T-{name}")
        rid, _ = new_run(uuid.UUID(tenant_id), uuid.UUID(subject_id))
        runs.claim(rid)
        with SessionLocal() as db:
            db.execute(update(GraphRagIndexRun).where(GraphRagIndexRun.id == rid).values(owner=owner))
            db.commit()
        ids[name] = rid
    with SessionLocal() as db:
        assert runs.fail_interrupted_runs(db) == 2
    status = {name: get_run(rid).status for name, rid in ids.items()}
    assert status == {"gone": "interrupted", "alive": "running", "elsewhere": "running", "legacy": "interrupted"}
    assert "gone" in get_run(ids["gone"]).error


def test_claiming_records_which_process_owns_the_run(client, fake_llm):
    tenant_id, _, subject_id = _setup(client)
    rid, _ = new_run(uuid.UUID(tenant_id), uuid.UUID(subject_id))
    runs.claim(rid)
    assert get_run(rid).owner == runs.owner_id() == f"{socket.gethostname()}:{os.getpid()}"


def test_a_run_that_lost_its_lease_cannot_be_finished_or_heartbeat(client, fake_llm):
    tenant_id, _, subject_id = _setup(client)
    rid, _ = new_run(uuid.UUID(tenant_id), uuid.UUID(subject_id))
    runs.claim(rid)
    old = datetime.now(UTC) - timedelta(seconds=get_settings().graphrag_run_lease_seconds + 60)
    with SessionLocal() as db:
        db.execute(update(GraphRagIndexRun).where(GraphRagIndexRun.id == rid).values(heartbeat_at=old))
        db.commit()
        runs.reap_stale(db)
    assert get_run(rid).status == "interrupted"
    assert runs.heartbeat(rid, {"x": 1}) is False and runs.still_running(rid) is False
    assert runs.finish(rid, "done", stats={"x": 1}) is False                 # not turned back into "done"
    assert get_run(rid).status == "interrupted" and get_run(rid).stats == {}


def test_finish_reports_whether_it_applied(client, fake_llm):
    tenant_id, _, subject_id = _setup(client)
    rid, _ = new_run(uuid.UUID(tenant_id), uuid.UUID(subject_id))
    runs.claim(rid)
    assert runs.heartbeat(rid) is True and runs.finish(rid, "done") is True and runs.finish(rid, "failed") is False
    assert get_run(rid).status == "done"


def test_start_run_tries_again_when_the_run_that_blocked_it_has_just_finished(client, fake_llm):
    tenant_id, _, subject_id = _setup(client)
    with SessionLocal() as db:
        real_commit, calls = db.commit, []

        def flaky():
            calls.append(1)
            if len(calls) == 2:       # the 1st commit is reap_stale's; the 2nd is the insert
                raise IntegrityError("insert", {}, Exception("unique violation"))   # the blocker "finished" before we looked
            real_commit()

        db.commit = flaky
        run, created = runs.start_run(db, uuid.UUID(tenant_id), uuid.UUID(subject_id))
    assert created and len(calls) == 3 and get_run(run.id).status == "queued"       # reap, failed insert, insert


@pytestmark_graph
def test_a_worker_that_lost_its_lease_stops_instead_of_finishing_the_run(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)

    class LosesTheLease(Counting):
        def extract(self, win):
            with SessionLocal() as db:                                  # another worker reaps this run mid-build
                db.execute(update(GraphRagIndexRun).where(GraphRagIndexRun.subject_id == subject_id).values(status="interrupted"))
                db.commit()
            return super().extract(win)

    rid, _ = new_run(tenant_id, subject_id)
    out = execute(rid, rag, LosesTheLease({source_id: RAW}))
    assert "abandoned" in out and get_run(rid).status == "interrupted" and "communities" not in (get_run(rid).stats or {})


@pytestmark_graph
def test_a_source_that_lost_all_its_active_chunks_is_removed_from_the_graph(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    execute(new_run(tenant_id, subject_id)[0], rag, Counting({source_id: RAW}))
    assert source_id in {d["id"] for d in rag.documents_state(tenant_id)}
    with SessionLocal() as db:
        db.execute(update(RetrievalChunk).where(RetrievalChunk.source_id == uuid.UUID(source_id)).values(is_active=False))
        db.commit()
    rid, _ = new_run(tenant_id, subject_id)
    execute(rid, rag, Counting({source_id: RAW}))
    run = get_run(rid)
    assert run.status == "done" and run.stats["inactive_removed"] == 1 and run.plan["inactive"] == 1
    assert rag.documents_state(tenant_id) == []


def test_the_plan_finds_orphans_in_sub_folders_and_sources_with_no_active_chunks():
    docs = [doc("child-gone", subject="child"), doc("elsewhere", subject="stranger"), doc("inactive", subject="subj"), doc("fine")]
    p = make_plan({"fine": "fp"}, docs, "sig", subject_id="subj", all_source_ids={"fine", "inactive", "elsewhere"},
                  subject_ids={"subj", "child"}, in_subject={"fine", "inactive"})
    assert p.orphans == ["child-gone"]                 # a descendant subject's orphan, found from the parent
    assert p.inactive == ["inactive"]                  # in Postgres, in the subject, but nothing active
    assert p.up_to_date == ["fine"]


# --- the job ----------------------------------------------------------------------------------


@pytestmark_graph
def test_a_run_builds_a_new_source_and_records_what_it_did(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    ex = Counting({source_id: RAW})
    rid, _ = new_run(tenant_id, subject_id)
    out = execute(rid, rag, ex)
    run = get_run(rid)
    assert run.status == "done" and not run.error and ex.calls >= 1
    assert run.plan["reasons"] == {"new": 1} and run.stats["build"]["written"]["sources_written"] == 1
    assert graph_chunks(rag, tenant_id, source_id) == active_chunk_ids(source_id)          # the graph holds Postgres's own chunk ids
    state = {d["id"]: d for d in rag.documents_state(tenant_id)}[source_id]
    assert state["chunk_fingerprint"] == chunk_fingerprint(active_chunk_ids(source_id)) and state["signature"] == current_signature(ONT)
    assert out["plan"]["to_build"] == 1 and run.finished_at and run.started_at


@pytestmark_graph
def test_a_second_run_with_nothing_changed_builds_nothing(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    ex = Counting({source_id: RAW})
    execute(new_run(tenant_id, subject_id)[0], rag, ex)
    calls = ex.calls
    rid, _ = new_run(tenant_id, subject_id)
    execute(rid, rag, ex)
    run = get_run(rid)
    assert run.status == "done" and ex.calls == calls and run.plan["up_to_date"] == 1 and "build" not in run.stats


@pytestmark_graph
def test_rechunking_a_source_is_noticed_and_the_graph_follows_the_new_chunk_ids(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    ex = Counting({source_id: RAW})
    execute(new_run(tenant_id, subject_id)[0], rag, ex)
    old_ids = active_chunk_ids(source_id)
    assert client.post(f"/api/v1/sources/{source_id}/rechunk").status_code in (200, 201, 202)
    new_ids = active_chunk_ids(source_id)
    assert new_ids and set(new_ids).isdisjoint(old_ids)               # the ids changed under the graph
    rid, _ = new_run(tenant_id, subject_id)
    execute(rid, rag, ex)
    assert get_run(rid).plan["reasons"] == {"changed": 1}
    assert graph_chunks(rag, tenant_id, source_id) == new_ids


@pytestmark_graph
def test_deleting_a_source_through_the_api_cleans_the_graph_by_the_stores_own_hook(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    execute(new_run(tenant_id, subject_id)[0], rag, Counting({source_id: RAW}))
    assert rag._read(tenant_id, "MATCH (f:Fact {kind: 'atom'}) RETURN count(f) AS n")[0]["n"] == 1
    assert client.delete(f"/api/v1/sources/{source_id}").status_code in (200, 204)
    assert rag._read(tenant_id, "MATCH (d:Document) RETURN count(d) AS n")[0]["n"] == 0
    assert rag._read(tenant_id, "MATCH (f:Fact {kind: 'atom'}) RETURN count(f) AS n")[0]["n"] == 0


@pytestmark_graph
def test_a_document_whose_source_is_gone_is_removed_as_an_orphan(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    ghost = str(uuid.uuid4())
    rag.base.upsert_document(tenant_id, {"id": ghost, "application_id": "a", "subject_id": str(subject_id), "filename": "x",
                                         "extraction_id": "e", "extraction_version": 1, "build_id": "b"})
    rid, _ = new_run(tenant_id, subject_id)
    execute(rid, rag, Counting({source_id: RAW}))
    assert get_run(rid).stats["orphans_removed"] == 1
    assert ghost not in {d["id"] for d in rag.documents_state(tenant_id)}


@pytestmark_graph
def test_a_tenant_over_its_cap_is_blocked_and_nothing_is_extracted(client, fake_llm, rag, monkeypatch):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    with SessionLocal() as db:
        db.add(GraphRagIndexRun(tenant_id=tenant_id, subject_id=subject_id, status="done", cost_usd=5.0))
        db.commit()
    monkeypatch.setattr(get_settings(), "graphrag_tenant_daily_cost_cap_usd", 1.0)
    ex = Counting({source_id: RAW})
    rid, _ = new_run(tenant_id, subject_id)
    execute(rid, rag, ex)
    run = get_run(rid)
    assert run.status == "blocked" and "cost cap" in run.error and ex.calls == 0
    assert rag.documents_state(tenant_id) == []


@pytestmark_graph
def test_a_run_where_every_window_fails_is_failed_and_leaves_the_graph_alone(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    rid, _ = new_run(tenant_id, subject_id)
    execute(rid, rag, Counting({}, fail={source_id}))
    run = get_run(rid)
    assert run.status == "failed" and "every window failed" in run.error
    assert rag.documents_state(tenant_id) == []


@pytestmark_graph
def test_an_extractor_that_raises_fails_its_windows_and_the_run_records_why(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)

    class Exploding:
        def extract(self, win):
            raise RuntimeError("kaboom")

    rid, _ = new_run(tenant_id, subject_id)
    execute(rid, rag, Exploding())
    run = get_run(rid)
    assert run.status == "failed" and "every window failed" in run.error
    assert any("kaboom" in e for e in run.stats["build"]["errors"])               # the window's error is on the run


@pytestmark_graph
def test_an_unexpected_error_elsewhere_fails_the_run_instead_of_escaping(client, fake_llm, rag, monkeypatch):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)

    def boom(*a, **kw):
        raise RuntimeError("falkordb went away")

    monkeypatch.setattr(rag, "upsert_retrieval_chunks", boom)
    rid, _ = new_run(tenant_id, subject_id)
    out = execute(rid, rag, Counting({source_id: RAW}))
    run = get_run(rid)
    assert run.status == "failed" and "falkordb went away" in run.error and "failed" in out


@pytestmark_graph
def test_a_duplicate_delivery_of_the_same_job_does_nothing(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    ex = Counting({source_id: RAW})
    rid, _ = new_run(tenant_id, subject_id)
    execute(rid, rag, ex)
    calls = ex.calls
    again = execute(rid, rag, ex)
    assert "skipped" in again and ex.calls == calls and get_run(rid).attempts == 1


@pytestmark_graph
def test_outdated_documents_are_rebuilt_only_when_asked(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    ex = Counting({source_id: RAW})
    execute(new_run(tenant_id, subject_id)[0], rag, ex)
    calls = ex.calls
    rid, _ = new_run(tenant_id, subject_id)
    execute(rid, rag, ex, signature="a-newer-prompt-version")                      # versions changed, nothing asked
    assert get_run(rid).plan["outdated_not_rebuilt"] == 1 and ex.calls == calls
    rid, _ = new_run(tenant_id, subject_id, options={"rebuild_outdated": True})
    execute(rid, rag, ex, signature="a-newer-prompt-version")
    assert get_run(rid).plan["reasons"] == {"outdated": 1} and ex.calls > calls
    assert {d["id"]: d for d in rag.documents_state(tenant_id)}[source_id]["signature"] == "a-newer-prompt-version"


@pytestmark_graph
def test_a_requested_source_is_rebuilt_even_when_nothing_changed(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    ex = Counting({source_id: RAW})
    execute(new_run(tenant_id, subject_id)[0], rag, ex)
    calls = ex.calls
    rid, _ = new_run(tenant_id, subject_id, source_ids=[source_id])
    execute(rid, rag, ex)
    assert get_run(rid).plan["reasons"] == {"requested": 1} and ex.calls > calls


# --- the endpoints ----------------------------------------------------------------------------


@pytestmark_graph
def test_the_build_endpoint_queues_a_run_hands_it_on_and_reuses_an_active_one(client, fake_llm, rag, monkeypatch):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    delivered = []
    monkeypatch.setattr(index_job, "run_index", lambda run_id: delivered.append(run_id))      # the job itself is tested above
    first = client.post(f"/api/v1/subjects/{subject_id}/graph-rag/build", json={})
    assert first.status_code == 202 and first.json()["status"] == "queued" and first.json()["trigger"] == "api"
    assert [str(d) for d in delivered] == [first.json()["id"]]
    second = client.post(f"/api/v1/subjects/{subject_id}/graph-rag/build", json={"rebuild_outdated": True})
    assert second.status_code == 200 and second.json()["id"] == first.json()["id"] and len(delivered) == 1

    listing = client.get(f"/api/v1/subjects/{subject_id}/graph-rag/runs").json()
    assert [r["id"] for r in listing] == [first.json()["id"]]
    one = client.get(f"/api/v1/graph-rag/runs/{first.json()['id']}")
    assert one.status_code == 200 and one.json()["subject_id"] == str(subject_id)


@pytestmark_graph
def test_the_build_endpoint_rejects_sources_that_are_not_the_subjects_and_unknown_runs(client, fake_llm, rag):
    tenant_id, subject_id, source_id = make_source(client, fake_llm)
    bad = client.post(f"/api/v1/subjects/{subject_id}/graph-rag/build", json={"source_ids": [str(uuid.uuid4())]})
    assert bad.status_code == 422
    assert client.get(f"/api/v1/graph-rag/runs/{uuid.uuid4()}").status_code == 404
    ok = client.post(f"/api/v1/subjects/{subject_id}/graph-rag/build", json={"source_ids": [source_id]})
    assert ok.status_code == 202 and ok.json()["source_ids"] == [source_id]
