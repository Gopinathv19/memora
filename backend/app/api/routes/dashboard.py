from fastapi import APIRouter

from app.api.deps import CurrentAuth, CurrentScope, DbSession
from app.schemas.dashboard import AuthContextRead, DashboardStats
from app.services import source_service

router = APIRouter(tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
def get_stats(db: DbSession, scope: CurrentScope):
    """Live counts for the console dashboard."""
    return source_service.dashboard_counts(db, scope)


@router.get("/whoami", response_model=AuthContextRead)
def whoami(auth: CurrentAuth):
    """What the presented API credential resolves to.

    Useful for confirming a token works, and it demonstrates the full
    token -> credential -> application -> tenant resolution in one response.
    """
    return AuthContextRead(
        credential_id=str(auth.credential.id),
        credential_name=auth.credential.name,
        application_id=str(auth.application.id),
        application_name=auth.application.name,
        application_slug=auth.application.slug,
        tenant_id=str(auth.tenant.id),
        tenant_name=auth.tenant.name,
    )
