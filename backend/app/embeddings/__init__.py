"""The embedding subsystem: chunk -> vector, under a versioned strategy.

Layering (docs/embeddings.md):

    retrieval_chunks.embedding_text   (owned by chunking)
            |
    EmbeddingStrategy                 (how Memora uses a model)
            |
    EmbeddingProvider                 (NVIDIA NIM / Nebius / local NIM)
            |
    chunk_embeddings                   (pgvector, HNSW)

The package is deliberately provider-agnostic: services depend on the
`EmbeddingProvider` protocol, and the concrete NIM client is resolved from
settings. Adding BGE-M3 or a multimodal model later is a new provider class
plus an `embedding_models` row, not a rewrite.
"""

from app.embeddings.provider import (
    EmbeddingProvider,
    EmbeddingProviderError,
    EmbeddingUsage,
    NIMEmbeddingProvider,
    get_embedding_provider,
)
from app.embeddings.strategy import (
    EmbeddingStrategyConfig,
    build_document_input,
    build_query_input,
    compute_input_hash,
    l2_normalize,
)

__all__ = [
    "EmbeddingProvider",
    "EmbeddingProviderError",
    "EmbeddingUsage",
    "NIMEmbeddingProvider",
    "get_embedding_provider",
    "EmbeddingStrategyConfig",
    "build_document_input",
    "build_query_input",
    "compute_input_hash",
    "l2_normalize",
]
