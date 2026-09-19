import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.db.models import Actor, ApiCredential, Application, Source, Subject, Tenant
from app.schemas.application import (
    ApplicationCreate,
    ApplicationUpdate,
    ApplicationWithCounts,
)
from app.services.scope import Scope
from app.services.tenant_service import get_tenant


def create_application(
    db: Session, tenant_id: uuid.UUID, payload: ApplicationCreate, scope: Scope
) -> Application:
    # Resolving the tenant first both validates the FK and enforces scope,
    # so a caller cannot create an application under someone else's tenant.
    tenant = get_tenant(db, tenant_id, scope)
    application = Application(
        tenant_id=tenant.id,
        name=payload.name,
        slug=payload.slug,
        status=payload.status.value,
    )
    db.add(application)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            f"An application with slug '{payload.slug}' already exists in this tenant"
        ) from exc
    db.refresh(application)
    return application


def get_application(
    db: Session, application_id: uuid.UUID, scope: Scope
) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise NotFoundError("Application not found")
    scope.assert_owns("Application", application.tenant_id, application.id)
    return application


def _counts_query(
    scope: Scope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
):
    credential_count = (
        select(func.count())
        .select_from(ApiCredential)
        .where(ApiCredential.application_id == Application.id)
        .scalar_subquery()
    )
    actor_count = (
        select(func.count())
        .select_from(Actor)
        .where(Actor.application_id == Application.id)
        .scalar_subquery()
    )
    subject_count = (
        select(func.count())
        .select_from(Subject)
        .where(Subject.application_id == Application.id)
        .scalar_subquery()
    )
    source_count = (
        select(func.count())
        .select_from(Source)
        .where(Source.application_id == Application.id)
        .scalar_subquery()
    )
    stmt = (
        select(
            Application,
            Tenant.name,
            credential_count,
            actor_count,
            subject_count,
            source_count,
        )
        .join(Tenant, Tenant.id == Application.tenant_id)
        .where(
            scope.tenant_predicate(Application.tenant_id),
            scope.application_predicate(Application.id),
        )
        .order_by(Application.created_at.desc())
    )
    if tenant_id is not None:
        stmt = stmt.where(Application.tenant_id == tenant_id)
    if application_id is not None:
        stmt = stmt.where(Application.id == application_id)
    return stmt


def list_applications(
    db: Session, scope: Scope, tenant_id: uuid.UUID | None = None
) -> list[ApplicationWithCounts]:
    if tenant_id is not None:
        get_tenant(db, tenant_id, scope)  # 404 for an out-of-scope tenant.

    rows = db.execute(_counts_query(scope, tenant_id=tenant_id)).all()
    return _to_schema(rows)


def _to_schema(rows) -> list[ApplicationWithCounts]:
    return [
        ApplicationWithCounts(
            **ApplicationWithCounts.model_validate(app).model_dump(
                exclude={
                    "tenant_name",
                    "credential_count",
                    "actor_count",
                    "subject_count",
                    "source_count",
                }
            ),
            tenant_name=tenant_name,
            credential_count=creds,
            actor_count=actors,
            subject_count=subjects,
            source_count=sources,
        )
        for app, tenant_name, creds, actors, subjects, sources in rows
    ]


def get_application_detail(
    db: Session, application_id: uuid.UUID, scope: Scope
) -> ApplicationWithCounts:
    # get_application first, so an out-of-scope id 404s consistently with
    # every other read rather than returning an empty result.
    get_application(db, application_id, scope)
    rows = _to_schema(
        db.execute(_counts_query(scope, application_id=application_id)).all()
    )
    if not rows:
        raise NotFoundError("Application not found")
    return rows[0]


def update_application(
    db: Session, application_id: uuid.UUID, payload: ApplicationUpdate, scope: Scope
) -> Application:
    application = get_application(db, application_id, scope)
    if payload.name is not None:
        application.name = payload.name
    if payload.slug is not None:
        application.slug = payload.slug
    if payload.status is not None:
        application.status = payload.status.value
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            f"An application with slug '{payload.slug}' already exists in this tenant"
        ) from exc
    db.refresh(application)
    return application
