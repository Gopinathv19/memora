"""The durable job queue: claim, retry with backoff, lease recovery."""

from datetime import UTC, datetime, timedelta

from app.db.database import SessionLocal
from app.db.models import Job
from app.services import job_service


def _enqueue(kind="extraction", payload=None) -> Job:
    with SessionLocal() as db:
        job = job_service.enqueue(db, kind, payload or {"extraction_id": "x"})
        db.expunge(job)
        return job


def _get(job_id) -> Job:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        db.expunge(job)
        return job


def test_claim_runs_each_job_once():
    job = _enqueue()
    with SessionLocal() as db:
        claimed = job_service.claim_next(db)
        assert claimed is not None and claimed.id == job.id
        assert claimed.status == "running" and claimed.attempts == 1
    with SessionLocal() as db:
        assert job_service.claim_next(db) is None


def test_success_marks_done(monkeypatch):
    seen = []
    monkeypatch.setitem(job_service.HANDLERS, "extraction", seen.append)
    job = _enqueue(payload={"k": 1})
    with SessionLocal() as db:
        claimed = job_service.claim_next(db)
    job_service.run_job(claimed)
    assert seen == [{"k": 1}]
    assert _get(job.id).status == "done"


def test_failure_retries_with_backoff_then_fails(monkeypatch):
    dead = []
    monkeypatch.setattr(job_service, "_on_dead", lambda j: dead.append(j.id))

    def boom(_payload):
        raise RuntimeError("provider down")

    monkeypatch.setitem(job_service.HANDLERS, "extraction", boom)
    job = _enqueue()
    for attempt in range(1, job.max_attempts + 1):
        with SessionLocal() as db:
            # Make the retry due now so the test does not wait out the backoff.
            row = db.get(Job, job.id)
            row.run_after = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()
            claimed = job_service.claim_next(db)
        assert claimed is not None and claimed.attempts == attempt
        job_service.run_job(claimed)
        row = _get(job.id)
        if attempt < job.max_attempts:
            assert row.status == "pending" and row.run_after > datetime.now(UTC)
    assert row.status == "failed" and "provider down" in row.last_error
    assert dead == [job.id]


def test_expired_lease_is_reclaimed():
    job = _enqueue()
    with SessionLocal() as db:
        job_service.claim_next(db)
    with SessionLocal() as db:
        row = db.get(Job, job.id)
        row.locked_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    with SessionLocal() as db:
        reclaimed = job_service.claim_next(db)
    assert reclaimed is not None and reclaimed.id == job.id and reclaimed.attempts == 2


def test_llm_json_cache_serves_repeat_calls_without_the_model():
    from types import SimpleNamespace as NS

    from app.llm.cache import cached_chat_json

    calls = []

    class Client:
        def chat_json(self, model, system, user):
            calls.append(user)
            return {"entities": [user]}, NS(prompt_tokens=1)

    first = cached_chat_json(Client(), "m", "sys", "text A")
    again = cached_chat_json(Client(), "m", "sys", "text A")
    other = cached_chat_json(Client(), "m", "sys", "text B")
    assert first[2] is False and again[2] is True and other[2] is False
    assert again[0] == first[0] and again[1] is None
    assert calls == ["text A", "text B"]
    # A different system prompt is a different request.
    assert cached_chat_json(Client(), "m", "sys v2", "text A")[2] is False
