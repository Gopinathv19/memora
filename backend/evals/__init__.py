"""Memora's evaluation package.

Everything here is separate from the production pipeline: no file in
``app/``, ``migrations/`` or ``tests/`` is modified by evaluation code, and
the database is only ever read (SELECT only).

Layout:

* ``schemas``    -- the eval-example schema and validation
* ``dataset``    -- JSONL loading, coverage, checksums
* ``metrics``    -- pure metric functions (retrieval, context, answer,
                    grounding, abstention, operational)
* ``adapter``    -- runs the real pipeline stages for one query
* ``reports``    -- JSON + Markdown report writing
* ``runner``     -- the CLI (``python -m evals.runner ...``)
* ``chunking_check`` -- wraps scripts/verify_chunking.py

``scripts/`` keeps the standalone generators/runners that predate this
package; they remain runnable and are gradually refactored to share code
with it.
"""
