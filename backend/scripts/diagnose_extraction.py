"""Diagnose why one source's extraction runs vary in wall-clock time.

For every run (version) of a source, print:
  * the run's status, wall time and failure reason (when failed);
  * per-phase timings (parse / read / windows / metadata / agent / total),
    from the `timings` column when present, otherwise derived from the usage
    rows' created_at timestamps;
  * every usage row (role, model, page, latency, status, error), with rows
    slower than --slow-ms flagged;
  * the readings' notes (cache hits, fallbacks, cleaning-gate failures).

Read-only: this script never writes to the database and makes no model calls.

Usage:
    python scripts/diagnose_extraction.py <source_id> [--slow-ms 15000] [--version N]

The source id is the UUID the console shows, or the `id` column of `sources`.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.database import SessionLocal  # noqa: E402
from app.db.models import ExtractionUsage, Source, SourceExtraction  # noqa: E402


def _fmt_ms(ms: int | float | None) -> str:
    if ms is None:
        return "-"
    ms = int(ms)
    if ms >= 60_000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def _fmt_duration(started: datetime, finished: datetime | None) -> str:
    if finished is None:
        return "never finished"
    seconds = (finished - started).total_seconds()
    if seconds >= 90:
        return f"{seconds / 60:.1f} min"
    return f"{seconds:.1f}s"


def _derive_phases(usage_rows: list[ExtractionUsage]) -> dict[str, int]:
    """Approximate phase spans from usage-row timestamps, for runs that
    predate the `timings` column: reading = layout+vision rows, extraction =
    extract rows. Overlapping threads make this a lower bound, not exact."""
    phases: dict[str, int] = {}
    reading = [u for u in usage_rows if u.role in ("layout", "vision")]
    extracting = [u for u in usage_rows if u.role == "extract"]
    if reading:
        start = min(u.created_at for u in reading)
        end = max(
            (u.created_at for u in reading),
            default=None,
        )
        # Add each row's own latency: created_at is when the row was written,
        # after the call returned.
        span = (max(u.created_at for u in reading) - start).total_seconds() * 1000
        phases["read_ms~"] = int(span + max((u.latency_ms for u in reading), default=0))
    if extracting:
        start = min(u.created_at for u in extracting)
        span = (max(u.created_at for u in extracting) - start).total_seconds() * 1000
        phases["extract_ms~"] = int(span + max((u.latency_ms for u in extracting), default=0))
    return phases


def _print_run(
    run: SourceExtraction,
    usage_rows: list[ExtractionUsage],
    slow_ms: int,
) -> None:
    print(f"\n=== version {run.version} · {run.status} ===")
    print(f"  mode: {run.mode}  provider: {run.provider}")
    print(f"  started: {run.created_at.isoformat(sep=' ', timespec='seconds')}")
    print(f"  wall time: {_fmt_duration(run.created_at, run.finished_at)}")
    if run.error:
        print(f"  ERROR: {run.error}")

    timings = dict(run.timings or {})
    if not timings:
        timings = _derive_phases(usage_rows)
        if timings:
            print("  (timings column empty; phases derived from usage rows, approximate)")
    if timings:
        print("  phases:")
        for key in sorted(timings):
            print(f"    {key:<14} {_fmt_ms(timings[key])}")

    if not usage_rows:
        print("  no usage rows recorded")
        return

    print(f"  usage rows ({len(usage_rows)}):")
    flagged = 0
    for u in usage_rows:
        flag = ""
        if u.latency_ms >= slow_ms:
            flag = "  <-- SLOW"
            flagged += 1
        error = f"  error={u.error}" if u.error else ""
        print(
            f"    [{u.created_at.strftime('%H:%M:%S')}] {u.role:<7} {u.model} "
            f"page={u.page if u.page is not None else '-'} "
            f"latency={_fmt_ms(u.latency_ms)} status={u.status}{error}{flag}"
        )
    if flagged:
        print(f"  {flagged} row(s) above {slow_ms}ms")


def _print_readings(run: SourceExtraction) -> None:
    readings = run.readings or []
    if not readings:
        return
    notes = [
        (r.get("page"), r.get("note"))
        for r in readings
        if isinstance(r, dict) and r.get("note")
    ]
    if not notes:
        return
    print("  reading notes:")
    for page, note in notes:
        print(f"    page {page}: {note}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_id", help="the source's UUID")
    parser.add_argument(
        "--slow-ms", type=int, default=15_000,
        help="flag usage rows slower than this (default: 15000)",
    )
    parser.add_argument(
        "--version", type=int, default=None,
        help="only show this version (default: every version)",
    )
    args = parser.parse_args()

    try:
        source_id = uuid.UUID(args.source_id)
    except ValueError:
        sys.exit(f"not a UUID: {args.source_id}")

    with SessionLocal() as db:
        source = db.get(Source, source_id)
        if source is None:
            sys.exit(f"no source with id {source_id}")

        stmt = (
            select(SourceExtraction)
            .where(SourceExtraction.source_id == source_id)
            .order_by(SourceExtraction.version)
        )
        if args.version is not None:
            stmt = stmt.where(SourceExtraction.version == args.version)
        runs = list(db.execute(stmt).scalars())
        if not runs:
            sys.exit("no extraction runs for this source")

        print(f"source {source_id} · {source.filename or '(no filename)'}")
        print(f"{len(runs)} run(s)")

        for run in runs:
            usage_rows = list(
                db.execute(
                    select(ExtractionUsage)
                    .where(ExtractionUsage.extraction_id == run.id)
                    .order_by(ExtractionUsage.created_at)
                ).scalars()
            )
            _print_run(run, usage_rows, args.slow_ms)
            _print_readings(run)


if __name__ == "__main__":
    main()
