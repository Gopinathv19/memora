"""Manual multilingual eval: per-page language, route, damage ratio, fallbacks.

Runs the LOCAL pipeline only (processor + cleaning + language detection) over
the fixture set or a folder of documents. No model calls, no cost. For a full
end-to-end eval with real model calls, run it with --live and an API key set
(you will be asked to confirm interactively).

Usage:
    python scripts/eval_multilingual.py                       # fixtures
    python scripts/eval_multilingual.py <folder>              # your documents
    python scripts/eval_multilingual.py --live <folder>       # includes model calls (paid!)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.processing.cleaning import clean_reading  # noqa: E402
from app.processing.langdetect import detect_language, looks_like_gibberish  # noqa: E402
from app.processing.processor import DocumentProcessor  # noqa: E402
from app.schemas.enums import ExtractionMode  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "multilingual"


def eval_local(path: Path, processor: DocumentProcessor) -> None:
    print(f"\n=== {path.name} ===")
    try:
        doc = processor.process(path.read_bytes(), None, path.name, ExtractionMode.STANDARD)
    except Exception as exc:
        print(f"  skipped: {exc}")
        return
    for unit in doc.units:
        lang = detect_language(unit.text)
        cleaned, report = clean_reading(unit.text)
        gibberish = looks_like_gibberish(unit.text)
        print(
            f"  {unit.kind} {unit.index}: route={unit.route.value:6s} "
            f"lang={lang.language or '?'} ({lang.script or '?'}, conf {lang.confidence:.2f}) "
            f"damage={report.damage_ratio:.3f} loop={report.loop_detected} "
            f"gibberish={gibberish}"
        )
    for w in doc.warnings:
        print(f"  warning: {w}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", default=str(FIXTURES))
    parser.add_argument("--live", action="store_true",
                        help="also run the paid model pipeline (asks for confirmation)")
    args = parser.parse_args()

    settings = get_settings()
    processor = DocumentProcessor(settings)
    root = Path(args.folder)
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix != ".md")

    print(f"Evaluating {len(files)} file(s) from {root} (local only, no model calls)")
    for path in files:
        eval_local(path, processor)

    if args.live:
        answer = input("\n--live runs REAL, PAID model calls. Type 'yes' to continue: ")
        if answer.strip().lower() != "yes":
            print("Aborted.")
            return
        from app.agents.extraction_agent import get_extraction_agent
        import uuid as uuid_mod

        agent = get_extraction_agent()
        for path in files:
            try:
                doc = processor.process(path.read_bytes(), None, path.name)
            except Exception:
                continue
            print(f"\n=== LIVE: {path.name} ===")
            output = agent.extract(doc, source_id=uuid_mod.uuid4())
            for p in output.result.pages:
                print(
                    f"  {p.kind} {p.page}: route={p.route.value} model={p.model} "
                    f"status={p.status} note={p.note}"
                )
            fallbacks = sum(1 for u in output.usage if u.status == "failed")
            print(f"  fallbacks/failed calls: {fallbacks}")


if __name__ == "__main__":
    main()
