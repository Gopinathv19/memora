"""SQLAlchemy models.

Import order matters only for Alembic's autogenerate, which needs every model
registered on `Base.metadata` before it inspects the database.

The ownership chain modelled here is:

    Tenant -> Application -> Actor -> Subject -> Source -> SourceExtraction
                                                          -> SourceGraphBuild
"""

from app.db.models.actor import Actor
from app.db.models.application import Application
from app.db.models.credential import ApiCredential
from app.db.models.extraction import ExtractionUsage, SourceExtraction
from app.db.models.graph import SourceGraphBuild
from app.db.models.source import Source
from app.db.models.subject import Subject
from app.db.models.tenant import Tenant
from app.db.models.users import Users
from app.db.models.auth_user import Authenticated_User

__all__ = [
    "Tenant",
    "Application",
    "ApiCredential",
    "Actor",
    "Subject",
    "Source",
    "SourceExtraction",
    "ExtractionUsage",
    "SourceGraphBuild",
    "Users",
    "Authenticated_User"
]
