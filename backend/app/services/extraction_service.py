"""Extraction runs: start a version, run it, read it back, report its cost.

The pipeline is Source -> DocumentProcessor -> ExtractionAgent -> this module,
which is the only part that writes to the database. A run is started inside a
request (`start_extraction`, which scope-checks and claims the next version)
and executed afterwards in a FastAPI background task (`run_extraction`, which
opens its own session because the request's is gone by then).
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.agents.extraction_agent import ExtractionAgent, ExtractionFailed, UsageRecord
from app.core.config import get_settings
from app.core.errors import ConflictError, MemoraError, NotFoundError, ValidationError
from app.db.database import SessionLocal
from app.db.models import (
    Actor,
    ApiCredential,
    Application,
    ExtractionUsage,
    Source,
    SourceExtraction,
    Users,
)
from app.processing import DocumentProcessor, UnsupportedDocumentError
from app.schemas.enums import ExtractionMode, ExtractionStatus, SourceStatus
from app.schemas.extraction import (
    ExtractionRequest,
    UsageGroup,
    UsageReport,
    UsageTotals,
)
from app.services.scope import Scope
from app.services.source_service import get_source
from app.storage import StorageBackend

log = logging.getLogger(__name__)


def _triggered_by(scope: Scope) -> dict:
    if scope.is_console:
        return {"triggered_by_kind": "user", "triggered_by_user_id": scope.user_id}
    return {
        "triggered_by_kind": "credential",
        "triggered_by_credential_id": scope.credential_id,
    }


def start_extraction(
    db: Session, source_id: uuid.UUID, request: ExtractionRequest, scope: Scope
) -> SourceExtraction:
    """Claim the next version for a source and mark both as processing.

    The source row is locked while the version is chosen, so two concurrent
    requests cannot both see "nothing running" and start overlapping runs.
    """
    get_source(db, source_id, scope)  # scope check first; 404 outside it
    source = db.execute(
        select(Source).where(Source.id == source_id).with_for_update()
    ).scalar_one()

    if not source.storage_uri:
        raise ValidationError("This source has no stored content to extract")
    if request.actor_id is not None:
        actor = db.get(Actor, request.actor_id)
        if actor is None or actor.application_id != source.application_id:
            raise ValidationError("actor_id does not reference an actor in this application")

    running = db.execute(
        select(func.count())
        .select_from(SourceExtraction)
        .where(
            SourceExtraction.source_id == source.id,
            SourceExtraction.status == ExtractionStatus.PROCESSING.value,
        )
    ).scalar_one()
    if running:
        raise ConflictError("An extraction is already running for this source")

    latest = db.execute(
        select(func.max(SourceExtraction.version)).where(SourceExtraction.source_id == source.id)
    ).scalar_one()

    settings = get_settings()
    extraction = SourceExtraction(
        source_id=source.id,
        tenant_id=source.tenant_id,
        application_id=source.application_id,
        version=(latest or 0) + 1,
        status=ExtractionStatus.PROCESSING.value,
        mode=request.mode.value,
        instructions=request.instructions,
        provider=settings.llm_provider,
        models=settings.llm_models,
        actor_id=request.actor_id,
        **_triggered_by(scope),
    )
    source.status = SourceStatus.PROCESSING.value
    db.add(extraction)
    db.commit()
    db.refresh(extraction)
    return extraction


def _cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    if provider != "nebius":
        return 0.0  # build.nvidia.com is free
    price = get_settings().llm_prices.get(model) or {}
    return round(
        prompt_tokens * price.get("input", 0.0) / 1_000_000
        + completion_tokens * price.get("output", 0.0) / 1_000_000,
        6,
    )


def _record_usage(db: Session, extraction: SourceExtraction, records: list[UsageRecord]) -> None:
    for record in records:
        cost = _cost(
            extraction.provider, record.model, record.prompt_tokens, record.completion_tokens
        )
        db.add(
            ExtractionUsage(
                extraction_id=extraction.id,
                source_id=extraction.source_id,
                tenant_id=extraction.tenant_id,
                application_id=extraction.application_id,
                triggered_by_kind=extraction.triggered_by_kind,
                triggered_by_user_id=extraction.triggered_by_user_id,
                triggered_by_credential_id=extraction.triggered_by_credential_id,
                actor_id=extraction.actor_id,
                provider=extraction.provider,
                model=record.model,
                role=record.role.value,
                page=record.page,
                prompt_tokens=record.prompt_tokens,
                completion_tokens=record.completion_tokens,
                cost_usd=cost,
                latency_ms=record.latency_ms,
                status=record.status,
                error=record.error,
            )
        )
        extraction.prompt_tokens += record.prompt_tokens
        extraction.completion_tokens += record.completion_tokens
        extraction.cost_usd = round(extraction.cost_usd + cost, 6)


def run_extraction(
    extraction_id: uuid.UUID,
    storage: StorageBackend,
    agent: ExtractionAgent,
    processor: DocumentProcessor | None = None,
) -> None:
    """Execute one claimed run. Never raises, never leaves it `processing`."""
    db = SessionLocal()
    try:
        extraction = db.get(SourceExtraction, extraction_id)
        if extraction is None:
            return
        source = db.get(Source, extraction.source_id)
        if source is None:
            return
        usage: list[UsageRecord] = []
        try:
            with storage.open(source.storage_uri) as stream:
                data = stream.read()
            document = (processor or DocumentProcessor()).process(
                data, source.mime_type, source.filename, ExtractionMode(extraction.mode)
            )
            output = agent.extract(
                document, source_id=source.id, instructions=extraction.instructions
            )
            usage = output.usage
            extraction.result = output.result.model_dump(mode="json")
            extraction.status = output.result.status.value
            source.status = SourceStatus.COMPLETED.value
        except (ExtractionFailed, UnsupportedDocumentError, MemoraError) as exc:
            usage = getattr(exc, "usage", [])
            extraction.status = ExtractionStatus.FAILED.value
            extraction.error = getattr(exc, "message", None) or str(exc)
            source.status = SourceStatus.FAILED.value
        except Exception as exc:  # anything unexpected still ends the run cleanly
            log.exception("extraction %s crashed", extraction_id)
            extraction.status = ExtractionStatus.FAILED.value
            extraction.error = f"Internal error: {type(exc).__name__}: {exc}"[:2000]
            source.status = SourceStatus.FAILED.value
        _record_usage(db, extraction, usage)
        extraction.finished_at = datetime.now(UTC)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("could not record the outcome of extraction %s", extraction_id)
        _mark_failed(extraction_id, "Internal error while saving the result")
    finally:
        db.close()


def _mark_failed(extraction_id: uuid.UUID, message: str) -> None:
    db = SessionLocal()
    try:
        extraction = db.get(SourceExtraction, extraction_id)
        if extraction is None:
            return
        extraction.status = ExtractionStatus.FAILED.value
        extraction.error = message
        extraction.finished_at = datetime.now(UTC)
        source = db.get(Source, extraction.source_id)
        if source is not None:
            source.status = SourceStatus.FAILED.value
        db.commit()
    finally:
        db.close()


def fail_interrupted_runs(db: Session) -> int:
    """At startup: a run still `processing` belonged to a process that died.

    Background tasks live in the server process, so after a restart nothing
    will ever finish them. Marking them failed lets the user simply re-extract.
    """
    runs = db.execute(
        select(SourceExtraction).where(
            SourceExtraction.status == ExtractionStatus.PROCESSING.value
        )
    ).scalars().all()
    for extraction in runs:
        extraction.status = ExtractionStatus.FAILED.value
        extraction.error = "Interrupted: the server restarted during this run"
        extraction.finished_at = datetime.now(UTC)
        source = db.get(Source, extraction.source_id)
        if source is not None and source.status == SourceStatus.PROCESSING.value:
            source.status = SourceStatus.FAILED.value
    db.commit()
    return len(runs)


# --- reading ----------------------------------------------------------------------


def list_extractions(db: Session, source_id: uuid.UUID, scope: Scope) -> list[SourceExtraction]:
    get_source(db, source_id, scope)
    return list(
        db.execute(
            select(SourceExtraction)
            .where(SourceExtraction.source_id == source_id)
            .order_by(SourceExtraction.version.desc())
        ).scalars()
    )


def get_extraction(
    db: Session, source_id: uuid.UUID, version: int | None, scope: Scope
) -> SourceExtraction:
    """One version of a source's extraction; `version=None` means the latest."""
    get_source(db, source_id, scope)
    stmt = (
        select(SourceExtraction)
        .where(SourceExtraction.source_id == source_id)
        .options(selectinload(SourceExtraction.usage))
    )
    if version is None:
        stmt = stmt.order_by(SourceExtraction.version.desc()).limit(1)
    else:
        stmt = stmt.where(SourceExtraction.version == version)
    extraction = db.execute(stmt).scalar_one_or_none()
    if extraction is None:
        raise NotFoundError("Extraction not found")
    return extraction


def usage_report(
    db: Session,
    scope: Scope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> UsageReport:
    """Usage and cost totals inside the caller's scope, grouped three ways."""
    predicates = [
        scope.tenant_predicate(ExtractionUsage.tenant_id),
        scope.application_predicate(ExtractionUsage.application_id),
    ]
    if tenant_id is not None:
        predicates.append(ExtractionUsage.tenant_id == tenant_id)
    if application_id is not None:
        predicates.append(ExtractionUsage.application_id == application_id)
    if date_from is not None:
        predicates.append(ExtractionUsage.created_at >= date_from)
    if date_to is not None:
        predicates.append(ExtractionUsage.created_at < date_to)

    measures = (
        func.count(func.distinct(ExtractionUsage.extraction_id)),
        func.count(),
        func.coalesce(func.sum(ExtractionUsage.prompt_tokens), 0),
        func.coalesce(func.sum(ExtractionUsage.completion_tokens), 0),
        func.coalesce(func.sum(ExtractionUsage.cost_usd), 0.0),
    )

    def totals(row) -> dict:
        runs, calls, prompt, completion, cost = row
        return {
            "runs": runs,
            "calls": calls,
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "cost_usd": round(float(cost), 6),
        }

    overall = db.execute(select(*measures).where(*predicates)).one()

    by_application = db.execute(
        select(Application.id, Application.name, *measures)
        .join(Application, Application.id == ExtractionUsage.application_id)
        .where(*predicates)
        .group_by(Application.id, Application.name)
        .order_by(func.sum(ExtractionUsage.cost_usd).desc())
    ).all()

    by_model = db.execute(
        select(ExtractionUsage.model, ExtractionUsage.role, *measures)
        .where(*predicates)
        .group_by(ExtractionUsage.model, ExtractionUsage.role)
        .order_by(ExtractionUsage.model)
    ).all()

    by_trigger = db.execute(
        select(
            ExtractionUsage.triggered_by_kind,
            ExtractionUsage.triggered_by_user_id,
            ExtractionUsage.triggered_by_credential_id,
            Users.email,
            ApiCredential.name,
            *measures,
        )
        .outerjoin(Users, Users.id == ExtractionUsage.triggered_by_user_id)
        .outerjoin(ApiCredential, ApiCredential.id == ExtractionUsage.triggered_by_credential_id)
        .where(*predicates)
        .group_by(
            ExtractionUsage.triggered_by_kind,
            ExtractionUsage.triggered_by_user_id,
            ExtractionUsage.triggered_by_credential_id,
            Users.email,
            ApiCredential.name,
        )
    ).all()

    return UsageReport(
        totals=UsageTotals(**totals(overall)),
        by_application=[
            UsageGroup(key=str(app_id), label=name, **totals(rest))
            for app_id, name, *rest in by_application
        ],
        by_model=[
            UsageGroup(key=model, label=role, **totals(rest))
            for model, role, *rest in by_model
        ],
        by_trigger=[
            UsageGroup(
                key=f"{kind}:{user_id or credential_id}",
                label=(email if kind == "user" else credential_name) or kind,
                **totals(rest),
            )
            for kind, user_id, credential_id, email, credential_name, *rest in by_trigger
        ],
    )
