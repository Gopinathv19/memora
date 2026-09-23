import uuid
from datetime import datetime

from fastapi import APIRouter, Query

from app.api.deps import CurrentScope, DbSession
from app.schemas.extraction import UsageReport
from app.services import extraction_service

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/extractions", response_model=UsageReport)
def extraction_usage(
    db: DbSession,
    scope: CurrentScope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
):
    """Model calls, tokens and cost of extraction runs inside the caller's scope,
    grouped by application, by model and by who triggered them."""
    return extraction_service.usage_report(
        db, scope, tenant_id, application_id, date_from, date_to
    )
