from pydantic import BaseModel


class DashboardStats(BaseModel):
    """Live counts for the console dashboard. Never hard-coded on the client."""

    tenants: int
    applications: int
    actors: int
    subjects: int
    sources: int
    active_credentials: int
    sources_by_status: dict[str, int]


class AuthContextRead(BaseModel):
    """What an API credential resolves to. Returned by GET /api/v1/whoami."""

    credential_id: str
    credential_name: str
    application_id: str
    application_name: str
    application_slug: str
    tenant_id: str
    tenant_name: str
