import uuid
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.core.errors import NotFoundError, ValidationError
from app.db.models import Actor, Application, Source, Subject, Tenant
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
    if not scope.is_admin:
        stmt = stmt.where(
            Source.tenant_id == scope.tenant_id,
            Source.application_id == scope.application_id,
        )
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


def delete_source(
    db: Session, source_id: uuid.UUID, scope: Scope, storage: StorageBackend
) -> None:
    """Delete a source row and any bytes Memora itself stored for it."""
    source = get_source(db, source_id, scope)
    storage_uri = source.storage_uri
    db.delete(source)
    db.commit()
    if storage_uri:
        # After the commit: a failed delete should leave a stray object, not a
        # row pointing at bytes that are already gone.
        storage.delete(storage_uri)


def dashboard_counts(db: Session, scope: Scope) -> dict:
    """Aggregate counts for the console dashboard, honouring the caller's scope."""
    from app.db.models import ApiCredential
    from app.schemas.enums import ResourceStatus

    def _count(model, *predicates):
        stmt = select(func.count()).select_from(model)
        for predicate in predicates:
            stmt = stmt.where(predicate)
        return db.execute(stmt).scalar_one()

    tenant_filter = [] if scope.is_admin else [Tenant.id == scope.tenant_id]
    app_filter = [] if scope.is_admin else [Application.id == scope.application_id]
    scoped = (
        []
        if scope.is_admin
        else [Source.tenant_id == scope.tenant_id, Source.application_id == scope.application_id]
    )

    status_rows = db.execute(
        select(Source.status, func.count())
        .where(*scoped)
        .group_by(Source.status)
    ).all()

    credential_predicates = [ApiCredential.status == ResourceStatus.ACTIVE.value]
    if not scope.is_admin:
        credential_predicates.append(
            ApiCredential.application_id == scope.application_id
        )

    return {
        "tenants": _count(Tenant, *tenant_filter),
        "applications": _count(Application, *app_filter),
        "actors": _count(
            Actor,
            *(
                []
                if scope.is_admin
                else [Actor.application_id == scope.application_id]
            ),
        ),
        "subjects": _count(
            Subject,
            *(
                []
                if scope.is_admin
                else [Subject.application_id == scope.application_id]
            ),
        ),
        "sources": _count(Source, *scoped),
        "active_credentials": _count(ApiCredential, *credential_predicates),
        "sources_by_status": {status: count for status, count in status_rows},
    }
