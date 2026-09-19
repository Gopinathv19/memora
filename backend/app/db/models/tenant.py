from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import UUID,ForeignKey
from app.db.database import Base
from app.db.models.mixins import StatusColumn, TimestampCreated, UUIDPrimaryKey

import uuid
if TYPE_CHECKING:
    from app.db.models.application import Application


class Tenant(UUIDPrimaryKey, StatusColumn, TimestampCreated, Base):
    """An organization / customer that owns data inside Memora."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_owner_id:Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),ForeignKey("users.id",ondelete="CASCADE"),nullable=False)
    applications: Mapped[list["Application"]] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
