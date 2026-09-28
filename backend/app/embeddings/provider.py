"""The one place Memora talks to an embedding provider.

`EmbeddingProvider` is the protocol the embedding service depends on; the
concrete implementation is an OpenAI-compatible embeddings client pointed at a
configurable base URL. That one shape serves:

* build.nvidia.com (development default)
* a Nebius-hosted NVIDIA NIM (production/hackathon)
* a local NIM container

because all three expose the same `/v1/embeddings` API. Switching between
them is a settings change (`nvidia_nim_base_url`), never a code change.

Nothing outside `app/embeddings` imports the `openai` SDK for embeddings.
"""

import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from app.core.config import Settings, get_settings


class EmbeddingProviderError(Exception):
    """An embedding call failed. `usage` holds whatever the provider billed."""

    def __init__(self, message: str, usage: "EmbeddingUsage | None" = None):
        super().__init__(message)
        self.message = message
        self.usage = usage


@dataclass
class EmbeddingUsage:
    prompt_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0


class EmbeddingProvider(Protocol):
    """What an embedding backend must do. Nothing about storage or retrieval."""

    provider: str

    def embed_documents(
        self, model: str, texts: list[str], dimensions: int
    ) -> tuple[list[list[float]], EmbeddingUsage]:
        """Embed a batch of document texts. Returns one vector per text, in order."""
        ...

    def embed_query(self, model: str, text: str, dimensions: int) -> tuple[list[float], EmbeddingUsage]:
        """Embed a single query text."""
        ...


class NIMEmbeddingProvider:
    """OpenAI-compatible embeddings client for NVIDIA NIM endpoints.

    The SDK retries 429/5xx with exponential backoff (max_retries from
    settings); the worker adds its own bounded attempt counting on top for
    anything that still fails.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.embedding_provider
        self._client = None
        self._lock = threading.Lock()

    def _sdk(self):
        # Lazy like NvidiaLLMClient: a missing key fails the run that needs it,
        # not app startup or unrelated requests.
        if self._client is None:
            with self._lock:
                if self._client is None:
                    if not self.settings.nvidia_api_key:
                        raise EmbeddingProviderError(
                            "NVIDIA_API_KEY is not set; add it to backend/.env"
                        )
                    import openai

                    self._client = openai.OpenAI(
                        base_url=self.settings.nvidia_nim_base_url,
                        api_key=self.settings.nvidia_api_key,
                        timeout=self.settings.llm_timeout_seconds,
                        max_retries=self.settings.embedding_max_retries,
                    )
        return self._client

    def _embed(self, model: str, texts: list[str], dimensions: int) -> tuple[list[list[float]], EmbeddingUsage]:
        import openai

        started = time.monotonic()
        try:
            response = self._sdk().embeddings.create(
                model=model,
                input=texts,
                # Some NIM deployments accept `dimensions`; some reject it. It
                # is optional, so shed it on a 400 like the LLM client does.
                dimensions=dimensions,
            )
        except openai.BadRequestError as exc:
            # Retry once without `dimensions` -- the model's native dimension
            # is what the strategy declares anyway.
            try:
                response = self._sdk().embeddings.create(model=model, input=texts)
            except openai.APIError as inner:
                raise EmbeddingProviderError(
                    f"provider rejected the embedding request: {inner}"
                ) from inner
        except openai.APIError as exc:
            raise EmbeddingProviderError(f"{type(exc).__name__}: {exc}") from exc

        usage = getattr(response, "usage", None)
        vectors = [item.embedding for item in response.data]
        return vectors, EmbeddingUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    def embed_documents(
        self, model: str, texts: list[str], dimensions: int
    ) -> tuple[list[list[float]], EmbeddingUsage]:
        if not texts:
            return [], EmbeddingUsage()
        return self._embed(model, texts, dimensions)

    def embed_query(self, model: str, text: str, dimensions: int) -> tuple[list[float], EmbeddingUsage]:
        vectors, usage = self._embed(model, [text], dimensions)
        return vectors[0], usage


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    """Resolve the configured provider. New providers register here."""
    settings = get_settings()
    if settings.embedding_provider != "nvidia_nim":
        raise EmbeddingProviderError(
            f"Unknown embedding provider {settings.embedding_provider!r}; "
            "supported: nvidia_nim"
        )
    return NIMEmbeddingProvider(settings)
