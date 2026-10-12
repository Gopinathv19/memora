"""JSONL dataset loading, coverage reporting and checksums.

An eval dataset is one JSONL file, one example per line, in the schema
of ``evals.schemas``. Loading is strict: every invalid line is reported
with its line number and a useful reason, and the caller decides whether
to abort (``load``) or skip (``load_lenient``).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from evals.schemas import EvalExample, RecordError, from_legacy, validate_record


def checksum(path: Path) -> str:
    """SHA256 of the file, for reproducible run metadata."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def load(path: Path | str, *, legacy: bool = False) -> list[EvalExample]:
    """Load and validate every line. Raises RecordError on the first bad line.

    ``legacy=True`` maps the older evals/data schema first (see
    ``schemas.from_legacy``) so the existing reviewed dataset works
    unchanged.
    """
    path = Path(path)
    examples: list[EvalExample] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RecordError(line_no, f"invalid JSON: {exc}") from exc
            if not isinstance(raw, dict):
                raise RecordError(line_no, "record is not a JSON object")
            if legacy:
                raw = from_legacy(raw)
            examples.append(validate_record(raw, line_no))
    return examples


def load_lenient(
    path: Path | str, *, legacy: bool = False
) -> tuple[list[EvalExample], list[RecordError]]:
    """Load every valid line; collect the invalid ones with line numbers."""
    path = Path(path)
    examples: list[EvalExample] = []
    errors: list[RecordError] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise RecordError(line_no, "record is not a JSON object")
                if legacy:
                    raw = from_legacy(raw)
                examples.append(validate_record(raw, line_no))
            except RecordError as err:
                errors.append(err)
            except json.JSONDecodeError as exc:
                errors.append(RecordError(line_no, f"invalid JSON: {exc}"))
    return examples, errors


def coverage(examples: list[EvalExample]) -> dict:
    """Dataset coverage: counts by query_type / difficulty / tenant, and
    how complete the ground-truth annotations are."""
    n = len(examples)
    by_type = Counter(e.query_type for e in examples)
    by_difficulty = Counter(e.difficulty for e in examples)
    by_tenant = Counter(e.tenant_id for e in examples)
    return {
        "total": n,
        "answerable": sum(1 for e in examples if not e.is_unanswerable),
        "unanswerable": sum(1 for e in examples if e.is_unanswerable),
        "by_query_type": dict(by_type),
        "by_difficulty": dict(by_difficulty),
        "by_tenant": dict(by_tenant),
        "annotation_coverage": {
            "expected_answer": sum(1 for e in examples if e.expected_answer),
            "supporting_chunk_ids": sum(1 for e in examples if e.supporting_chunk_ids),
            "supporting_pages": sum(1 for e in examples if e.supporting_pages),
            "expected_citations": sum(1 for e in examples if e.expected_citations),
            "multi_chunk": sum(1 for e in examples if len(e.supporting_chunk_ids) > 1),
        },
    }
