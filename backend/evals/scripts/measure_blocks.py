#!/usr/bin/env python3
"""Measure the size of the semantic blocks behind your active chunks.

READ-ONLY against the database. A block counts when at least one of its chunks is
active (blocks carry no `is_active` of their own; older versions' blocks stay in
the table). Prints token percentiles, size buckets, chunks per block and the
largest blocks, to decide how Step 1 should feed blocks to the graph extractor
(split the big ones at chunk boundaries? merge the tiny ones?).

    .venv/bin/python evals/scripts/measure_blocks.py --tenant-id <uuid> [--subject-id <uuid>] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("API_SECRET", "measure-blocks")

from sqlalchemy import select  # noqa: E402

from app.db.database import SessionLocal  # noqa: E402
from app.db.models import RetrievalChunk, SemanticBlock, Source  # noqa: E402
from evals.block_stats import summarize_blocks  # noqa: E402


def load_blocks(db, tenant_id, subject_id) -> list[dict]:
    stmt = (
        select(SemanticBlock, RetrievalChunk.token_count, RetrievalChunk.content_type, Source.filename)
        .join(RetrievalChunk, RetrievalChunk.semantic_block_id == SemanticBlock.id)
        .join(Source, Source.id == SemanticBlock.source_id)
        .where(SemanticBlock.tenant_id == tenant_id, RetrievalChunk.is_active.is_(True))
    )
    if subject_id:
        stmt = stmt.where(SemanticBlock.subject_id == subject_id)
    blocks: dict[uuid.UUID, dict] = {}
    chunks: dict[uuid.UUID, list[int]] = defaultdict(list)
    tables: dict[uuid.UUID, bool] = defaultdict(bool)
    for block, token_count, content_type, filename in db.execute(stmt).all():
        blocks.setdefault(block.id, {
            "content": block.content, "section_path": block.section_path or [],
            "title": block.title, "filename": filename,
        })
        chunks[block.id].append(token_count)
        tables[block.id] = tables[block.id] or content_type == "table"
    return [{**b, "chunks": chunks[i], "has_table": tables[i]} for i, b in blocks.items()]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--subject-id")
    p.add_argument("--small", type=int, default=100, help="count blocks under this many tokens")
    p.add_argument("--large", type=int, default=3000, help="count blocks over this many tokens")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    with SessionLocal() as db:
        blocks = load_blocks(
            db, uuid.UUID(args.tenant_id), uuid.UUID(args.subject_id) if args.subject_id else None
        )
    report = summarize_blocks(blocks, small=args.small, large=args.large)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    t = report["tokens"]
    print(f"{report['blocks']} active blocks, {t['total']} tokens in total")
    print(f"tokens  min {t['min']}  p10 {t['p10']}  p50 {t['p50']}  p90 {t['p90']}  p99 {t['p99']}  max {t['max']}")
    for label, count in report["buckets"].items():
        print(f"  {label:>10} tokens: {count}")
    print(f"under {args.small} tokens: {report['below_small']}   over {args.large} tokens: {report['above_large']}")
    c = report["chunks_per_block"]
    print(f"chunks per block  p50 {c['p50']}  p90 {c['p90']}  max {c['max']}  single-chunk share {c['single_chunk_share']}")
    print(f"blocks with a table chunk: {report['table_share']}   without a section path: {report['no_section_path_share']}")
    print("largest blocks:")
    for b in report["largest"]:
        print(f"  {b['tokens']:>6} tokens  {b['file']}  {b['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
