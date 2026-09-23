 

import uuid
from dataclasses import dataclass

from app.core.errors import NotFoundError

from typing import Literal
from collections.abc import Iterable
from sqlalchemy import true , false
@dataclass(frozen=True)
class Scope:

    kind:Literal["user","credential"]
    
    #user_based_sessions
    
    user_id:uuid.UUID | None = None
    tenant_ids:frozenset[uuid.UUID] = frozenset()

    #api_credential
    tenant_id: uuid.UUID | None = None
    application_id: uuid.UUID | None = None
    # Which credential made the call. Attribution only (the cost ledger);
    # it plays no part in any access decision.
    credential_id: uuid.UUID | None = None

    @classmethod
    def for_users(cls,
                  user_id:uuid.UUID,
                  tenants_ids:Iterable[uuid.UUID],
                  )->"Scope":
        return cls(kind="user",user_id=user_id,tenant_ids=frozenset(tenants_ids))
    @classmethod
    def  for_credentials(cls,tenant_id:uuid.UUID,
                         application_id:uuid.UUID,
                         credential_id:uuid.UUID | None = None)-> "Scope":

        return cls(kind="credential",tenant_id=tenant_id,application_id=application_id,
                   credential_id=credential_id)

    @property
    def is_console(self)->bool:
        return self.kind=="user"

    def allow_tenant(self,tenant_id:uuid.UUID)->bool:
        if self.is_console:
            return tenant_id in self.tenant_ids
        return tenant_id == self.tenant_id

    def allow_application(self,application_id:uuid.UUID)->bool:
        if self.is_console:
            return True
        return self.application_id == application_id

    def assert_owns(self,
                    resource:str,
                    tenant_id:uuid.UUID,
                    application_id:uuid.UUID | None = None)-> None:

        if not self.allow_tenant(tenant_id=tenant_id):
            raise NotFoundError(f"{resource} not found")
        if application_id is not None and not self.allow_application(application_id=application_id):
            raise NotFoundError(f"{resource} not found")


    def tenant_predicate(self,column):
        if self.is_console:
            if not self.tenant_ids:
                return false()
            return column.in_(list(self.tenant_ids))

        return column == self.tenant_id

    def application_predicate(self,column):
        if self.is_console:
            return true()

        return column == self.application_id


    

    
    

