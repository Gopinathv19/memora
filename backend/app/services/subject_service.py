import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.models import Actor, Application, Source, Subject, Tenant
from app.schemas.subject import SubjectCreate, SubjectUpdate, SubjectWithCounts
from app.services.application_service import get_application
from app.services.scope import Scope


def _resolve_actor(
    db: Session, actor_id: uuid.UUID | None, application: Application
) -> Actor | None:
    """Validate that an actor, if given, belongs to this same application.

    Without this check a caller could attach a subject to an actor from another
    application -- a cross-tenant reference the foreign key alone would happily
    accept, since the FK only proves the actor exists.
    """
    if actor_id is None:
        return None
    actor = db.get(Actor, actor_id)
    if actor is None or actor.application_id != application.id:
        raise ValidationError(
            "actor_id does not reference an actor in this application"
        )
    return actor


def create_subject(
    db: Session, application_id: uuid.UUID, payload: SubjectCreate, scope: Scope
) -> Subject:
    application = get_application(db, application_id, scope)
    actor = _resolve_actor(db, payload.actor_id, application)

    subject = Subject(
        tenant_id=application.tenant_id,
        application_id=application.id,
        actor_id=actor.id if actor else None,
        external_id=payload.external_id,
        status=payload.status.value,
    )
    db.add(subject)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            f"A subject with external_id '{payload.external_id}' already exists "
            "in this application"
        ) from exc
    db.refresh(subject)
    return subject


def get_subject(db: Session, subject_id: uuid.UUID, scope: Scope) -> Subject:
    subject = db.get(Subject, subject_id)
    if subject is None:
        raise NotFoundError("Subject not found")
    scope.assert_owns("Subject", subject.tenant_id, subject.application_id)
    return subject


def _query(
    scope: Scope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
):
    source_count = (
        select(func.count())
        .select_from(Source)
        .where(Source.subject_id == Subject.id)
        .scalar_subquery()
    )
    actor_alias = aliased(Actor)
    stmt = (
        select(Subject, Application.name, Tenant.name, actor_alias.external_id, source_count)
        .join(Application, Application.id == Subject.application_id)
        .join(Tenant, Tenant.id == Subject.tenant_id)
        .outerjoin(actor_alias, actor_alias.id == Subject.actor_id)
        .where(
            scope.tenant_predicate(Subject.tenant_id),
            scope.application_predicate(Subject.application_id),
        )
        .order_by(Subject.created_at.desc())
    )
    if tenant_id is not None:
        stmt = stmt.where(Subject.tenant_id == tenant_id)
    if application_id is not None:
        stmt = stmt.where(Subject.application_id == application_id)
    if actor_id is not None:
        stmt = stmt.where(Subject.actor_id == actor_id)
    if subject_id is not None:
        stmt = stmt.where(Subject.id == subject_id)
    return stmt


def _to_schema(rows) -> list[SubjectWithCounts]:
    return [
        SubjectWithCounts(
            **SubjectWithCounts.model_validate(subject).model_dump(
                exclude={
                    "application_name",
                    "tenant_name",
                    "actor_external_id",
                    "source_count",
                }
            ),
            application_name=app_name,
            tenant_name=tenant_name,
            actor_external_id=actor_external_id,
            source_count=sources,
        )
        for subject, app_name, tenant_name, actor_external_id, sources in rows
    ]


def list_subjects(
    db: Session,
    scope: Scope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> list[SubjectWithCounts]:
    if application_id is not None:
        get_application(db, application_id, scope)
    return _to_schema(
        db.execute(
            _query(
                scope,
                tenant_id=tenant_id,
                application_id=application_id,
                actor_id=actor_id,
            )
        ).all()
    )


def get_subject_detail(
    db: Session, subject_id: uuid.UUID, scope: Scope
) -> SubjectWithCounts:
    get_subject(db, subject_id, scope)
    rows = _to_schema(db.execute(_query(scope, subject_id=subject_id)).all())
    if not rows:
        raise NotFoundError("Subject not found")
    return rows[0]


def update_subject(
    db: Session, subject_id: uuid.UUID, payload: SubjectUpdate, scope: Scope
) -> Subject:
    subject = get_subject(db, subject_id, scope)
    if payload.status is not None:
        subject.status = payload.status.value
    if payload.actor_id is not None:
        actor = _resolve_actor(db, payload.actor_id, subject.application)
        subject.actor_id = actor.id if actor else None
    db.commit()
    db.refresh(subject)
    return subject
