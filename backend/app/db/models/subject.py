import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
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
    """

    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "external_id",
            name="uq_subjects_application_external_id",
        ),
        Index("ix_subjects_tenant_id", "tenant_id"),
        Index("ix_subjects_application_id", "application_id"),
        Index("ix_subjects_actor_id", "actor_id"),
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

    tenant: Mapped["Tenant"] = relationship()
    application: Mapped["Application"] = relationship(back_populates="subjects")
    actor: Mapped["Actor | None"] = relationship(back_populates="subjects")
    sources: Mapped[list["Source"]] = relationship(
        back_populates="subject",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
