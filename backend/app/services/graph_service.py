"""Knowledge-graph builds and graph retrieval: the Postgres side.

The graph pipeline is Source -> extraction content -> GraphIngestionService ->
FalkorDB, and this module is the only part of it that writes to Postgres:

* `start_build` scope-checks the source, picks the extraction version to read
  and claims a `source_graph_builds` row (409 while one is running).
* `run_build` executes it afterwards in a FastAPI background task with its own
  session, records every model call in the shared `extraction_usage` ledger
  (role `graph`, `graph_build_id` set) and never leaves a build `processing`.
* `subject_graph` / `query_subject` resolve a subject -- and its folders --
  to the source ids the caller may see, and only then ask the graph.

A source's `status` is not touched: it follows the extraction, not the graph.
"""

import logging
import uuid
from datetime import UTC, datetime

from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from app.core.pricing import price_call
from app.db.database import SessionLocal
from app.db.models import (
    Actor,
    ExtractionUsage,
    Source,
    SourceExtraction,
    SourceGraphBuild,
)
from app.graph import store as graph_store
from app.graph.ingestion import GraphIngestionService
from app.graph.retrieval import GraphRetrievalService
from app.schemas.enums import ExtractionStatus, GraphBuildStatus
from app.schemas.graph import GraphBuildRequest, GraphQueryRequest, GraphRetrievalResult
from app.services.extraction_service import _triggered_by
from app.services.scope import Scope
from app.services.source_service import get_source
from app.services.subject_service import _descendant_ids, get_subject

log = logging.getLogger(__name__)

NOT_CONFIGURED = "The knowledge graph is not configured on this server (FALKORDB_URL)"
_READABLE = (ExtractionStatus.COMPLETED.value, ExtractionStatus.PARTIAL.value)


def _require(service):
    if service is None:
        raise ServiceUnavailableError(NOT_CONFIGURED)
    return service


def _reachable(read):
    """Run a graph read; an unreachable or misconfigured FalkorDB is a 503."""
    try:
        return read()
    except RedisError as exc:
        log.warning("knowledge graph unavailable: %s", exc)
        raise ServiceUnavailableError(
            f"The knowledge graph is not reachable right now ({type(exc).__name__})"
        ) from exc


def _latest_build(db: Session, source_id: uuid.UUID) -> SourceGraphBuild | None:
    return db.execute(
        select(SourceGraphBuild)
        .where(SourceGraphBuild.source_id == source_id)
        .order_by(SourceGraphBuild.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _running(db: Session, source_id: uuid.UUID) -> bool:
    return bool(
        db.execute(
            select(func.count())
            .select_from(SourceGraphBuild)
            .where(
                SourceGraphBuild.source_id == source_id,
                SourceGraphBuild.status == GraphBuildStatus.PROCESSING.value,
            )
        ).scalar_one()
    )


def _latest_readable_extraction(db: Session, source_id: uuid.UUID) -> SourceExtraction | None:
    return db.execute(
        select(SourceExtraction)
        .where(
            SourceExtraction.source_id == source_id,
            SourceExtraction.status.in_(_READABLE),
        )
        .order_by(SourceExtraction.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def _new_build(
    source: Source,
    extraction: SourceExtraction,
    triggered_by: dict,
    actor_id: uuid.UUID | None,
    retry_of: SourceGraphBuild | None = None,
) -> SourceGraphBuild:
    settings = get_settings()
    return SourceGraphBuild(
        source_id=source.id,
        tenant_id=source.tenant_id,
        application_id=source.application_id,
        extraction_id=extraction.id,
        extraction_version=extraction.version,
        retry_of_id=retry_of.id if retry_of else None,
        status=GraphBuildStatus.PROCESSING.value,
        provider=settings.llm_provider,
        model=settings.llm_graph_model,
        # A retry must chunk exactly like the build it retries.
        chunk_chars=retry_of.chunk_chars if retry_of else settings.graph_chunk_chars,
        chunk_overlap=retry_of.chunk_overlap if retry_of else settings.graph_chunk_overlap,
        actor_id=actor_id,
        **triggered_by,
    )


def start_build(
    db: Session,
    source_id: uuid.UUID,
    request: GraphBuildRequest,
    scope: Scope,
    ingestion: GraphIngestionService | None,
) -> SourceGraphBuild:
    """Claim a graph build for a source. The source row is locked meanwhile,
    so two concurrent requests cannot both start one."""
    get_source(db, source_id, scope)  # scope check first; 404 outside it
    _require(ingestion)
    source = db.execute(
        select(Source).where(Source.id == source_id).with_for_update()
    ).scalar_one()
    if request.actor_id is not None:
        actor = db.get(Actor, request.actor_id)
        if actor is None or actor.application_id != source.application_id:
            raise ValidationError("actor_id does not reference an actor in this application")
    if _running(db, source.id):
        raise ConflictError("A graph build is already running for this source")

    retry_of = None
    if request.retry_failed:
        retry_of = _latest_build(db, source.id)
        if retry_of is None or retry_of.status != GraphBuildStatus.PARTIAL.value:
            raise ValidationError(
                "retry_failed needs a latest build that is `partial`; start a full build instead"
            )
        extraction = db.get(SourceExtraction, retry_of.extraction_id)
    else:
        extraction = _latest_readable_extraction(db, source.id)
        if extraction is None:
            raise ValidationError("This source has no completed extraction; extract it first")
    if extraction is None or not extraction.content:
        raise ValidationError(
            "This extraction has no stored content (it predates the knowledge graph); "
            "re-extract the source first"
        )

    build = _new_build(source, extraction, _triggered_by(scope), request.actor_id, retry_of)
    db.add(build)
    db.commit()
    db.refresh(build)
    return build


def _record_usage(db: Session, build: SourceGraphBuild, records) -> None:
    for record in records:
        cost, price = price_call(
            build.provider,
            record.model,
            record.role.value,
            record.prompt_tokens,
            record.completion_tokens,
            at=build.created_at,
        )
        db.add(
            ExtractionUsage(
                extraction_id=build.extraction_id,
                graph_build_id=build.id,
                source_id=build.source_id,
                tenant_id=build.tenant_id,
                application_id=build.application_id,
                triggered_by_kind=build.triggered_by_kind,
                triggered_by_user_id=build.triggered_by_user_id,
                triggered_by_credential_id=build.triggered_by_credential_id,
                actor_id=build.actor_id,
                provider=build.provider,
                model=record.model,
                role=record.role.value,
                page=record.page,
                prompt_tokens=record.prompt_tokens,
                completion_tokens=record.completion_tokens,
                cost_usd=cost,
                price=price,
                latency_ms=record.latency_ms,
                status=record.status,
                error=record.error,
            )
        )
        build.prompt_tokens += record.prompt_tokens
        build.completion_tokens += record.completion_tokens
        build.cost_usd = round(build.cost_usd + cost, 6)


def run_build(build_id: uuid.UUID, ingestion: GraphIngestionService) -> None:
    """Execute one claimed build. Never raises, never leaves it `processing`."""
    db = SessionLocal()
    try:
        build = db.get(SourceGraphBuild, build_id)
        if build is None:
            return
        source = db.get(Source, build.source_id)
        extraction = db.get(SourceExtraction, build.extraction_id)
        if source is None or extraction is None:
            return
        only = None
        if build.retry_of_id is not None:
            previous = db.get(SourceGraphBuild, build.retry_of_id)
            only = {f["index"] for f in (previous.failed_chunks if previous else [])}
        try:
            result = ingestion.ingest_document(
                tenant_id=str(build.tenant_id),
                application_id=str(build.application_id),
                subject_id=str(source.subject_id),
                source_id=str(source.id),
                extraction_id=str(extraction.id),
                extraction_version=extraction.version,
                build_id=str(build.id),
                content=extraction.content or "",
                filename=source.filename,
                chunk_chars=build.chunk_chars,
                chunk_overlap=build.chunk_overlap,
                only_chunks=only,
            )
            _record_usage(db, build, result.usage)
            build.status = result.status.value
            build.error = result.error
            build.chunk_count = result.chunk_count
            build.failed_chunks = result.failed_chunks
            build.failed_chunk_count = len(result.failed_chunks)
            build.entity_count = result.entity_count
            build.relationship_count = result.relationship_count
            build.stats = result.stats
        except Exception as exc:  # the graph write failed, or something unexpected
            log.exception("graph build %s crashed", build_id)
            build.status = GraphBuildStatus.FAILED.value
            build.error = f"Internal error: {type(exc).__name__}: {exc}"[:2000]
        build.finished_at = datetime.now(UTC)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("could not record the outcome of graph build %s", build_id)
        _mark_failed(build_id, "Internal error while saving the result")
    finally:
        db.close()


def _mark_failed(build_id: uuid.UUID, message: str) -> None:
    db = SessionLocal()
    try:
        build = db.get(SourceGraphBuild, build_id)
        if build is not None:
            build.status = GraphBuildStatus.FAILED.value
            build.error = message
            build.finished_at = datetime.now(UTC)
            db.commit()
    finally:
        db.close()


def build_after_extraction(
    extraction_id: uuid.UUID, ingestion: GraphIngestionService | None
) -> None:
    """Background task queued after `run_extraction` when `build_graph=true`.

    Starts a build if the extraction produced content. Attributed to whoever
    triggered the extraction. Never raises.
    """
    if ingestion is None:
        log.warning("build_graph requested but the graph is not configured; skipped")
        return
    db = SessionLocal()
    try:
        extraction = db.get(SourceExtraction, extraction_id)
        if extraction is None or extraction.status not in _READABLE or not extraction.content:
            return
        source = db.execute(
            select(Source).where(Source.id == extraction.source_id).with_for_update()
        ).scalar_one_or_none()
        if source is None or _running(db, source.id):
            return
        build = _new_build(
            source,
            extraction,
            {
                "triggered_by_kind": extraction.triggered_by_kind,
                "triggered_by_user_id": extraction.triggered_by_user_id,
                "triggered_by_credential_id": extraction.triggered_by_credential_id,
            },
            extraction.actor_id,
        )
        db.add(build)
        db.commit()
        build_id = build.id
    except Exception:
        db.rollback()
        log.exception("could not start the graph build after extraction %s", extraction_id)
        return
    finally:
        db.close()
    run_build(build_id, ingestion)


def fail_interrupted_builds(db: Session) -> int:
    """At startup: a build still `processing` belonged to a process that died."""
    builds = db.execute(
        select(SourceGraphBuild).where(
            SourceGraphBuild.status == GraphBuildStatus.PROCESSING.value
        )
    ).scalars().all()
    for build in builds:
        build.status = GraphBuildStatus.FAILED.value
        build.error = "Interrupted: the server restarted during this build"
        build.finished_at = datetime.now(UTC)
    db.commit()
    return len(builds)


def sweep_orphaned_documents(db: Session) -> int:
    """At startup: remove graph data for sources Postgres no longer has.

    Catches any post-commit graph cleanup that failed (e.g. FalkorDB was down
    during a delete).
    """
    store = graph_store.get_graph_store()
    if store is None:
        return 0
    removed = 0
    for tenant_id in store.tenant_ids():
        in_graph = store.document_ids(tenant_id)
        if not in_graph:
            continue
        ids = []
        for value in in_graph:
            try:
                ids.append(uuid.UUID(value))
            except (TypeError, ValueError):
                continue
        existing = {
            str(sid)
            for sid in db.execute(
                select(Source.id).where(Source.tenant_id == tenant_id, Source.id.in_(ids))
            ).scalars()
        }
        for document_id in in_graph:
            if document_id not in existing:
                store.delete_source(tenant_id, document_id)
                removed += 1
    return removed


# --- reading ----------------------------------------------------------------------


def list_builds(db: Session, source_id: uuid.UUID, scope: Scope) -> list[SourceGraphBuild]:
    get_source(db, source_id, scope)
    return list(
        db.execute(
            select(SourceGraphBuild)
            .where(SourceGraphBuild.source_id == source_id)
            .order_by(SourceGraphBuild.created_at.desc())
        ).scalars()
    )


def get_latest_build(db: Session, source_id: uuid.UUID, scope: Scope) -> SourceGraphBuild:
    get_source(db, source_id, scope)
    build = db.execute(
        select(SourceGraphBuild)
        .where(SourceGraphBuild.source_id == source_id)
        .options(selectinload(SourceGraphBuild.usage))
        .order_by(SourceGraphBuild.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if build is None:
        raise NotFoundError("Graph build not found")
    return build


def subject_source_ids(
    db: Session, subject_id: uuid.UUID, scope: Scope, include_subfolders: bool = True
):
    """The subject (scope-checked) and the ids of every source the view covers."""
    subject = get_subject(db, subject_id, scope)
    subjects = _descendant_ids(db, subject.id) if include_subfolders else [subject.id]
    source_ids = db.execute(
        select(Source.id).where(
            Source.subject_id.in_(subjects),
            scope.tenant_predicate(Source.tenant_id),
            scope.application_predicate(Source.application_id),
        )
    ).scalars().all()
    return subject, [str(s) for s in source_ids]


def subject_graph(
    db: Session,
    subject_id: uuid.UUID,
    scope: Scope,
    retrieval: GraphRetrievalService | None,
    *,
    include_subfolders: bool = True,
    max_entities: int = 200,
    max_relationships: int = 500,
) -> GraphRetrievalResult:
    subject, source_ids = subject_source_ids(db, subject_id, scope, include_subfolders)
    service = _require(retrieval)
    return _reachable(
        lambda: service.view(
            tenant_id=subject.tenant_id,
            source_ids=source_ids,
            max_entities=max_entities,
            max_relationships=max_relationships,
        )
    )


def query_subject(
    db: Session,
    subject_id: uuid.UUID,
    request: GraphQueryRequest,
    scope: Scope,
    retrieval: GraphRetrievalService | None,
) -> GraphRetrievalResult:
    subject, source_ids = subject_source_ids(db, subject_id, scope, request.include_subfolders)
    service = _require(retrieval)
    return _reachable(
        lambda: service.retrieve(
            tenant_id=subject.tenant_id,
            source_ids=source_ids,
            query=request.query,
            max_hops=request.max_hops,
            max_entities=request.max_entities,
            max_relationships=request.max_relationships,
        )
    )
