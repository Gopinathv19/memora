"""The ownership scope a request is allowed to act within.

Every service read/write takes a `Scope`. This is the single mechanism that
makes "a source must never be accessible merely because its id is known" true:
services load a row by id and then assert the row's owners match the scope,
returning 404 rather than 403 when they do not, so an id in another tenant is
indistinguishable from an id that does not exist.

Two kinds of scope exist:

* `Scope.admin()` -- a console/management request. Unrestricted.
* `Scope.from_credential(...)` -- an API-credential request. Pinned to the
  tenant and application the credential belongs to. These values come from the
  credential row, never from the client, which is why a caller cannot widen its
  own scope by passing different ids.
"""

import uuid
from dataclasses import dataclass

from app.core.errors import NotFoundError


@dataclass(frozen=True)
class Scope:
    tenant_id: uuid.UUID | None = None
    application_id: uuid.UUID | None = None
    is_admin: bool = False

    @classmethod
    def admin(cls) -> "Scope":
        return cls(is_admin=True)

    @classmethod
    def from_credential(
        cls, tenant_id: uuid.UUID, application_id: uuid.UUID
    ) -> "Scope":
        return cls(tenant_id=tenant_id, application_id=application_id)

    def allows_tenant(self, tenant_id: uuid.UUID) -> bool:
        return self.is_admin or self.tenant_id == tenant_id

    def allows_application(self, application_id: uuid.UUID) -> bool:
        return self.is_admin or self.application_id == application_id

    def assert_owns(
        self,
        resource: str,
        tenant_id: uuid.UUID,
        application_id: uuid.UUID | None = None,
    ) -> None:
        """Raise NotFoundError unless this scope covers the given owners.

        404 rather than 403 is intentional: a 403 would confirm that the id
        exists in some other tenant, which is itself a leak.
        """
        if not self.allows_tenant(tenant_id):
            raise NotFoundError(f"{resource} not found")
        if application_id is not None and not self.allows_application(application_id):
            raise NotFoundError(f"{resource} not found")
