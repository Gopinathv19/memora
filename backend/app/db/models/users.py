from app.db.database import Base
from app.db.models.mixins import StatusColumn,TimestampCreated,UUIDPrimaryKey
from sqlalchemy import Boolean,String
from sqlalchemy.orm import Mapped , mapped_column

class Users(UUIDPrimaryKey,StatusColumn,TimestampCreated,Base):
    __tablename__="users"
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")

    avatar_url: Mapped[str] = mapped_column(String(1024), nullable=False, server_default="")

    


