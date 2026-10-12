"""Chunking validation, wrapped for the eval package.

``scripts/verify_chunking.py`` stays exactly as it is -- a standalone,
read-only QA report against the chunking strategy rules. This wrapper
makes it callable programmatically (``evals.runner chunking``) without
duplicating any of its logic. Chunking checks are deliberately kept
separate from retrieval and end-to-end evaluation: they validate the
chunking stage, not answer quality.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND / "scripts"


def run_chunking_check(source_id: str | None = None) -> int:
    """Run scripts/verify_chunking.py (in-process) and return its exit code."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import verify_chunking  # noqa: E402  (the standalone script)

    # The script reads its argument from sys.argv; set it, run, restore.
    argv = sys.argv
    try:
        sys.argv = ["verify_chunking.py"] + ([source_id] if source_id else [])
        verify_chunking.main()
    finally:
        sys.argv = argv
    return 0
