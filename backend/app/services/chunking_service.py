"""Chunking service: readings → document_units, semantic_blocks, retrieval_chunks.

The only module that writes chunking tables. It takes a completed extraction's
readings, runs the pure chunking pipeline, persists the results, and flips
`is_active` on the new version's chunks while deactivating older versions --
all in one transaction (strategy §27, transactional replacement).

Auto-chunks after a successful extraction when `chunk_on_extract` is true, and
on explicit request via `POST /sources/{id}/rechunk`.
"""

import logging
import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.chunking import (
    build_semantic_blocks,
    chunk_semantic_block,
    parse_readings,
)
from app.chunking.structure import assign_section_paths, detect_numbered_headings
from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.db.models import (
    DocumentUnit,
    RetrievalChunk,
    SemanticBlock,
    Source,
    SourceExtraction,
)

from app.services.scope import Scope
from app.services.source_service import get_source

log = logging.getLogger(__name__)


def chunk_extraction(
    db: Session, source_id: uuid.UUID, scope: Scope, version: int | None = None
) -> SourceExtraction:
    """Chunk an extraction's readings into units, blocks and chunks.

    `version=None` means the latest extraction. The source must be in scope.
    The extraction must have readings (completed or partial). Older versions'
    chunks are deactivated in the same transaction as the new chunks' insertion.
    """
    get_source(db, source_id, scope)  # scope check; 404 outside it
    source = db.get(Source, source_id)
    if source is None:
        raise NotFoundError("Source not found")

    stmt = select(SourceExtraction).where(SourceExtraction.source_id == source_id)
    if version is None:
        stmt = stmt.order_by(SourceExtraction.version.desc()).limit(1)
    else:
        stmt = stmt.where(SourceExtraction.version == version)
    extraction = db.execute(stmt).scalar_one_or_none()
    if extraction is None:
        raise NotFoundError("Extraction not found")

    if not extraction.readings:
        raise ValidationError("This extraction has no readings to chunk")

    readings = extraction.readings
    if not isinstance(readings, list):
        raise ValidationError("Extraction readings are malformed")

    # --- Clear any prior chunking artifacts for this extraction (idempotent) ---
    db.execute(
        delete(RetrievalChunk).where(RetrievalChunk.extraction_id == extraction.id)
    )
    db.execute(
        delete(SemanticBlock).where(SemanticBlock.extraction_id == extraction.id)
    )
    db.execute(
        delete(DocumentUnit).where(DocumentUnit.extraction_id == extraction.id)
    )
    db.flush()

    # --- Run the pure chunking pipeline ---------------------------------------

    units = parse_readings(readings)
    units = detect_numbered_headings(units)
    units = assign_section_paths(units)
    blocks = build_semantic_blocks(units)

    settings = get_settings()

    # --- Persist document_units -----------------------------------------------
    unit_id_map: list[uuid.UUID] = []  # index → unit id
    for position, u in enumerate(units):
        unit = DocumentUnit(
            extraction_id=extraction.id,
            tenant_id=source.tenant_id,
            application_id=source.application_id,
            subject_id=source.subject_id,
            source_id=source.id,
            position=position,
            kind=u.kind,
            content=u.content,
            page=u.page,
            heading_level=u.heading_level,
            section_path=u.section_path,
        )
        db.add(unit)
        db.flush()  # get the id
        unit_id_map.append(unit.id)

    # --- Persist semantic_blocks + retrieval_chunks ---------------------------
    global_chunk_index = 0
    for block_position, block in enumerate(blocks):
        block_unit_ids = [unit_id_map[i] for i in block.unit_indices if i < len(unit_id_map)]
        semantic_block = SemanticBlock(
            extraction_id=extraction.id,
            tenant_id=source.tenant_id,
            application_id=source.application_id,
            subject_id=source.subject_id,
            source_id=source.id,
            position=block_position,
            section_path=block.section_path,
            title=block.title,
            unit_ids=[str(uid) for uid in block_unit_ids],
            page_start=block.page_start,
            page_end=block.page_end,
            content=block.content,
        )
        db.add(semantic_block)
        db.flush()

        chunk_list = chunk_semantic_block(block, settings)
        for c in chunk_list:
            chunk_unit_ids = [
                str(unit_id_map[i])
                for i in block.unit_indices
                if i < len(unit_id_map)
            ]
            db.add(RetrievalChunk(
                semantic_block_id=semantic_block.id,
                extraction_id=extraction.id,
                source_version=extraction.version,
                tenant_id=source.tenant_id,
                application_id=source.application_id,
                subject_id=source.subject_id,
                source_id=source.id,
                chunk_index=global_chunk_index,
                content=c.content,
                embedding_text=c.embedding_text,
                token_count=c.token_count,
                content_type=c.content_type,
                page_start=c.page_start,
                page_end=c.page_end,
                document_unit_ids=chunk_unit_ids,
                section_path=c.section_path,
                is_active=True,
            ))
            global_chunk_index += 1


    # --- Deactivate older versions' chunks in the same transaction -------------
    db.execute(
        update(RetrievalChunk)
        .where(
            RetrievalChunk.source_id == source.id,
            RetrievalChunk.extraction_id != extraction.id,
            RetrievalChunk.is_active == True,  # noqa: E712
        )
        .values(is_active=False)
    )

    db.commit()
    db.refresh(extraction)
    return extraction


def list_chunks(
    db: Session, source_id: uuid.UUID, scope: Scope, active_only: bool = True
) -> list[RetrievalChunk]:
    """List chunks for a source, optionally filtered to the active version."""
    get_source(db, source_id, scope)
    stmt = select(RetrievalChunk).where(RetrievalChunk.source_id == source_id)
    if active_only:
        stmt = stmt.where(RetrievalChunk.is_active == True)  # noqa: E712
    stmt = stmt.order_by(RetrievalChunk.extraction_id, RetrievalChunk.chunk_index)
    return list(db.execute(stmt).scalars())
