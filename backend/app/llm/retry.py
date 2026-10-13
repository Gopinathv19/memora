"""Memora's own retry policy for model calls.

The OpenAI SDK's `max_retries` is set to 0 everywhere (see
`llm_sdk_max_retries`): the SDK's exponential backoff multiplies with the
agent's own per-page fallback chain, which is how a small PDF could stack
3 SDK retries x 2 layout attempts x 2 vision attempts x 120s timeouts into
a 10+ minute run. This module is the single place retrying happens:

* timeouts        -- never retried on the same model; raised immediately so
                     the agent falls through to the next model in the chain.
* 429             -- retried, honoring the provider's Retry-After header,
                     capped at `llm_retry_after_cap_seconds`.
* 5xx / connection -- retried with capped exponential backoff.
* 400 / other 4xx -- never retried (parameter shedding happens in the
                     client's `_create`).

Every knob is a setting, so tuning is a config change, never a code change.
"""

import time

# Error categories carried on LLMError so callers can decide (e.g. the
# agent skips a same-model retry when the category is "timeout").
TIMEOUT = "timeout"
RATE_LIMIT = "rate_limit"
SERVER_ERROR = "server_error"
CLIENT_ERROR = "client_error"


def _retry_after_seconds(exc, cap: float) -> float | None:
    """The provider's Retry-After hint, capped; None when absent/unparsable."""
    try:
        raw = exc.response.headers.get("retry-after")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return max(0.0, min(float(raw), cap))
    except ValueError:
        return None  # HTTP-date form is not honored; fall back to backoff


def call_with_retry(fn, settings):
    """Call `fn()` with Memora's retry policy. Raises LLMError on failure.

    `fn` must be the raw SDK call (no try/except of its own). Anything the
    wrapper does not handle -- including `openai.BadRequestError`, which the
    client's parameter-shedding loop needs to see -- propagates untouched.
    """
    from app.llm.client import LLMError

    max_retries = settings.llm_retry_max_attempts
    backoff_cap = settings.llm_retry_backoff_cap_seconds
    retry_after_cap = settings.llm_retry_after_cap_seconds

    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            import openai

            if isinstance(exc, openai.BadRequestError):
                raise  # parameter shedding handles 400s; never retried here
            if isinstance(exc, openai.APITimeoutError):
                # No same-model retry: raise now so the caller falls through
                # to the next model in its fallback chain.
                raise LLMError(f"timeout: {exc}", category=TIMEOUT) from exc
            if isinstance(exc, openai.RateLimitError):
                category = RATE_LIMIT
                wait = _retry_after_seconds(exc, retry_after_cap)
                if wait is None:
                    wait = min(2.0**attempt, backoff_cap)
            elif isinstance(exc, (openai.InternalServerError, openai.APIConnectionError)):
                category = SERVER_ERROR
                wait = min(2.0**attempt, backoff_cap)
            elif isinstance(exc, openai.APIStatusError) and exc.status >= 500:
                category = SERVER_ERROR
                wait = min(2.0**attempt, backoff_cap)
            elif isinstance(exc, openai.APIError):
                raise LLMError(
                    f"{type(exc).__name__}: {exc}", category=CLIENT_ERROR
                ) from exc
            else:
                raise  # not an SDK error; the caller decides

            if attempt >= max_retries:
                raise LLMError(
                    f"{category} after {attempt + 1} attempt(s): {exc}",
                    category=category,
                ) from exc
            time.sleep(wait)
            attempt += 1