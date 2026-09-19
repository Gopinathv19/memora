import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentScope, DbSession
from app.schemas.tenant import (
    TenantCreate,
    TenantRead,
    TenantUpdate,
    TenantWithCounts,
)
from app.services import tenant_service
from app.core.errors import PermissionError_
router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantRead, status_code=status.HTTP_201_CREATED)
def create_tenant(payload: TenantCreate, db: DbSession, scope: CurrentScope):
    if not scope.is_console:
        raise PermissionError_("API credentials cannot create tenants")
    return tenant_service.create_tenant(db, payload,user_owner_id=scope.user_id)


@router.get("", response_model=list[TenantWithCounts])
def list_tenants(db: DbSession, scope: CurrentScope):
    return tenant_service.list_tenants(db, scope)


@router.get("/{tenant_id}", response_model=TenantRead)
def get_tenant(tenant_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    return tenant_service.get_tenant(db, tenant_id, scope)


@router.patch("/{tenant_id}", response_model=TenantRead)
def update_tenant(
    tenant_id: uuid.UUID, payload: TenantUpdate, db: DbSession, scope: CurrentScope
):
    return tenant_service.update_tenant(db, tenant_id, payload, scope)
