"""Strategy resolution: how a chunk's text becomes the provider's input.

The strategy owns everything that determines the vector representation:

* which chunk field is the input (`embedding_text` -- owned by chunking)
* the document and query templates (identity for Nemotron; a future model may
  need "query: ..." / "passage: ..." prefixes)
* normalization (L2) and the similarity metric (cosine)

This module is pure: no DB, no provider. The service layer loads a strategy
row, wraps it in `EmbeddingStrategyConfig`, and calls these helpers.
"""

import hashlib
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingStrategyConfig:
    """The strategy's vector-relevant configuration, resolved from the DB row.

    `model_identifier` and `model_version` participate in the input hash so a
    model change invalidates embeddings even when the text did not change.
    """

    strategy_id: str
    name: str
    model_identifier: str
    model_version: str | None
    dimension: int
    normalization: str
    similarity_metric: str
    document_template: str
    query_template: str
    input_type: str


def build_document_input(strategy: EmbeddingStrategyConfig, embedding_text: str) -> str:
    """Apply the strategy's document template to a chunk's embedding_text.

    The canonical input is `retrieval_chunks.embedding_text` -- the chunking
    subsystem already folded the section context into it. This function never
    re-derives chunk formatting; it only applies the strategy's template.
    """
    return strategy.document_template.format(input=embedding_text)


def build_query_input(strategy: EmbeddingStrategyConfig, query: str) -> str:
    """Apply the strategy's query template to a user query."""
    return strategy.query_template.format(input=query)


def compute_input_hash(
    strategy: EmbeddingStrategyConfig, rendered_input: str
) -> str:
    """The idempotency key: strategy + model + the exact text sent to the model.

    Re-running the worker over unchanged content produces the same hash and
    is a no-op; changed content, a changed strategy or a changed model version
    produces a new hash, and therefore a new embedding row.
    """
    material = "|".join(
        [
            strategy.strategy_id,
            strategy.model_identifier,
            strategy.model_version or "",
            strategy.normalization,
            strategy.document_template,
            rendered_input,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def l2_normalize(vector: list[float]) -> list[float]:
    """v / ||v||. A zero vector is returned unchanged (it has no direction)."""
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        return vector
    return [x / norm for x in vector]


def validate_dimension(vector: list[float], expected: int) -> None:
    """Fail loudly on a dimension mismatch. Never persist a corrupt vector."""
    if len(vector) != expected:
        raise ValueError(
            f"provider returned a {len(vector)}-dimensional vector; "
            f"strategy expects {expected}"
        )
