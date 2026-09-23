"""Domain errors, translated into HTTP responses by handlers in main.py.

Keeping services free of `HTTPException` means the service layer stays usable
from a worker, a CLI, or a test without importing FastAPI.
"""


class MemoraError(Exception):
    """Base class for expected, client-visible failures."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(MemoraError):
    status_code = 404
    code = "not_found"


class ConflictError(MemoraError):
    """A uniqueness rule was violated, e.g. a duplicate slug within a tenant."""

    status_code = 409
    code = "conflict"


class ValidationError(MemoraError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(MemoraError):
    status_code = 401
    code = "unauthenticated"


class PermissionError_(MemoraError):
    status_code = 403
    code = "forbidden"
