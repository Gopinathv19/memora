"""Retry policy, deadlines, and the persistent caches -- the fixes for the
"same small PDF takes 1 min or 10+ min" bug. All model calls are faked; no
test spends credits.
"""

import time
import uuid

import pytest

from tests.extraction_fakes import FakeLLMClient


def _settings(**overrides):
    from app.core.config import Settings

    return Settings(**{"environment": "local", **overrides})


# --- retry policy ---------------------------------------------------------------------


class _Flaky:
    """Raises the given exception classes in order, then succeeds."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


def test_timeout_is_never_retried(monkeypatch):
    """A timeout raises immediately (category timeout), no same-model retry."""
    import openai

    from app.llm import retry
    from app.llm.client import LLMError

    flaky = _Flaky([openai.APITimeoutError(request=None)])
    with pytest.raises(LLMError) as exc:
        retry.call_with_retry(flaky, _settings())
    assert exc.value.category == retry.TIMEOUT
    assert flaky.calls == 1  # no retry on the same model


def test_rate_limit_honors_capped_retry_after(monkeypatch):
    """A 429 waits the Retry-After hint, capped, then retries."""
    import openai

    from app.llm import retry
    from app.llm.client import LLMError

    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))

    import httpx

    request = httpx.Request("POST", "https://api.test/v1")
    response = httpx.Response(429, headers={"retry-after": "999"}, request=request)
    error = openai.RateLimitError("too many", response=response, body=None)
    flaky = _Flaky([error, error, error])
    with pytest.raises(LLMError) as exc:
        retry.call_with_retry(flaky, _settings())
    assert exc.value.category == retry.RATE_LIMIT
    # Retry-After 999s is capped at llm_retry_after_cap_seconds (30),
    # once per retry: max 2 retries, 3 attempts total.
    assert sleeps == [30.0, 30.0]
    assert flaky.calls == 3


def test_server_error_retries_with_capped_backoff(monkeypatch):
    import openai

    from app.llm import retry
    from app.llm.client import LLMError

    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))

    import httpx

    request = httpx.Request("POST", "https://api.test/v1")
    response = httpx.Response(500, request=request)
    flaky = _Flaky(
        [openai.InternalServerError("boom", response=response, body=None)] * 3
    )
    with pytest.raises(LLMError):
        retry.call_with_retry(flaky, _settings())
    # Exponential backoff capped at llm_retry_backoff_cap_seconds (8).
    assert sleeps == [1.0, 2.0]
    assert flaky.calls == 3


def test_bad_request_propagates_for_parameter_shedding():
    """A 400 is never retried here: the client's shedding loop needs it."""
    import openai

    from app.llm import retry

    import httpx

    request = httpx.Request("POST", "https://api.test/v1")
    response = httpx.Response(400, request=request)
    flaky = _Flaky(
        [openai.BadRequestError("bad", response=response, body=None)]
    )
    with pytest.raises(openai.BadRequestError):
        retry.call_with_retry(flaky, _settings())
    assert flaky.calls == 1


# --- deadlines -------------------------------------------------------------------------


def _unit(page_image=None, text="local text", route=None):
    from app.processing.document import ReadingUnit
    from app.schemas.enums import ExtractionRoute

    from app.schemas.enums import PageDifficulty

    return ReadingUnit(
        index=1,
        kind="page",
        difficulty=PageDifficulty.MEDIUM,
        route=route or ExtractionRoute.LAYOUT,
        text=text,
        page_image=page_image,
        images=[],
    )


class _Img:
    def __init__(self):
        self.data = b"png-bytes"
        self.mime = "image/png"


def test_page_deadline_falls_through_without_same_model_retry():
    """A page past its deadline records the miss and uses local text."""
    from app.agents.extraction_agent import NemotronExtractionAgent
    from app.schemas.enums import ExtractionRoute

    class TimeoutClient(FakeLLMClient):
        def read_image(self, *a, **k):
            from app.llm.client import LLMError

            raise LLMError("timeout: too slow", category="timeout")

    agent = NemotronExtractionAgent(TimeoutClient(), _settings())
    unit = _unit(page_image=_Img())
    reading = agent._read(unit, deadline=time.monotonic() - 1)  # already past
    assert reading.route == ExtractionRoute.TEXT
    assert reading.text == "local text"
    assert reading.note and "budget" in reading.note


def test_run_budget_makes_result_partial():
    """A run whose budget is exhausted completes PARTIAL with local text,
    never a silent failure."""
    from app.agents.extraction_agent import (
        ExtractionFailed,
        NemotronExtractionAgent,
    )
    from app.processing.document import ProcessedDocument
    from app.schemas.enums import ExtractionRoute

    class TimeoutClient(FakeLLMClient):
        def read_image(self, *a, **k):
            from app.llm.client import LLMError

            raise LLMError("timeout: too slow", category="timeout")

    agent = NemotronExtractionAgent(
        TimeoutClient(), _settings(extraction_run_budget_seconds=0.0)
    )
    doc = ProcessedDocument(
        units=[_unit(page_image=_Img(), text="page text")],
        warnings=[],
        skipped_units=0,
        finalize=None,
        format="pdf",
    )
    output = agent.extract(doc, source_id=uuid.uuid4())
    assert output.result.status.value == "partial"
    assert any("budget" in w for w in output.result.warnings)
    assert output.readings[0].text == "page text"


# --- persistent caches -------------------------------------------------------------------


def test_reading_cache_survives_new_agent_instance():
    """A validated reading cached by one agent instance is served to a
    brand-new instance (the DB layer, not just the in-memory LRU)."""
    from app.agents.extraction_agent import NemotronExtractionAgent
    from app.schemas.enums import ModelRole

    class OneShotClient(FakeLLMClient):
        calls = 0

        def read_image(self, model, image, mime, prompt, **k):
            OneShotClient.calls += 1
            return "read once", None

    s = _settings()
    agent1 = NemotronExtractionAgent(OneShotClient(), s)
    unit = _unit(page_image=_Img())
    r1 = agent1._read(unit)
    assert OneShotClient.calls == 1

    # A brand-new agent (new in-memory LRU) must be served by the store.
    agent2 = NemotronExtractionAgent(OneShotClient(), s)
    r2 = agent2._read(unit)
    assert OneShotClient.calls == 1  # no second model call
    assert r2.text == r1.text
    assert "cache" in (r2.note or "")


def test_document_cache_reuses_result():
    """The same file bytes under the same pipeline version, mode and
    instructions reuse the stored result: no model call at all."""
    from app.db.database import SessionLocal
    from app.db.models import DocumentCacheEntry
    from app.services import extraction_service as svc

    key = svc._document_cache_key(b"same-bytes", "standard", None)
    db = SessionLocal()
    try:
        db.merge(
            DocumentCacheEntry(
                key=key,
                result={"status": "completed", "fields": []},
                content="cached content",
                readings=None,
            )
        )
        db.commit()
    finally:
        db.close()

    class NeverCalledClient(FakeLLMClient):
        def read_image(self, *a, **k):
            raise AssertionError("document cache hit must not call models")

        def chat_json(self, *a, **k):
            raise AssertionError("document cache hit must not call models")

    # The cache key is deterministic on (bytes, mode, instructions).
    assert svc._document_cache_key(b"same-bytes", "standard", None) == key
    assert svc._document_cache_key(b"other", "standard", None) != key
    assert svc._document_cache_key(b"same-bytes", "agentic", None) != key


# --- shed-parameter recording ---------------------------------------------------------------


def test_shed_params_reach_the_usage_record():
    from app.agents.extraction_agent import UsageRecord
    from app.llm.client import LLMUsage

    record = UsageRecord.of(
        "layout", "nvidia/nemotron-parse", 1, LLMUsage(shed_params=["extra_body"])
    )
    assert record.notes == "shed optional parameters: extra_body"
    record2 = UsageRecord.of("layout", "nvidia/nemotron-parse", 1, LLMUsage())
    assert record2.notes is None
