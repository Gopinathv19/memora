"""Chunking: readings → DocumentUnits → SemanticBlocks → RetrievalChunks.

Pure and local: no LLM calls, no database. The service layer
(`app/services/chunking_service.py`) is the only thing that persists the
output of this package. See docs/chunking.md.
"""

from app.chunking.blocks import build_semantic_blocks, merge_heading_only_blocks
from app.chunking.chunker import chunk_semantic_block
from app.chunking.representation import build_embedding_text, count_tokens
from app.chunking.units import parse_readings

__all__ = [
    "build_embedding_text",
    "build_semantic_blocks",
    "chunk_semantic_block",
    "count_tokens",
    "merge_heading_only_blocks",
    "parse_readings",
]

