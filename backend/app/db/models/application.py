import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.mixins import StatusColumn, TimestampCreated, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.actor import Actor
    from app.db.models.credential import ApiCredential
    from app.db.models.subject import Subject
    from app.db.models.tenant import Tenant


class Application(UUIDPrimaryKey, StatusColumn, TimestampCreated, Base):
    """A consuming application that talks to Memora on a tenant's behalf."""

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_applications_tenant_slug"),
        Index("ix_applications_tenant_id", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="applications")
    credentials: Mapped[list["ApiCredential"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    actors: Mapped[list["Actor"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    subjects: Mapped[list["Subject"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
