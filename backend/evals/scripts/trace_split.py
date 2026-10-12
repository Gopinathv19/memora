#!/usr/bin/env python3
"""Trace _split_into_elements line-by-line to find the exact hang position.

Replicates the loop from app/chunking/units.py with an iteration cap and
progress printing. Read-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

import os  # noqa: E402

os.environ.setdefault("API_SECRET", "trace-split")

from sqlalchemy import select  # noqa: E402

from app.db.database import SessionLocal  # noqa: E402
from app.db.models import SourceExtraction  # noqa: E402
from app.chunking.units import (  # noqa: E402
    _FIGURE,
    _FIGURE_BARE,
    _HEADING,
    _KEY_VALUE,
    _LIST_ITEM,
    _TABLE_ROW,
    _TABLE_SEP,
    _normalize_latex,
)
import re  # noqa: E402


def trace_split(lines_with_pages, max_iters=20000):
    units = 0
    current_page = None
    i = 0
    n = len(lines_with_pages)
    iters = 0
    last_i = -1
    stuck_count = 0

    while i < n:
        iters += 1
        if iters > max_iters:
            print(f"!! ITERATION CAP HIT at i={i}")
            print(f"   line[{i}]: {lines_with_pages[i][0]!r}")
            if i + 1 < n:
                print(f"   line[{i+1}]: {lines_with_pages[i+1][0]!r}")
            if i + 2 < n:
                print(f"   line[{i+2}]: {lines_with_pages[i+2][0]!r}")
            return
        if i == last_i:
            stuck_count += 1
            if stuck_count > 50:
                print(f"!! STUCK at i={i}: {lines_with_pages[i][0][:150]!r}")
                return
        else:
            stuck_count = 0
        last_i = i

        line, page = lines_with_pages[i]
        stripped = line.strip()

        if page is not None:
            current_page = page
        if not stripped:
            i += 1
            continue

        if _HEADING.match(stripped):
            units += 1
            i += 1
            continue

        if _FIGURE.match(stripped) or (
            _FIGURE_BARE.match(stripped) and len(stripped) < 200
        ):
            units += 1
            i += 1
            continue

        if _TABLE_ROW.match(stripped) and i + 1 < n and _TABLE_SEP.match(
            lines_with_pages[i + 1][0].strip()
        ):
            while i < n:
                tsline = lines_with_pages[i][0].strip()
                if not _TABLE_ROW.match(tsline):
                    break
                i += 1
            units += 1
            continue

        if _LIST_ITEM.match(stripped):
            while i < n:
                lline = lines_with_pages[i][0]
                lsline = lline.strip()
                if not lsline:
                    if i + 1 < n and _LIST_ITEM.match(lines_with_pages[i + 1][0].strip()):
                        i += 1
                        continue
                    break
                if not _LIST_ITEM.match(lsline) and not lline.startswith("  "):
                    break
                i += 1
            units += 1
            continue

        m = _KEY_VALUE.match(stripped)
        if m and not stripped.startswith("|"):
            key = m.group(1)
            if (
                len(key) <= 30
                and "," not in key
                and ";" not in key
                and not re.search(r"[.!?]$", key)
            ):
                while i < n:
                    ksline = lines_with_pages[i][0].strip()
                    if not ksline:
                        if i + 1 < n and _KEY_VALUE.match(lines_with_pages[i + 1][0].strip()):
                            i += 1
                            continue
                        break
                    if not _KEY_VALUE.match(ksline):
                        break
                    i += 1
                units += 1
                continue

        # Paragraph
        para_lines = []
        start_i = i
        while i < n:
            pline = lines_with_pages[i][0]
            psline = pline.strip()
            if not psline:
                break
            km = _KEY_VALUE.match(psline)
            if (
                _HEADING.match(psline)
                or _FIGURE.match(psline)
                or (_FIGURE_BARE.match(psline) and len(psline) < 200)
                or _TABLE_ROW.match(psline)
                or _LIST_ITEM.match(psline)
                or (
                    km
                    and len(km.group(1)) <= 30
                    and "," not in km.group(1)
                    and ";" not in km.group(1)
                )
            ):
                break
            para_lines.append(psline)
            i += 1
        if para_lines:
            units += 1
        elif i == start_i:
            print(f"!! ZERO-PROGRESS PARAGRAPH at i={i}")
            print(f"   line[{i}]: {lines_with_pages[i][0][:200]!r}")
            if i + 1 < n:
                print(f"   line[{i+1}]: {lines_with_pages[i+1][0][:200]!r}")
            return

    print(f"COMPLETED: {units} units, {iters} iterations, i={i}, n={n}")


def main() -> int:
    db = SessionLocal()
    try:
        ext = db.execute(
            select(SourceExtraction).order_by(SourceExtraction.version.desc()).limit(1)
        ).scalar_one()
        text = (ext.readings[0].get("text") or "").strip()
        lines_with_pages = [(ln, 1) for ln in text.split("\n")]
        normalized = _normalize_latex(lines_with_pages)
        print(f"normalized lines: {len(normalized)}")
        trace_split(normalized)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
