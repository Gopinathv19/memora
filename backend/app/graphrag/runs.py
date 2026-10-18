"""Index run state in Postgres: start (the lease), heartbeat, finish, cost cap (plan Step 7).

A run is a `graph_rag_index_runs` row. The partial unique index on `(subject_id)` for `queued`/`running`
rows is the lease: `start_run` inserts, and a second run for the same subject hits the index and gets the
active one back. A run whose worker died stops blocking once its heartbeat is older than
`graphrag_run_lease_seconds` (`reap_stale` marks it `interrupted`). Every function takes or opens a short
session, so nothing holds a connection open across a build.
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.db.models import GraphRagIndexRun
from app.db.models.graphrag_run import ACTIVE_STATUSES

QUEUED_GRACE_FACTOR = 6      # a queued run nobody started within this many leases is dead


class LeaseLost(Exception):
    """The run was taken away (reaped as stale, or finished by someone else): its worker must stop writing."""


def _now() -> datetime:
    return datetime.now(UTC)


def owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def reap_stale(db: Session, subject_id: uuid.UUID | None = None) -> int:
    """Mark runs whose worker is gone `interrupted`, so they stop blocking their subject."""
    lease = timedelta(seconds=get_settings().graphrag_run_lease_seconds)
    now = _now()
    stale_running = (GraphRagIndexRun.status == "running") & (
        func.coalesce(GraphRagIndexRun.heartbeat_at, GraphRagIndexRun.started_at, GraphRagIndexRun.created_at)
        < now - lease
    )
    stale_queued = (GraphRagIndexRun.status == "queued") & (GraphRagIndexRun.created_at < now - lease * QUEUED_GRACE_FACTOR)
    stmt = (
        update(GraphRagIndexRun).where(stale_running | stale_queued)
        .values(status="interrupted", error="The run stopped reporting (the worker died or the job was lost)", finished_at=now)
    )
    if subject_id is not None:
        stmt = stmt.where(GraphRagIndexRun.subject_id == subject_id)
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def start_run(
    db: Session, tenant_id: uuid.UUID, subject_id: uuid.UUID, *, source_ids: list[str] | None = None,
    options: dict | None = None, trigger: str = "manual",
) -> tuple[GraphRagIndexRun, bool]:
    """(run, created). When the subject already has an active run, that one is returned and `created` is False."""
    reap_stale(db, subject_id)
    for _attempt in range(3):
        run = GraphRagIndexRun(
            tenant_id=tenant_id, subject_id=subject_id, status="queued", trigger=trigger,
            source_ids=source_ids, options=options or {},
        )
        db.add(run)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            active = db.execute(
                select(GraphRagIndexRun).where(
                    GraphRagIndexRun.subject_id == subject_id, GraphRagIndexRun.status.in_(ACTIVE_STATUSES)
                )
            ).scalar_one_or_none()
            if active is not None:
                return active, False
            continue            # the run that blocked us finished in between: try again
        db.refresh(run)
        return run, True
    raise RuntimeError("could not start an index run: the subject's lease kept changing")


def claim(run_id: uuid.UUID) -> GraphRagIndexRun | None:
    """queued -> running, once. A second delivery of the same job (a retry, a duplicate) gets None."""
    with SessionLocal() as db:
        now = _now()
        claimed = db.execute(
            update(GraphRagIndexRun)
            .where(GraphRagIndexRun.id == run_id, GraphRagIndexRun.status == "queued")
            .values(status="running", started_at=now, heartbeat_at=now, attempts=GraphRagIndexRun.attempts + 1,
                    owner=owner_id())
        ).rowcount
        db.commit()
        if not claimed:
            return None
        run = db.get(GraphRagIndexRun, run_id)
        db.expunge(run)
        return run


def heartbeat(run_id: uuid.UUID, stats: dict | None = None) -> bool:
    """Report that the run is alive. False means it is no longer `running` (it was reaped): the caller lost the lease."""
    with SessionLocal() as db:
        values: dict = {"heartbeat_at": _now()}
        if stats is not None:
            values["stats"] = stats
        updated = db.execute(
            update(GraphRagIndexRun).where(GraphRagIndexRun.id == run_id, GraphRagIndexRun.status == "running").values(**values)
        ).rowcount
        db.commit()
        return bool(updated)


def still_running(run_id: uuid.UUID) -> bool:
    with SessionLocal() as db:
        return db.execute(select(GraphRagIndexRun.status).where(GraphRagIndexRun.id == run_id)).scalar_one_or_none() == "running"


def set_plan(run_id: uuid.UUID, plan: dict) -> None:
    with SessionLocal() as db:
        db.execute(update(GraphRagIndexRun).where(GraphRagIndexRun.id == run_id).values(plan=plan))
        db.commit()


def finish(run_id: uuid.UUID, status: str, *, stats: dict | None = None, error: str | None = None, cost_usd: float = 0.0) -> bool:
    """Record the outcome. Only an active run can be finished: a run already marked `interrupted` (its lease was taken)
    is not turned back into `done` by a worker that did not notice. Returns whether anything was updated."""
    with SessionLocal() as db:
        values: dict = {"status": status, "error": (error or None) and error[:2000], "cost_usd": cost_usd, "finished_at": _now()}
        if stats is not None:
            values["stats"] = stats
        updated = db.execute(
            update(GraphRagIndexRun).where(GraphRagIndexRun.id == run_id, GraphRagIndexRun.status.in_(ACTIVE_STATUSES)).values(**values)
        ).rowcount
        db.commit()
        return bool(updated)


def spent_since(db: Session, tenant_id: uuid.UUID, hours: float = 24.0) -> float:
    cutoff = _now() - timedelta(hours=hours)
    total = db.execute(
        select(func.coalesce(func.sum(GraphRagIndexRun.cost_usd), 0.0)).where(
            GraphRagIndexRun.tenant_id == tenant_id, GraphRagIndexRun.created_at >= cutoff
        )
    ).scalar_one()
    return float(total)


def cost_cap_status(db: Session, tenant_id: uuid.UUID) -> dict:
    """{"cap", "spent", "blocked"}; a cap of 0 means none."""
    cap = get_settings().graphrag_tenant_daily_cost_cap_usd
    spent = spent_since(db, tenant_id)
    return {"cap": cap, "spent": round(spent, 6), "blocked": bool(cap > 0 and spent >= cap)}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def fail_interrupted_runs(db: Session) -> int:
    """At startup: mark runs whose process is gone `interrupted`, and only those.

    A run records its owner (`host:pid`). One owned by a process on this host that no longer exists is dead; one
    owned by another host, or by a process that is still alive (another worker of this deployment), is left to its
    heartbeat lease. A run with no recorded owner (older rows) counts as dead.
    """
    host = socket.gethostname()
    running = db.execute(select(GraphRagIndexRun).where(GraphRagIndexRun.status == "running")).scalars().all()
    dead = []
    for run in running:
        if not run.owner:
            dead.append(run.id)
            continue
        run_host, _, pid = run.owner.rpartition(":")
        if run_host == host and pid.isdigit() and not _pid_alive(int(pid)):
            dead.append(run.id)
    if not dead:
        return 0
    db.execute(
        update(GraphRagIndexRun).where(GraphRagIndexRun.id.in_(dead), GraphRagIndexRun.status == "running")
        .values(status="interrupted", error="Interrupted: the process running this index run is gone", finished_at=_now())
    )
    db.commit()
    return len(dead)


def list_runs(db: Session, subject_id: uuid.UUID, limit: int = 20) -> list[GraphRagIndexRun]:
    return list(db.execute(
        select(GraphRagIndexRun).where(GraphRagIndexRun.subject_id == subject_id)
        .order_by(GraphRagIndexRun.created_at.desc()).limit(limit)
    ).scalars())
