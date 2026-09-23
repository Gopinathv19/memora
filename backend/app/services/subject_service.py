import uuid

from sqlalchemy import func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.models import Actor, Application, Source, Subject, Tenant
from app.schemas.subject import (
    SubjectCreate,
    SubjectPathEntry,
    SubjectUpdate,
    SubjectWithCounts,
)
from app.services.application_service import get_application
from app.services.scope import Scope
from app.storage import StorageBackend


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


def _ancestor_path(db: Session, subject_id: uuid.UUID) -> list[SubjectPathEntry]:
    """The folder chain above a subject, nearest first, ending at the root.

    A recursive CTE rather than a Python loop of `db.get` calls: one round
    trip regardless of depth, and the walk happens where the tree lives.
    """
    ancestors = (
        select(
            Subject.id,
            Subject.external_id,
            Subject.parent_subject_id,
            literal(0).label("depth"),
        )
        .where(Subject.id == subject_id)
        .cte(name="subject_ancestors", recursive=True)
    )
    walk = aliased(Subject, name="ancestor_walk")
    ancestors = ancestors.union_all(
        select(
            walk.id,
            walk.external_id,
            walk.parent_subject_id,
            (ancestors.c.depth + 1).label("depth"),
        ).join(ancestors, walk.id == ancestors.c.parent_subject_id)
    )
    rows = db.execute(
        select(ancestors.c.id, ancestors.c.external_id)
        .where(ancestors.c.id != subject_id)
        .order_by(ancestors.c.depth)
    ).all()
    return [SubjectPathEntry(id=row[0], external_id=row[1]) for row in rows]


def _descendant_ids(db: Session, subject_id: uuid.UUID) -> list[uuid.UUID]:
    """Every subject in the subtree, including the subject itself.

    Used twice: to collect the sources a folder delete must remove, and to
    reject a move that would place a subject inside its own subtree.
    """
    subtree = (
        select(Subject.id)
        .where(Subject.id == subject_id)
        .cte(name="subject_subtree", recursive=True)
    )
    walk = aliased(Subject, name="subtree_walk")
    subtree = subtree.union_all(
        select(walk.id).join(subtree, walk.parent_subject_id == subtree.c.id)
    )
    return db.execute(select(subtree.c.id)).scalars().all()


def create_subject(
    db: Session, application_id: uuid.UUID, payload: SubjectCreate, scope: Scope
) -> Subject:
    application = get_application(db, application_id, scope)
    actor = _resolve_actor(db, payload.actor_id, application)

    parent = None
    if payload.parent_subject_id is not None:
        # The parent is resolved and scope-checked before anything is written,
        # and must live in the same application: a folder tree never spans
        # applications, so the denormalized ownership columns on both ends of
        # the parent link always agree.
        parent = get_subject(db, payload.parent_subject_id, scope)
        if parent.application_id != application.id:
            raise ValidationError(
                "parent_subject_id does not reference a subject in this application"
            )

    subject = Subject(
        tenant_id=application.tenant_id,
        application_id=application.id,
        actor_id=actor.id if actor else None,
        parent_subject_id=parent.id if parent else None,
        external_id=payload.external_id,
        status=payload.status.value,
    )
    db.add(subject)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        location = (
            f"under '{parent.external_id}'" if parent else "at the root"
        )
        raise ConflictError(
            f"A subject with external_id '{payload.external_id}' already exists "
            f"{location} of this application"
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
    parent_subject_id: uuid.UUID | None = None,
    roots_only: bool = False,
):
    source_count = (
        select(func.count())
        .select_from(Source)
        .where(Source.subject_id == Subject.id)
        .scalar_subquery()
    )
    child = aliased(Subject, name="child_subject")
    child_count = (
        select(func.count())
        .select_from(child)
        .where(child.parent_subject_id == Subject.id)
        .scalar_subquery()
    )
    actor_alias = aliased(Actor)
    stmt = (
        select(
            Subject,
            Application.name,
            Tenant.name,
            actor_alias.external_id,
            source_count,
            child_count,
        )
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
    if parent_subject_id is not None:
        stmt = stmt.where(Subject.parent_subject_id == parent_subject_id)
    if roots_only:
        stmt = stmt.where(Subject.parent_subject_id.is_(None))
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
                    "child_count",
                    "path",
                }
            ),
            application_name=app_name,
            tenant_name=tenant_name,
            actor_external_id=actor_external_id,
            source_count=sources,
            child_count=children,
        )
        for subject, app_name, tenant_name, actor_external_id, sources, children in rows
    ]


def list_subjects(
    db: Session,
    scope: Scope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    parent_subject_id: uuid.UUID | None = None,
    roots_only: bool = False,
) -> list[SubjectWithCounts]:
    if application_id is not None:
        get_application(db, application_id, scope)
    if parent_subject_id is not None:
        # A filter on a specific folder is scope-checked like the application
        # filter: the id alone must never be enough to learn what is inside.
        get_subject(db, parent_subject_id, scope)
    return _to_schema(
        db.execute(
            _query(
                scope,
                tenant_id=tenant_id,
                application_id=application_id,
                actor_id=actor_id,
                parent_subject_id=parent_subject_id,
                roots_only=roots_only,
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
    detail = rows[0]
    detail.path = _ancestor_path(db, subject_id)
    return detail


def update_subject(
    db: Session, subject_id: uuid.UUID, payload: SubjectUpdate, scope: Scope
) -> Subject:
    subject = get_subject(db, subject_id, scope)

    if payload.external_id is not None:
        subject.external_id = payload.external_id
    if payload.status is not None:
        subject.status = payload.status.value
    if payload.actor_id is not None:
        actor = _resolve_actor(db, payload.actor_id, subject.application)
        subject.actor_id = actor.id if actor else None

    # A move is distinguished from "leave the parent alone" by presence in the
    # request, not by nullness: an explicit null means "make this a root".
    if "parent_subject_id" in payload.model_fields_set:
        new_parent_id = payload.parent_subject_id
        if new_parent_id is not None:
            if new_parent_id == subject.id:
                raise ValidationError("A subject cannot be its own parent")
            new_parent = get_subject(db, new_parent_id, scope)
            if new_parent.application_id != subject.application_id:
                raise ValidationError(
                    "parent_subject_id does not reference a subject in this application"
                )
            if new_parent_id in _descendant_ids(db, subject.id):
                raise ValidationError(
                    "A subject cannot be moved inside its own subtree"
                )
        subject.parent_subject_id = new_parent_id

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            f"A subject with external_id '{subject.external_id}' already exists "
            "among those siblings"
        ) from exc
    db.refresh(subject)
    return subject


def delete_subject(
    db: Session, subject_id: uuid.UUID, scope: Scope, storage: StorageBackend
) -> None:
    """Delete a subject, its whole subtree, and any bytes Memora stored.

    The subtree is collected with a recursive CTE, the root row is deleted and
    the database's own cascades remove the rest, then -- after the commit --
    the stored objects are removed. That order is the house rule for deletes:
    a failed cleanup leaves a stray object rather than a row pointing at bytes
    that are already gone.
    """
    subject = get_subject(db, subject_id, scope)
    subtree_ids = _descendant_ids(db, subject.id)
    storage_uris = (
        db.execute(
            select(Source.storage_uri).where(
                Source.subject_id.in_(subtree_ids),
                Source.storage_uri.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    db.delete(subject)
    db.commit()
    for storage_uri in storage_uris:
        storage.delete(storage_uri)
