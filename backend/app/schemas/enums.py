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


class ExtractionMode(str, Enum):
    """How hard an extraction run tries.

    DEEP sends every PDF page to the layout model regardless of triage -- more
    accurate on awkward documents, and more expensive.
    """

    STANDARD = "standard"
    DEEP = "deep"


class ExtractionStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    # Something was extracted, but at least one page could not be read.
    PARTIAL = "partial"
    FAILED = "failed"


class PageDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ExtractionRoute(str, Enum):
    """How one unit (page, slide, sheet, image) of a document was read."""

    TEXT = "text"      # local text only, no model call
    VISION = "vision"  # local text plus pictures described by the vision model
    LAYOUT = "layout"  # whole page rendered and transcribed by the layout model


class ModelRole(str, Enum):
    LAYOUT = "layout"
    VISION = "vision"
    EXTRACT = "extract"
