"""Durable background jobs.

`dispatch_*` is the one place routes hand off slow work. In "background" mode
(the default) that is a FastAPI background task, exactly as before. In
"durable" mode the work is a row in the `jobs` table: it survives restarts,
is retried with backoff, and can be run by any number of worker processes
(`python -m app.worker`) or by the API process itself.

The worker is a pool of threads. Each claims one job at a time with
SELECT ... FOR UPDATE SKIP LOCKED (so two workers never run the same job),
runs its handler, and records the outcome. A job whose worker died is
reclaimed once its lease expires. Handlers are idempotent: a re-run of a run
or build that already finished is a no-op.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.db.models import Job, SourceExtraction, SourceGraphBuild
from app.schemas.enums import ExtractionStatus, GraphBuildStatus

log = logging.getLogger(__name__)

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"
KIND_EXTRACTION = "extraction"
KIND_GRAPH_BUILD = "graph_build"

_wake = threading.Event()


def is_durable() -> bool:
    return get_settings().job_queue_mode == "durable"


# --- queue ---------------------------------------------------------------------------


def enqueue(db: Session, kind: str, payload: dict) -> Job:
    job = Job(
        kind=kind,
        payload=payload,
        status=PENDING,
        max_attempts=get_settings().job_max_attempts,
    )
    db.add(job)
    db.commit()
    _wake.set()  # a worker in this process picks it up without waiting to poll
    return job


def claim_next(db: Session) -> Job | None:
    """Claim one runnable job: pending and due, or running with an expired lease."""
    now = datetime.now(UTC)
    lease_cutoff = now - timedelta(seconds=get_settings().job_lease_seconds)
    job = db.execute(
        select(Job)
        .where(
            or_(
                (Job.status == PENDING) & (Job.run_after <= now),
                (Job.status == RUNNING) & (Job.locked_at < lease_cutoff),
            )
        )
        .order_by(Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if job is None:
        return None
    job.status = RUNNING
    job.attempts += 1
    job.locked_at = now
    job.updated_at = now
    db.commit()
    return job


def _finish(job_id: uuid.UUID, error: str | None) -> None:
    """Record the outcome in a fresh session: done, retry with backoff, or failed."""
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        now = datetime.now(UTC)
        job.updated_at = now
        job.locked_at = None
        if error is None:
            job.status = DONE
            job.last_error = None
        else:
            job.last_error = error[:2000]
            if job.attempts >= job.max_attempts:
                job.status = FAILED
                _on_dead(job)
            else:
                job.status = PENDING
                job.run_after = now + timedelta(seconds=min(2 ** job.attempts * 5, 300))
        db.commit()


def _on_dead(job: Job) -> None:
    """Out of attempts: make sure the thing the job was for does not stay
    `processing` forever."""
    message = f"interrupted: job failed after {job.attempts} attempts: {job.last_error}"
    try:
        if job.kind == KIND_EXTRACTION:
            from app.services.extraction_service import _mark_failed

            _mark_failed(uuid.UUID(job.payload["extraction_id"]), message)
        elif job.kind == KIND_GRAPH_BUILD:
            from app.services.graph_service import _mark_failed as mark_build_failed

            mark_build_failed(uuid.UUID(job.payload["build_id"]), message)
    except Exception:
        log.exception("could not mark the target of dead job %s failed", job.id)


# --- handlers ------------------------------------------------------------------------


def _handle_extraction(payload: dict) -> None:
    from app.agents.extraction_agent import get_extraction_agent
    from app.graph.ingestion import get_graph_ingestion
    from app.services import extraction_service, graph_service
    from app.storage.local import get_storage

    extraction_id = uuid.UUID(payload["extraction_id"])
    with SessionLocal() as db:
        extraction = db.get(SourceExtraction, extraction_id)
        still_running = (
            extraction is not None
            and extraction.status == ExtractionStatus.PROCESSING.value
        )
    if still_running:
        extraction_service.run_extraction(
            extraction_id, get_storage(), get_extraction_agent()
        )
        if payload.get("build_graph"):
            graph_service.build_after_extraction(extraction_id, get_graph_ingestion())


def _handle_graph_build(payload: dict) -> None:
    from app.graph.ingestion import get_graph_ingestion
    from app.services import graph_service

    build_id = uuid.UUID(payload["build_id"])
    with SessionLocal() as db:
        build = db.get(SourceGraphBuild, build_id)
        still_running = (
            build is not None and build.status == GraphBuildStatus.PROCESSING.value
        )
    ingestion = get_graph_ingestion()
    if still_running and ingestion is not None:
        graph_service.run_build(build_id, ingestion)


HANDLERS: dict[str, Callable[[dict], None]] = {
    KIND_EXTRACTION: _handle_extraction,
    KIND_GRAPH_BUILD: _handle_graph_build,
}


def run_job(job: Job) -> None:
    handler = HANDLERS.get(job.kind)
    if handler is None:
        _finish(job.id, f"unknown job kind {job.kind!r}")
        return
    try:
        handler(job.payload)
    except Exception as exc:  # noqa: BLE001 - recorded, retried by policy
        log.exception("job %s (%s) failed", job.id, job.kind)
        _finish(job.id, f"{type(exc).__name__}: {exc}")
    else:
        _finish(job.id, None)


# --- worker --------------------------------------------------------------------------


def worker_loop(stop: threading.Event) -> None:
    """One worker thread: claim, run, repeat; sleep when the queue is empty."""
    poll = get_settings().job_poll_seconds
    while not stop.is_set():
        job = None
        try:
            with SessionLocal() as db:
                job = claim_next(db)
        except Exception:
            log.exception("job claim failed")
        if job is None:
            _wake.wait(timeout=poll)
            _wake.clear()
            continue
        run_job(job)


def start_workers(stop: threading.Event, count: int | None = None) -> list[threading.Thread]:
    count = count or get_settings().job_worker_concurrency
    threads = [
        threading.Thread(
            target=worker_loop, args=(stop,), name=f"job-worker-{i}", daemon=True
        )
        for i in range(max(1, count))
    ]
    for t in threads:
        t.start()
    log.info("started %d job worker thread(s)", len(threads))
    return threads


# --- dispatch (the routes' only entry point) -----------------------------------------


def dispatch_extraction(
    db: Session,
    background: BackgroundTasks,
    extraction_id: uuid.UUID,
    *,
    storage,
    agent,
    graph,
    build_graph: bool,
) -> None:
    """Hand an extraction run (and optional graph build) to the configured runner."""
    from app.services import extraction_service, graph_service

    if is_durable():
        enqueue(
            db,
            KIND_EXTRACTION,
            {"extraction_id": str(extraction_id), "build_graph": bool(build_graph)},
        )
        return
    background.add_task(extraction_service.run_extraction, extraction_id, storage, agent)
    if build_graph:
        # Background tasks run in order, so this starts after the run ends.
        background.add_task(graph_service.build_after_extraction, extraction_id, graph)


def dispatch_graph_build(
    db: Session, background: BackgroundTasks, build_id: uuid.UUID, ingestion
) -> None:
    from app.services import graph_service

    if is_durable():
        enqueue(db, KIND_GRAPH_BUILD, {"build_id": str(build_id)})
        return
    background.add_task(graph_service.run_build, build_id, ingestion)
