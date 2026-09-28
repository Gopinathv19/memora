"""The reranking stage: a dedicated cross-encoder, independent of embeddings.

Stage 3 of the retrieval pipeline (strategy §4). The reranker scores every
(query, chunk) pair and re-sorts the MMR survivors; the final top 5 comes
from *this* score, never from the embedding cosine similarity.

The provider is NeMo Retriever's hosted reranking NIM. Rerank models are NOT
served from the OpenAI-compatible chat host: they live under

    POST {reranker_base_url}/{model}/reranking
    e.g. https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-nemotron-rerank-vl-1b-v2/reranking

with the request shape

    {"model": ..., "query": {"text": ...}, "passages": [{"text": ...}, ...]}

and the response carries ``rankings: [{index, logit}, ...]`` -- the logit is
the relevance score. The same NVIDIA_API_KEY authorizes it. The older
``nv-rerankqa-mistral-4b-v3`` / ``llama-3.2-nv-rerankqa-1b-*`` models are
end-of-life on the hosted API; ``llama-nemotron-rerank-vl-1b-v2`` is the
current one (it also accepts passage images, which Memora does not send).

Modular by design (strategy §4): ``RerankerProvider`` is the protocol the
pipeline depends on; swapping in a different reranker means implementing it
and registering it in ``get_reranker`` -- nothing else changes.
"""

import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from app.core.config import Settings, get_settings


class RerankerError(Exception):
    """The reranking call failed."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class RerankResult:
    """One scored passage: the candidate's position in the input list and
    its relevance score (the reranker's logit)."""

    index: int
    score: float


class RerankerProvider(Protocol):
    """What a reranking backend must do. Nothing about storage or retrieval."""

    model: str

    def rerank(self, query: str, passages: list[str]) -> list[RerankResult]:
        """Score every passage against the query. Returns one result per
        passage, sorted by relevance (highest first)."""
        ...


class NIMReranker:
    """NeMo Retriever hosted reranking NIM via POST {base}/{model}/reranking."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.reranker_model
        self._client = None
        self._lock = threading.Lock()

    def _sdk(self):
        # Lazy like every other provider client: a missing key fails the
        # query that needs it, not app startup.
        if self._client is None:
            with self._lock:
                if self._client is None:
                    if not self.settings.nvidia_api_key:
                        raise RerankerError(
                            "NVIDIA_API_KEY is not set; add it to backend/.env"
                        )
                    import httpx

                    self._client = httpx.Client(
                        base_url=self.settings.reranker_base_url,
                        headers={"Authorization": f"Bearer {self.settings.nvidia_api_key}"},
                        timeout=self.settings.reranker_timeout_seconds,
                    )
        return self._client

    def rerank(self, query: str, passages: list[str]) -> list[RerankResult]:
        if not passages:
            return []

        limit = self.settings.reranker_max_passage_chars
        payload = {
            "model": self.model,
            # The retrieval API takes the query as an object, not a string.
            "query": {"text": query},
            "passages": [
                {"text": p[:limit]} for p in passages
            ],
        }

        # The path carries the model id (minus the "nvidia/" owner prefix --
        # the base URL already ends in /nvidia):
        #   {base}/{model}/reranking
        model_path = self.model.split("/", 1)[-1]

        last_error: str | None = None
        for attempt in range(self.settings.reranker_max_retries):
            try:
                response = self._sdk().post(f"/{model_path}/reranking", json=payload)
                response.raise_for_status()
                data = response.json()
                rankings = data.get("rankings") or []
                results = [
                    RerankResult(index=int(r["index"]), score=float(r["logit"]))
                    for r in rankings
                ]
                # Defensive: some NIM builds omit passages that scored NaN;
                # give every missing candidate the worst score so the final
                # selection still has the full pool to choose from.
                if len(results) != len(passages):
                    seen = {r.index for r in results}
                    floor = min((r.score for r in results), default=0.0) - 1.0
                    results += [
                        RerankResult(index=i, score=floor)
                        for i in range(len(passages))
                        if i not in seen
                    ]
                return sorted(results, key=lambda r: r.score, reverse=True)
            except Exception as exc:  # httpx raises many shapes; normalize
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(0.5 * (attempt + 1))

        raise RerankerError(f"reranking failed after retries: {last_error}")


@lru_cache
def get_reranker() -> RerankerProvider:
    """Resolve the configured reranker. New providers register here."""
    settings = get_settings()
    return NIMReranker(settings)


def rerank(query: str, passages: list[str]) -> list[RerankResult]:
    """Convenience wrapper: the configured reranker, one call."""
    return get_reranker().rerank(query, passages)
