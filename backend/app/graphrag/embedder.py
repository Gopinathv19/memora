"""Embedding for the graph layer, under the same strategy the chunks use.

Entities, facts and community summaries are embedded with the *active*
embedding strategy (same model, dimension and normalization as
`chunk_embeddings`), using its document template for stored text and its query
template for questions, so graph and chunk vectors live in one space.
"""

from app.embeddings.provider import get_embedding_provider
from app.embeddings.strategy import (
    EmbeddingStrategyConfig,
    build_document_input,
    l2_normalize,
    validate_dimension,
)
from app.core.config import get_settings
from app.services import embedding_service


def embed_query(cfg: EmbeddingStrategyConfig, text: str) -> list[float]:
    vector, _, _ = embedding_service.embed_query_with_config(cfg, text)
    return vector


def embed_documents(cfg: EmbeddingStrategyConfig, texts: list[str]) -> tuple[list[list[float]], int]:
    """Embed stored texts in batches. Returns (vectors in order, tokens used)."""
    if not texts:
        return [], 0
    provider = get_embedding_provider()
    batch = max(1, get_settings().embedding_batch_size)
    vectors: list[list[float]] = []
    tokens = 0
    for start in range(0, len(texts), batch):
        inputs = [build_document_input(cfg, t) for t in texts[start : start + batch]]
        out, usage = provider.embed_documents(cfg.model_identifier, inputs, cfg.dimension)
        tokens += usage.total_tokens
        for vector in out:
            validate_dimension(vector, cfg.dimension)
            vectors.append(l2_normalize(vector) if cfg.normalization == "l2" else vector)
    return vectors, tokens
