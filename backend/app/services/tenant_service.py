import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models import Application, Source, Subject, Tenant
from app.schemas.tenant import TenantCreate, TenantUpdate, TenantWithCounts
from app.services.scope import Scope


def create_tenant(db: Session, payload: TenantCreate) -> Tenant:
    tenant = Tenant(name=payload.name, status=payload.status.value)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def get_tenant(db: Session, tenant_id: uuid.UUID, scope: Scope) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")
    scope.assert_owns("Tenant", tenant.id)
    return tenant


def list_tenants(db: Session, scope: Scope) -> list[TenantWithCounts]:
    """List tenants with their aggregate counts.

    The counts come from correlated scalar subqueries rather than joins so a
    tenant with many applications is not multiplied across rows, and so a
    tenant with nothing attached still appears with zeroes.
    """
    app_count = (
        select(func.count())
        .select_from(Application)
        .where(Application.tenant_id == Tenant.id)
        .scalar_subquery()
    )
    subject_count = (
        select(func.count())
        .select_from(Subject)
        .where(Subject.tenant_id == Tenant.id)
        .scalar_subquery()
    )
    source_count = (
        select(func.count())
        .select_from(Source)
        .where(Source.tenant_id == Tenant.id)
        .scalar_subquery()
    )

    stmt = select(Tenant, app_count, subject_count, source_count).order_by(
        Tenant.created_at.desc()
    )
    if not scope.is_admin:
        stmt = stmt.where(Tenant.id == scope.tenant_id)

    return [
        TenantWithCounts(
            **TenantWithCounts.model_validate(tenant).model_dump(
                exclude={"application_count", "subject_count", "source_count"}
            ),
            application_count=apps,
            subject_count=subjects,
            source_count=sources,
        )
        for tenant, apps, subjects, sources in db.execute(stmt).all()
    ]


def update_tenant(
    db: Session, tenant_id: uuid.UUID, payload: TenantUpdate, scope: Scope
) -> Tenant:
    tenant = get_tenant(db, tenant_id, scope)
    if payload.name is not None:
        tenant.name = payload.name
    if payload.status is not None:
        tenant.status = payload.status.value
    db.commit()
    db.refresh(tenant)
    return tenant
