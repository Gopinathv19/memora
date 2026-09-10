import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.mixins import StatusColumn, TimestampCreated, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.application import Application
    from app.db.models.subject import Subject
    from app.db.models.tenant import Tenant


class Actor(UUIDPrimaryKey, StatusColumn, TimestampCreated, Base):
    """Whoever or whatever operates on Memora inside an application.

    An actor is *not* a Memora user account. Memora does not own the customer's
    identity system: it stores no email, password, or role. The consuming
    application supplies its own identifier as `external_id` (e.g. `lawyer_123`)
    and Memora maps that to an internal UUID.

    `type` is deliberately broader than "user", because a subject can equally be
    created by a service account, an autonomous agent, or a system process.
    """

    __tablename__ = "actors"
    __table_args__ = (
        UniqueConstraint(
            "application_id", "external_id", name="uq_actors_application_external_id"
        ),
        Index("ix_actors_tenant_id", "tenant_id"),
        Index("ix_actors_application_id", "application_id"),
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
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="user")
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    application: Mapped["Application"] = relationship(back_populates="actors")
    tenant: Mapped["Tenant"] = relationship()
    subjects: Mapped[list["Subject"]] = relationship(back_populates="actor")
