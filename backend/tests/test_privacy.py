"""Phase-1 privacy hardening: log redaction, production checks, temp sweep.

These tests are deliberately DB-free: every check here is a pure function or
a logging filter, so the suite runs anywhere.
"""

import logging
import os
import tempfile
import time

import pytest

from app.connectors.google_drive.rag_adapter import sweep_stale_temp_files
from app.core.logging import RedactQueryParamsFilter
from app.retrieval.answer import query_fingerprint


def _access_record(path: str) -> logging.LogRecord:
    """The shape uvicorn's access formatter expects: args build the line."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:51000", "GET", path, "1.1", 200),
        exc_info=None,
    )


class TestRedactQueryParamsFilter:
    def test_oauth_callback_code_and_state_are_redacted(self):
        record = _access_record(
            "/api/v1/gdrive/callback?code=SECRET_CODE&state=SECRET_STATE"
        )
        RedactQueryParamsFilter().filter(record)
        line = record.getMessage()
        assert "SECRET_CODE" not in line
        assert "SECRET_STATE" not in line
        assert "code=[REDACTED]" in line
        assert "state=[REDACTED]" in line

    def test_token_shaped_params_are_redacted_anywhere(self):
        record = _access_record("/api/v1/anything?access_token=abc123&x=1")
        RedactQueryParamsFilter().filter(record)
        line = record.getMessage()
        assert "abc123" not in line
        assert "x=1" in line

    def test_benign_query_params_survive(self):
        record = _access_record("/api/v1/sources?status=pending&limit=10")
        RedactQueryParamsFilter().filter(record)
        line = record.getMessage()
        assert "status=pending" in line
        assert "limit=10" in line


class TestTempSweep:
    def test_old_files_removed_fresh_kept(self):
        tmpdir = tempfile.gettempdir()
        old = os.path.join(tmpdir, f"gdrive-old-{os.getpid()}.pdf")
        fresh = os.path.join(tmpdir, f"gdrive-fresh-{os.getpid()}.pdf")
        try:
            for path in (old, fresh):
                with open(path, "wb") as fh:
                    fh.write(b"%PDF-fake")
            ancient = time.time() - 7 * 3600
            os.utime(old, (ancient, ancient))

            removed = sweep_stale_temp_files()

            assert removed >= 1
            assert not os.path.exists(old)
            assert os.path.exists(fresh)
        finally:
            for path in (old, fresh):
                if os.path.exists(path):
                    os.unlink(path)


class TestQueryFingerprint:
    def test_query_text_never_appears(self):
        query = "what is the secret salary of the CEO?"
        fingerprint = query_fingerprint(query)
        assert "secret" not in fingerprint
        assert "salary" not in fingerprint
        assert f"query_len={len(query)}" in fingerprint

    def test_same_query_same_fingerprint(self):
        assert query_fingerprint("hello") == query_fingerprint("hello")
        assert query_fingerprint("hello") != query_fingerprint("goodbye")
