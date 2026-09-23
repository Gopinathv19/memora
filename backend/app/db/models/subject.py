import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.mixins import StatusColumn, TimestampCreated, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.actor import Actor
    from app.db.models.application import Application
    from app.db.models.source import Source
    from app.db.models.tenant import Tenant


class Subject(UUIDPrimaryKey, StatusColumn, TimestampCreated, Base):
    """A workspace: the boundary that knowledge is scoped to.

    Naming: `id` is always Memora's internal UUID and `external_id` is always
    the application's own identifier for the workspace (`case-ABC-456`,
    `customer_123`). The two are never given the same column name -- conflating
    "Memora's subject id" with "the customer's workspace id" is exactly the
    confusion this split exists to prevent.

    `actor_id` records the *originating* actor -- who caused the workspace to
    exist. It is an audit fact, not an access rule: several actors may later
    work inside the same subject. Per-actor authorization, if it is ever needed,
    belongs in a separate `subject_actors` join table and not here.

    A subject may also be a *folder* inside another subject: `parent_subject_id`
    points at the containing subject and is NULL for a root. The tree is an
    adjacency list, so nesting is unbounded. `external_id` is unique among
    siblings (two partial unique indexes, because PostgreSQL treats NULLs as
    distinct), which is what makes `external_id` usable as a folder name.
    Deleting a parent cascades to the whole subtree, and the existing
    subjects -> sources cascade removes their rows with it.
    """

    __tablename__ = "subjects"
    __table_args__ = (
        # Roots: unique per application. Every pre-existing subject is a root,
        # so this is exactly as strict as the constraint it replaced.
        Index(
            "uq_subjects_root_external_id",
            "application_id",
            "external_id",
            unique=True,
            postgresql_where=text("parent_subject_id IS NULL"),
        ),
        # Children: unique among siblings of the same parent.
        Index(
            "uq_subjects_sibling_external_id",
            "application_id",
            "parent_subject_id",
            "external_id",
            unique=True,
            postgresql_where=text("parent_subject_id IS NOT NULL"),
        ),
        Index("ix_subjects_tenant_id", "tenant_id"),
        Index("ix_subjects_application_id", "application_id"),
        Index("ix_subjects_actor_id", "actor_id"),
        Index("ix_subjects_parent_subject_id", "parent_subject_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Nullable so a subject created by a back-end process with no meaningful
    # originating actor is still representable. RESTRICT, not CASCADE: deleting
    # an actor must not silently delete the workspaces they opened.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
        nullable=True,
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # NULL for a root subject; otherwise the containing subject ("folder").
    parent_subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=True,
    )

    tenant: Mapped["Tenant"] = relationship()
    application: Mapped["Application"] = relationship(back_populates="subjects")
    actor: Mapped["Actor | None"] = relationship(back_populates="subjects")
    sources: Mapped[list["Source"]] = relationship(
        back_populates="subject",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # The folder tree. `remote_side` is what tells SQLAlchemy this is a
    # self-referential many-to-one, and cascade mirrors the database's own
    # ON DELETE CASCADE so an ORM-level delete behaves the same way.
    parent_subject: Mapped["Subject | None"] = relationship(
        back_populates="child_subjects",
        remote_side="Subject.id",
        foreign_keys=[parent_subject_id],
    )
    child_subjects: Mapped[list["Subject"]] = relationship(
        back_populates="parent_subject",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
