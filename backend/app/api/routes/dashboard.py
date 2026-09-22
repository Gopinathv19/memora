from fastapi import APIRouter, Query

from app.api.deps import CurrentAuth, CurrentScope, DbSession
from app.schemas.dashboard import AuthContextRead, DashboardMetrics, DashboardStats
from app.services import source_service

router = APIRouter(tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
def get_stats(db: DbSession, scope: CurrentScope):
    """Live counts for the console dashboard."""
    return source_service.dashboard_counts(db, scope)


@router.get("/stats/metrics", response_model=DashboardMetrics)
def get_metrics(
    db: DbSession,
    scope: CurrentScope,
    days: int = Query(30, ge=7, le=90, description="Size of the reporting window"),
):
    """Daily activity, storage and type mix for the console dashboard.

    Bucketing happens in PostgreSQL rather than the browser: the console asks
    for a window and renders exactly what comes back, so the payload stays the
    same size whether the tenant has ten sources or ten million.
    """
    return source_service.dashboard_metrics(db, scope, days=days)


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
