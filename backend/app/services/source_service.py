import uuid
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.core.errors import NotFoundError, ValidationError
from app.db.models import Actor, Application, Source, Subject, Tenant
from app.graph import sync as graph_sync
from app.schemas.enums import SourceStatus, SourceType
from app.schemas.source import SourceCreate, SourceDetail, SourceUpdate
from app.services.scope import Scope
from app.services.subject_service import get_subject
from app.storage import StorageBackend
from app.storage.local import build_key


def _resolve_actor_id(
    db: Session, actor_id: uuid.UUID | None, subject: Subject
) -> uuid.UUID | None:
    if actor_id is None:
        return None
    actor = db.get(Actor, actor_id)
    if actor is None or actor.application_id != subject.application_id:
        raise ValidationError(
            "created_by_actor_id does not reference an actor in this application"
        )
    return actor.id


def create_source(
    db: Session, subject_id: uuid.UUID, payload: SourceCreate, scope: Scope
) -> Source:
    """Register a source from metadata, with no bytes attached.

    Used for a URL, a chat transcript, or a file already sitting in external
    storage. The ownership columns are copied off the resolved subject, so the
    client cannot choose them.
    """
    subject = get_subject(db, subject_id, scope)
    source = Source(
        tenant_id=subject.tenant_id,
        application_id=subject.application_id,
        subject_id=subject.id,
        created_by_actor_id=_resolve_actor_id(db, payload.created_by_actor_id, subject),
        type=payload.type.value,
        mime_type=payload.mime_type,
        filename=payload.filename,
        storage_uri=payload.storage_uri,
        size_bytes=payload.size_bytes,
        status=payload.status.value,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def create_source_from_upload(
    db: Session,
    subject_id: uuid.UUID,
    fileobj: BinaryIO,
    filename: str | None,
    content_type: str | None,
    scope: Scope,
    storage: StorageBackend,
    created_by_actor_id: uuid.UUID | None = None,
) -> Source:
    """Register a source *and* store its bytes.

    Order matters: the subject is resolved (and scope-checked) before a single
    byte is written, so an unauthorized upload never touches storage. The
    database row is written last, and if that fails the stored object is
    removed -- otherwise a failed request would leave an orphan file that
    nothing references and nothing will ever clean up.
    """
    subject = get_subject(db, subject_id, scope)
    actor_id = _resolve_actor_id(db, created_by_actor_id, subject)

    key = build_key(subject.tenant_id, subject.id, filename)
    stored = storage.put(key, fileobj, content_type)

    source = Source(
        tenant_id=subject.tenant_id,
        application_id=subject.application_id,
        subject_id=subject.id,
        created_by_actor_id=actor_id,
        type=SourceType.FILE.value,
        mime_type=content_type,
        filename=filename,
        storage_uri=stored.storage_uri,
        size_bytes=stored.size_bytes,
        status=SourceStatus.PENDING.value,
    )
    db.add(source)
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(stored.storage_uri)
        raise
    db.refresh(source)
    return source


def get_source(db: Session, source_id: uuid.UUID, scope: Scope) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise NotFoundError("Source not found")
    scope.assert_owns("Source", source.tenant_id, source.application_id)
    return source


def get_source_in_subject(
    db: Session, subject_id: uuid.UUID, source_id: uuid.UUID, scope: Scope
) -> Source:
    """Resolve a source that must live in a specific subject.

    This is the strict form of the ownership rule: the tenant, application,
    subject and source ids all have to describe one chain. Knowing the source
    id is not enough.
    """
    source = get_source(db, source_id, scope)
    if source.subject_id != subject_id:
        raise NotFoundError("Source not found")
    return source


def _query(
    scope: Scope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    status: str | None = None,
    source_id: uuid.UUID | None = None,
):
    actor_alias = aliased(Actor)
    stmt = (
        select(
            Source,
            Tenant.name,
            Application.name,
            Subject.external_id,
            actor_alias.external_id,
        )
        .join(Tenant, Tenant.id == Source.tenant_id)
        .join(Application, Application.id == Source.application_id)
        .join(Subject, Subject.id == Source.subject_id)
        .outerjoin(actor_alias, actor_alias.id == Source.created_by_actor_id)
        .where(
            scope.tenant_predicate(Source.tenant_id),
            scope.application_predicate(Source.application_id),
        )
        .order_by(Source.created_at.desc())
    )
    if tenant_id is not None:
        stmt = stmt.where(Source.tenant_id == tenant_id)
    if application_id is not None:
        stmt = stmt.where(Source.application_id == application_id)
    if subject_id is not None:
        stmt = stmt.where(Source.subject_id == subject_id)
    if status is not None:
        stmt = stmt.where(Source.status == status)
    if source_id is not None:
        stmt = stmt.where(Source.id == source_id)
    return stmt


def _to_schema(rows) -> list[SourceDetail]:
    return [
        SourceDetail(
            **SourceDetail.model_validate(source).model_dump(
                exclude={
                    "tenant_name",
                    "application_name",
                    "subject_external_id",
                    "actor_external_id",
                }
            ),
            tenant_name=tenant_name,
            application_name=app_name,
            subject_external_id=subject_external_id,
            actor_external_id=actor_external_id,
        )
        for source, tenant_name, app_name, subject_external_id, actor_external_id in rows
    ]


def list_sources(
    db: Session,
    scope: Scope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    status: str | None = None,
) -> list[SourceDetail]:
    if subject_id is not None:
        get_subject(db, subject_id, scope)
    return _to_schema(
        db.execute(
            _query(
                scope,
                tenant_id=tenant_id,
                application_id=application_id,
                subject_id=subject_id,
                status=status,
            )
        ).all()
    )


def get_source_detail(
    db: Session, source_id: uuid.UUID, scope: Scope
) -> SourceDetail:
    get_source(db, source_id, scope)
    rows = _to_schema(db.execute(_query(scope, source_id=source_id)).all())
    if not rows:
        raise NotFoundError("Source not found")
    return rows[0]


def update_source(
    db: Session, source_id: uuid.UUID, payload: SourceUpdate, scope: Scope
) -> Source:
    source = get_source(db, source_id, scope)
    if payload.status is not None:
        source.status = payload.status.value
    if payload.filename is not None:
        source.filename = payload.filename
    if payload.mime_type is not None:
        source.mime_type = payload.mime_type
    if payload.storage_uri is not None:
        source.storage_uri = payload.storage_uri
    db.commit()
    db.refresh(source)
    return source


def move_source(
    db: Session,
    source_id: uuid.UUID,
    target_subject_id: uuid.UUID,
    scope: Scope,
) -> Source:
    """Move a file to another subject (folder) in the same application.

    The `storage_uri` is opaque and never changes: moving a file is one row
    update, not a byte copy. The target must be scope-checked and must belong
    to the same application as the source, so the denormalized ownership
    columns stay truthful.
    """
    source = get_source(db, source_id, scope)
    target = get_subject(db, target_subject_id, scope)
    if target.application_id != source.application_id:
        raise ValidationError(
            "target_subject_id does not reference a subject in this application"
        )
    moved = source.subject_id != target.id
    source.subject_id = target.id
    db.commit()
    db.refresh(source)
    if moved:
        graph_sync.move_sources(source.tenant_id, [source.id], target.id)
    return source


def delete_source(
    db: Session, source_id: uuid.UUID, scope: Scope, storage: StorageBackend
) -> None:
    """Delete a source row and any bytes Memora itself stored for it."""
    source = get_source(db, source_id, scope)
    storage_uri, tenant_id = source.storage_uri, source.tenant_id
    db.delete(source)
    db.commit()
    if storage_uri:
        # After the commit: a failed delete should leave a stray object, not a
        # row pointing at bytes that are already gone.
        storage.delete(storage_uri)
    # Same rule for its knowledge-graph facts, which Postgres cannot cascade to.
    graph_sync.forget_sources(tenant_id, [source_id])


def dashboard_counts(db: Session, scope: Scope) -> dict:
    """Aggregate counts for the console dashboard, honouring the caller's scope."""
    from app.db.models import ApiCredential
    from app.schemas.enums import ResourceStatus

    def _count(model, *predicates, join=None):
        """A scalar COUNT subquery; all of them are fetched in one statement."""
        stmt = select(func.count()).select_from(model)
        if join is not None:
            stmt = stmt.join(*join)
        for predicate in predicates:
            stmt = stmt.where(predicate)
        return stmt.scalar_subquery()

    # Every count is constrained by both predicates. For a console user the
    # application half is a no-op `true`, which is what gives them tenant-wide
    # reach; for a credential both halves bite. Nothing is ever unfiltered.
    scoped = [
        scope.tenant_predicate(Source.tenant_id),
        scope.application_predicate(Source.application_id),
    ]

    status_rows = db.execute(
        select(Source.status, func.count())
        .where(*scoped)
        .group_by(Source.status)
    ).all()

    counts = db.execute(
        select(
            _count(Tenant, scope.tenant_predicate(Tenant.id)).label("tenants"),
            _count(
                Application,
                scope.tenant_predicate(Application.tenant_id),
                scope.application_predicate(Application.id),
            ).label("applications"),
            _count(
                Actor,
                scope.tenant_predicate(Actor.tenant_id),
                scope.application_predicate(Actor.application_id),
            ).label("actors"),
            _count(
                Subject,
                scope.tenant_predicate(Subject.tenant_id),
                scope.application_predicate(Subject.application_id),
            ).label("subjects"),
            _count(Source, *scoped).label("sources"),
            # ApiCredential has no tenant_id of its own, so the tenant half of
            # the scope can only be applied through its application.
            _count(
                ApiCredential,
                ApiCredential.status == ResourceStatus.ACTIVE.value,
                scope.tenant_predicate(Application.tenant_id),
                scope.application_predicate(ApiCredential.application_id),
                join=(Application, Application.id == ApiCredential.application_id),
            ).label("active_credentials"),
        )
    ).one()

    return {
        **counts._asdict(),
        "sources_by_status": {status: count for status, count in status_rows},
    }


def dashboard_metrics(db: Session, scope: Scope, days: int = 30) -> dict:
    """Time-bucketed activity for the console dashboard.

    The console needs three things a plain count cannot answer: how fast each
    resource is growing, whether that is faster or slower than the period
    before, and how much storage the sources are consuming. All of it is
    aggregated in PostgreSQL -- the client renders the payload as it arrives
    and never downloads a list to count it.

    `days` is the window; the same span immediately before it is aggregated
    too, so every headline number can carry a delta.
    """
    from datetime import date, timedelta

    window_start = date.today() - timedelta(days=days - 1)
    previous_start = window_start - timedelta(days=days)

    def _series(session: Session, model, predicates) -> dict:
        """Daily points for the window, plus current/previous totals.

        One query buckets both windows (the previous span and the current
        one); the two totals are summed from those buckets, so a series costs
        a single round trip instead of three.
        """
        day = func.date_trunc("day", model.created_at).label("day")
        rows = session.execute(
            select(day, func.count())
            .where(*predicates, model.created_at >= previous_start)
            .group_by(day)
            .order_by(day)
        ).all()
        counted = {row[0].date().isoformat(): row[1] for row in rows}
        window_start_iso = window_start.isoformat()
        current = sum(n for d, n in counted.items() if d >= window_start_iso)
        previous = sum(n for d, n in counted.items() if d < window_start_iso)
        # Zero-fill: a chart with gaps for quiet days reads as missing data
        # rather than as nothing having happened.
        points = [
            {
                "date": (window_start + timedelta(days=offset)).isoformat(),
                "value": counted.get(
                    (window_start + timedelta(days=offset)).isoformat(), 0
                ),
            }
            for offset in range(days)
        ]
        return {"points": points, "current": current, "previous": previous}

    source_scope = [
        scope.tenant_predicate(Source.tenant_id),
        scope.application_predicate(Source.application_id),
    ]
    subject_scope = [
        scope.tenant_predicate(Subject.tenant_id),
        scope.application_predicate(Subject.application_id),
    ]
    actor_scope = [
        scope.tenant_predicate(Actor.tenant_id),
        scope.application_predicate(Actor.application_id),
    ]

    def _types(session: Session) -> list:
        return session.execute(
            select(Source.type, func.count()).where(*source_scope).group_by(Source.type)
        ).all()

    def _totals(session: Session):
        return session.execute(
            select(
                func.coalesce(func.sum(Source.size_bytes), 0),
                func.coalesce(func.max(Source.size_bytes), 0),
                func.max(Source.created_at),
            ).where(*source_scope)
        ).one()

    # The five reads are independent, and each costs a network round trip to the
    # database, so they run side by side on their own connections: the request
    # takes about one round trip instead of five. (Read-only; the caller's
    # session is not used from the worker threads.)
    from concurrent.futures import ThreadPoolExecutor

    from app.db.database import ReadSessionLocal

    def _run(task):
        with ReadSessionLocal() as session:
            return task(session)

    tasks = [
        lambda se: _series(se, Source, source_scope),
        lambda se: _series(se, Subject, subject_scope),
        lambda se: _series(se, Actor, actor_scope),
        _types,
        _totals,
    ]
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        sources_series, subjects_series, actors_series, type_rows, totals = list(
            pool.map(_run, tasks)
        )
    storage_bytes, largest, latest = totals
    storage_bytes = storage_bytes or 0

    return {
        "days": days,
        "sources": sources_series,
        "subjects": subjects_series,
        "actors": actors_series,
        "sources_by_type": {source_type: count for source_type, count in type_rows},
        "storage_bytes": int(storage_bytes),
        "largest_source_bytes": int(largest or 0),
        "last_source_at": latest.isoformat() if latest else None,
    }
