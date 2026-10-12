#!/usr/bin/env python3
"""Repair a source whose auto-chunking failed: re-chunk + embed.

Runs the same service functions the API routes use:
  chunk_extraction()  -> units, blocks, chunks (pure, no LLM)
  enqueue_embeddings() -> pending chunk_embeddings rows
  claim_pending() + run_embedding_batch() -> real vectors (NVIDIA NIM)

Usage:
    .venv/bin/python evals/scripts/repair_source.py --source-id <uuid> [--skip-embed]
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

import os  # noqa: E402

os.environ.setdefault("API_SECRET", "repair-source")

from sqlalchemy import select, func  # noqa: E402

from app.db.database import SessionLocal  # noqa: E402
from app.db.models import (  # noqa: E402
    ChunkEmbedding,
    DocumentUnit,
    RetrievalChunk,
    SemanticBlock,
    Source,
    SourceExtraction,
)
from app.services.scope import Scope  # noqa: E402
from app.services.chunking_service import chunk_extraction  # noqa: E402
from app.services.embedding_service import (  # noqa: E402
    claim_pending,
    enqueue_embeddings,
    run_embedding_batch,
)


def counts(db, source_id) -> dict:
    def one(q):
        return db.execute(q).scalar()

    return {
        "document_units": one(select(func.count()).select_from(DocumentUnit).where(DocumentUnit.source_id == source_id)),
        "semantic_blocks": one(select(func.count()).select_from(SemanticBlock).where(SemanticBlock.source_id == source_id)),
        "retrieval_chunks": one(select(func.count()).select_from(RetrievalChunk).where(RetrievalChunk.source_id == source_id)),
        "active_chunks": one(select(func.count()).select_from(RetrievalChunk).where(RetrievalChunk.source_id == source_id, RetrievalChunk.is_active == True)),  # noqa: E712
        "chunk_embeddings": one(select(func.count()).select_from(ChunkEmbedding).join(RetrievalChunk, RetrievalChunk.id == ChunkEmbedding.chunk_id).where(RetrievalChunk.source_id == source_id)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--skip-embed", action="store_true", help="only re-chunk; do not embed")
    args = parser.parse_args()
    source_id = uuid.UUID(args.source_id)

    db = SessionLocal()
    try:
        source = db.get(Source, source_id)
        if source is None:
            print("source not found")
            return 1
        scope = Scope.for_credentials(source.tenant_id, source.application_id)

        print("BEFORE:", counts(db, source_id))

        # --- Step 1: re-chunk the latest extraction --------------------------
        extraction = chunk_extraction(db, source_id, scope)
        print(f"re-chunked extraction {extraction.id} (version {extraction.version})")
        print("AFTER CHUNK:", counts(db, source_id))

        if args.skip_embed:
            return 0

        # --- Step 2: enqueue + run the embedding worker ----------------------
        enqueued = enqueue_embeddings(db, scope, source_id=source_id)
        print(f"enqueued {enqueued} embedding(s)")

        total_completed = total_failed = 0
        while True:
            rows = claim_pending(db)
            if not rows:
                break
            result = run_embedding_batch(db, rows)
            total_completed += result["completed"]
            total_failed += result["failed"]
            print(f"batch: {result}")
            if result["failed"] and not result["completed"]:
                break  # provider error; stop instead of spinning

        print(f"embeddings completed={total_completed} failed={total_failed}")
        print("FINAL:", counts(db, source_id))

        # --- Step 3: verify eval-eligibility ---------------------------------
        eligible = db.execute(
            select(func.count())
            .select_from(RetrievalChunk)
            .join(ChunkEmbedding, ChunkEmbedding.chunk_id == RetrievalChunk.id)
            .where(
                RetrievalChunk.source_id == source_id,
                RetrievalChunk.is_active == True,  # noqa: E712
                ChunkEmbedding.embedding_status == "completed",
            )
        ).scalar()
        print(f"eval-eligible chunks (active + embedded): {eligible}")
        return 0 if eligible > 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
