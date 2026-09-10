import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentScope, DbSession
from app.schemas.actor import ActorCreate, ActorRead, ActorUpdate, ActorWithCounts
from app.services import actor_service

application_router = APIRouter(
    prefix="/applications/{application_id}/actors", tags=["actors"]
)
router = APIRouter(prefix="/actors", tags=["actors"])


@application_router.post("", response_model=ActorRead, status_code=status.HTTP_201_CREATED)
def create_actor(
    application_id: uuid.UUID, payload: ActorCreate, db: DbSession, scope: CurrentScope
):
    return actor_service.create_actor(db, application_id, payload, scope)


@application_router.get("", response_model=list[ActorWithCounts])
def list_application_actors(
    application_id: uuid.UUID, db: DbSession, scope: CurrentScope
):
    return actor_service.list_actors(db, scope, application_id=application_id)


@router.get("", response_model=list[ActorWithCounts])
def list_actors(
    db: DbSession,
    scope: CurrentScope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
):
    return actor_service.list_actors(
        db, scope, tenant_id=tenant_id, application_id=application_id
    )


@router.get("/{actor_id}", response_model=ActorWithCounts)
def get_actor(actor_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    return actor_service.get_actor_detail(db, actor_id, scope)


@router.patch("/{actor_id}", response_model=ActorRead)
def update_actor(
    actor_id: uuid.UUID, payload: ActorUpdate, db: DbSession, scope: CurrentScope
):
    return actor_service.update_actor(db, actor_id, payload, scope)
