import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.db.models import Actor, Application, Subject
from app.schemas.actor import ActorCreate, ActorUpdate, ActorWithCounts
from app.services.application_service import get_application
from app.services.scope import Scope


def create_actor(
    db: Session, application_id: uuid.UUID, payload: ActorCreate, scope: Scope
) -> Actor:
    application = get_application(db, application_id, scope)
    actor = Actor(
        # tenant_id is copied from the application, never taken from the client.
        tenant_id=application.tenant_id,
        application_id=application.id,
        external_id=payload.external_id,
        type=payload.type.value,
        name=payload.name,
        status=payload.status.value,
    )
    db.add(actor)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            f"An actor with external_id '{payload.external_id}' already exists "
            "in this application"
        ) from exc
    db.refresh(actor)
    return actor


def get_actor(db: Session, actor_id: uuid.UUID, scope: Scope) -> Actor:
    actor = db.get(Actor, actor_id)
    if actor is None:
        raise NotFoundError("Actor not found")
    scope.assert_owns("Actor", actor.tenant_id, actor.application_id)
    return actor


def _query(
    scope: Scope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
):
    subject_count = (
        select(func.count())
        .select_from(Subject)
        .where(Subject.actor_id == Actor.id)
        .scalar_subquery()
    )
    stmt = (
        select(Actor, Application.name, subject_count)
        .join(Application, Application.id == Actor.application_id)
        .where(
            scope.tenant_predicate(Actor.tenant_id),
            scope.application_predicate(Actor.application_id),
        )
        .order_by(Actor.created_at.desc())
    )
    if tenant_id is not None:
        stmt = stmt.where(Actor.tenant_id == tenant_id)
    if application_id is not None:
        stmt = stmt.where(Actor.application_id == application_id)
    if actor_id is not None:
        stmt = stmt.where(Actor.id == actor_id)
    return stmt


def _to_schema(rows) -> list[ActorWithCounts]:
    return [
        ActorWithCounts(
            **ActorWithCounts.model_validate(actor).model_dump(
                exclude={"application_name", "subject_count"}
            ),
            application_name=app_name,
            subject_count=subjects,
        )
        for actor, app_name, subjects in rows
    ]


def list_actors(
    db: Session,
    scope: Scope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
) -> list[ActorWithCounts]:
    if application_id is not None:
        get_application(db, application_id, scope)
    return _to_schema(
        db.execute(
            _query(scope, tenant_id=tenant_id, application_id=application_id)
        ).all()
    )


def get_actor_detail(
    db: Session, actor_id: uuid.UUID, scope: Scope
) -> ActorWithCounts:
    get_actor(db, actor_id, scope)
    rows = _to_schema(db.execute(_query(scope, actor_id=actor_id)).all())
    if not rows:
        raise NotFoundError("Actor not found")
    return rows[0]


def update_actor(
    db: Session, actor_id: uuid.UUID, payload: ActorUpdate, scope: Scope
) -> Actor:
    actor = get_actor(db, actor_id, scope)
    if payload.name is not None:
        actor.name = payload.name
    if payload.type is not None:
        actor.type = payload.type.value
    if payload.status is not None:
        actor.status = payload.status.value
    db.commit()
    db.refresh(actor)
    return actor
