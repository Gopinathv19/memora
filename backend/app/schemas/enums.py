from enum import Enum


class ResourceStatus(str, Enum):
    """Lifecycle status shared by tenants, applications, credentials, actors
    and subjects."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class ActorType(str, Enum):
    """What kind of thing an actor is.

    Broader than "user" so that a workspace opened by a background job or an
    autonomous agent is representable without abusing the user type.
    """

    USER = "user"
    SERVICE = "service"
    AGENT = "agent"
    SYSTEM = "system"


class SourceType(str, Enum):
    FILE = "file"
    CHAT = "chat"
    URL = "url"


class SourceStatus(str, Enum):
    """A source's processing lifecycle.

    Nothing advances a source past PENDING in this MVP -- extraction is the next
    phase. The states exist now so the API contract does not have to change when
    a processor starts moving them.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
