import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentScope, DbSession
from app.schemas.application import (
    ApplicationCreate,
    ApplicationRead,
    ApplicationUpdate,
    ApplicationWithCounts,
)
from app.services import application_service

# Nested routes: an application only exists inside a tenant, so creating one
# names its tenant in the path rather than in the body.
tenant_router = APIRouter(prefix="/tenants/{tenant_id}/applications", tags=["applications"])
router = APIRouter(prefix="/applications", tags=["applications"])


@tenant_router.post("", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def create_application(
    tenant_id: uuid.UUID,
    payload: ApplicationCreate,
    db: DbSession,
    scope: CurrentScope,
):
    return application_service.create_application(db, tenant_id, payload, scope)


@tenant_router.get("", response_model=list[ApplicationWithCounts])
def list_tenant_applications(tenant_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    return application_service.list_applications(db, scope, tenant_id=tenant_id)


@router.get("", response_model=list[ApplicationWithCounts])
def list_applications(
    db: DbSession, scope: CurrentScope, tenant_id: uuid.UUID | None = None
):
    """Flat list across tenants, for the console's Applications page."""
    return application_service.list_applications(db, scope, tenant_id=tenant_id)


@router.get("/{application_id}", response_model=ApplicationWithCounts)
def get_application(application_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    return application_service.get_application_detail(db, application_id, scope)


@router.patch("/{application_id}", response_model=ApplicationRead)
def update_application(
    application_id: uuid.UUID,
    payload: ApplicationUpdate,
    db: DbSession,
    scope: CurrentScope,
):
    return application_service.update_application(db, application_id, payload, scope)
