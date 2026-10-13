"""Re-apply cleaning and the new chunker to already-stored readings.

MANUAL ONLY. This script is never run automatically. It re-applies
`clean_reading()` to every stored reading, reports which units fail the
damage/loop checks (so only those need re-extraction), and -- when run with
`--apply` -- regenerates chunks and embeddings for the affected extractions.

Default is --dry-run: it only reports, changing nothing.

Usage:
    python scripts/backfill_cleaning.py            # dry run (default)
    python scripts/backfill_cleaning.py --apply   # rewrite readings + rechunk
    python scripts/backfill_cleaning.py --source <uuid>   # one source only

Requires the backend environment (DATABASE_URL) and a running database.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.processing.cleaning import clean_reading  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="actually rewrite readings and re-chunk (default: dry run)")
    parser.add_argument("--source", type=uuid.UUID, default=None,
                        help="limit to one source id")
    args = parser.parse_args()

    settings = get_settings()
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.db.models import SourceExtraction

    engine = create_engine(settings.database_url)
    failed_units: list[str] = []
    touched_extractions: list = []

    # An ORM Session (not a bare connection) so rows come back as models.
    with Session(engine, expire_on_commit=False) as conn:
        stmt = select(SourceExtraction)
        if args.source:
            stmt = stmt.where(SourceExtraction.source_id == args.source)
        extractions = conn.execute(stmt).scalars().all()

        for ex in extractions:
            readings = ex.readings
            if not isinstance(readings, list) or not readings:
                continue
            changed = False
            for r in readings:
                if not isinstance(r, dict):
                    continue
                text = r.get("text") or ""
                if not text:
                    continue
                cleaned, report = clean_reading(
                    text,
                    loop_compression_min=settings.cleaning_loop_compression_min,
                    loop_min_chars=settings.cleaning_loop_min_chars,
                )
                if report.loop_detected or report.damage_ratio > settings.cleaning_damage_ratio_threshold:
                    failed_units.append(
                        f"source {ex.source_id} extraction v{ex.version} "
                        f"{r.get('kind')} {r.get('page')}: damage "
                        f"{report.damage_ratio:.3f}, loop={report.loop_detected} "
                        f"(needs re-extraction)"
                    )
                if cleaned != text:
                    changed = True
                    if args.apply:
                        r["raw_text"] = r.get("raw_text") or text
                        r["text"] = cleaned
                        r["damage_ratio"] = round(report.damage_ratio, 4)
                        r["loop_detected"] = report.loop_detected
            if changed:
                touched_extractions.append(ex)
                print(
                    f"source {ex.source_id} v{ex.version}: readings would be cleaned"
                    if not args.apply
                    else f"source {ex.source_id} v{ex.version}: readings cleaned"
                )

    print(f"\n{len(touched_extractions)} extraction(s) "
          f"{'would be ' if not args.apply else ''}updated.")
    if failed_units:
        print(f"\n{len(failed_units)} unit(s) failed damage/loop checks "
              "(re-extract these):")
        for line in failed_units:
            print(f"  {line}")

    if not args.apply:
        print("\nDry run: nothing was written. Re-run with --apply to apply.")
        return 0

    # --apply: persist the cleaned readings, then re-chunk (which also
    # re-embeds via the existing chunk_on_extract/embed_on_chunk pipeline).
    with engine.begin() as conn:
        for ex in touched_extractions:
            conn.execute(
                SourceExtraction.__table__.update()
                .where(SourceExtraction.id == ex.id)
                .values(readings=json.loads(json.dumps(ex.readings)))
            )
    print(f"\nApplied: {len(touched_extractions)} extraction(s) rewritten.")
    print("Re-chunk each affected source with: POST /sources/{id}/rechunk")
    print("(or run chunk_extraction per source), which regenerates chunks and")
    print("embeddings from the cleaned readings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
