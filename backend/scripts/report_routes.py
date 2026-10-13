"""Report how a folder of sample documents routes its pages.

For tuning the triage thresholds (triage_* settings) without changing any
default: run this over representative documents, see how many pages go to
TEXT / VISION / LAYOUT, and adjust the settings in backend/.env.

Usage:
    python scripts/report_routes.py <folder> [--deep]

Reads .pdf, .docx, .pptx, .xlsx, images and text files. Local and free:
no model calls are made.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.processing.processor import DocumentProcessor  # noqa: E402
from app.schemas.enums import ExtractionMode  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", help="folder of sample documents")
    parser.add_argument("--deep", action="store_true", help="route every PDF page to LAYOUT")
    args = parser.parse_args()

    settings = get_settings()
    processor = DocumentProcessor(settings)
    mode = ExtractionMode.DEEP if args.deep else ExtractionMode.STANDARD
    root = Path(args.folder)
    files = [p for p in root.rglob("*") if p.is_file()]

    totals: Counter = Counter()
    for path in sorted(files):
        try:
            doc = processor.process(path.read_bytes(), None, path.name, mode)
        except Exception as exc:
            print(f"{path.name}: skipped ({exc})")
            continue
        routes = Counter(u.route.value for u in doc.units)
        totals.update(routes)
        per = ", ".join(f"{k}={v}" for k, v in sorted(routes.items()))
        print(f"{path.name}: {len(doc.units)} unit(s) [{per}]")
        for w in doc.warnings:
            print(f"    warning: {w}")

    print("\nTotals across all documents:")
    for route, count in sorted(totals.items()):
        print(f"  {route:8s} {count}")
    total_units = sum(totals.values())
    if total_units:
        print(f"  share: " + ", ".join(
            f"{r}={c / total_units:.0%}" for r, c in sorted(totals.items())
        ))


if __name__ == "__main__":
    main()
