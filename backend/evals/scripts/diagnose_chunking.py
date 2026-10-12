#!/usr/bin/env python3
"""Diagnostic: run the pure chunking pipeline against the latest extraction's
readings, read-only, printing every step. No DB writes.

Usage:
    .venv/bin/python evals/scripts/diagnose_chunking.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

import os  # noqa: E402

os.environ.setdefault("API_SECRET", "diagnose-chunking")

from sqlalchemy import select  # noqa: E402

from app.db.database import SessionLocal  # noqa: E402
from app.db.models import SourceExtraction  # noqa: E402
from app.chunking import parse_readings, build_semantic_blocks  # noqa: E402
from app.chunking.structure import assign_section_paths, detect_numbered_headings  # noqa: E402
from app.chunking.chunker import chunk_semantic_block  # noqa: E402
from app.core.config import get_settings  # noqa: E402


def main() -> int:
    db = SessionLocal()
    try:
        ext = db.execute(
            select(SourceExtraction).order_by(SourceExtraction.version.desc()).limit(1)
        ).scalar_one()
        print(f"Extraction: {ext.id}  version={ext.version}  status={ext.status}")
        readings = ext.readings
        print(f"readings type: {type(readings).__name__}")
        if not isinstance(readings, list):
            print("!! readings is not a list — chunking would raise ValidationError")
            return 1
        print(f"readings count: {len(readings)}")
        for i, r in enumerate(readings):
            if isinstance(r, dict):
                print(
                    f"  reading[{i}]: kind={r.get('kind')} route={r.get('route')} "
                    f"status={r.get('status')} text_len={len(r.get('text') or '')}"
                )
            else:
                print(f"  reading[{i}]: NOT A DICT: {type(r).__name__}")

        print()
        print("--- running pure pipeline ---")
        units = parse_readings(readings)
        print(f"parse_readings OK -> {len(units)} units")
        if units:
            kinds: dict[str, int] = {}
            for u in units:
                kinds[u.kind] = kinds.get(u.kind, 0) + 1
            print(f"unit kinds: {kinds}")

        units = detect_numbered_headings(units)
        units = assign_section_paths(units)
        print(f"structure OK -> {len(units)} units")

        blocks = build_semantic_blocks(units)
        print(f"build_semantic_blocks OK -> {len(blocks)} blocks")
        if blocks:
            print(f"first block title: {blocks[0].title!r}")
            print(f"first block content[:200]: {blocks[0].content[:200]!r}")

        settings = get_settings()
        total_chunks = 0
        for block in blocks:
            chunks = chunk_semantic_block(block, settings)
            total_chunks += len(chunks)
        print(f"chunk_semantic_block OK -> {total_chunks} chunks total")
        if total_chunks == 0 and blocks:
            print("!! blocks exist but chunking produced 0 chunks")
        print()
        print("PIPELINE SUCCEEDED")
        return 0
    except Exception:
        print("PIPELINE FAILED:")
        traceback.print_exc()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
