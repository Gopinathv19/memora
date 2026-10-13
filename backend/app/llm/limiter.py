"""One process-wide concurrency limiter for every model call.

The extraction agent's worker pool, the windowed-extract pool, the graph
pipeline and the embedding worker all call providers from different
threads. Without a shared cap they can stack (4 agent workers + window
workers + the embedding worker) into a burst that trips 429s and makes
latency unpredictable. One BoundedSemaphore per provider bucket caps the
in-flight calls; the embedding NIM shares the build-nvidia bucket when it
points at the same endpoint.
"""

import threading

from app.core.config import get_settings

_lock = threading.Lock()
_limiters: dict[str, threading.BoundedSemaphore] = {}


def _bucket_for(provider: str) -> str:
    """Group providers that hit the same endpoint."""
    settings = get_settings()
    if provider == "nvidia_nim" and settings.nvidia_nim_base_url.rstrip("/") == (
        settings.nvidia_base_url.rstrip("/")
    ):
        return "build-nvidia"
    return provider


def get_limiter(provider: str) -> threading.BoundedSemaphore:
    """The process-wide limiter for a provider bucket."""
    bucket = _bucket_for(provider)
    limit = get_settings().llm_max_concurrent_calls_per_provider
    with _lock:
        if bucket not in _limiters:
            _limiters[bucket] = threading.BoundedSemaphore(max(1, limit))
        return _limiters[bucket]


def limited(provider: str):
    """Context manager: acquire the provider's shared slot."""
    return get_limiter(provider)