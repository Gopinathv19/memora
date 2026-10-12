#!/usr/bin/env python3
"""Find lines in the latest extraction's readings that would trigger the
paragraph infinite-loop in _split_into_elements (a table row not followed
by a separator row). Read-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

import os  # noqa: E402

os.environ.setdefault("API_SECRET", "find-bad-table")

from sqlalchemy import select  # noqa: E402

from app.db.database import SessionLocal  # noqa: E402
from app.db.models import SourceExtraction  # noqa: E402
from app.chunking.units import _TABLE_ROW, _TABLE_SEP  # noqa: E402


def main() -> int:
    db = SessionLocal()
    try:
        ext = db.execute(
            select(SourceExtraction).order_by(SourceExtraction.version.desc()).limit(1)
        ).scalar_one()
        text = (ext.readings[0].get("text") or "").strip()
        lines = text.split("\n")
        print(f"total lines: {len(lines)}")
        bad = 0
        for i, line in enumerate(lines):
            s = line.strip()
            if _TABLE_ROW.match(s):
                nxt = lines[i + 1].strip() if i + 1 < len(lines) else None
                if nxt is None or not _TABLE_SEP.match(nxt):
                    bad += 1
                    print(f"BAD TABLE ROW at line {i}: {s[:120]!r}")
                    print(f"    next line {i+1}: {nxt[:120]!r}")
        print(f"\n{bad} table row(s) not followed by a separator -> infinite loop trigger(s)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
