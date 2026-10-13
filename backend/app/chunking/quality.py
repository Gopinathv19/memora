"""Quality gate and dedupe for retrieval chunks, before embedding.

A chunk that is too short, mostly repeated tokens, highly compressible (a
model loop), or still carrying junk tokens (`<unk>`, U+FFFD) pollutes the
vector index. `gate_chunk()` flags or rejects such chunks; `dedupe_chunks()`
drops exact and near-duplicate chunks within a document; `dedupe_sentences()`
drops sentences already seen earlier in the same chunk. All local and free.
"""

import hashlib
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.chunking.chunker import ChunkData
from app.core.config import Settings, get_settings

_JUNK = re.compile(r"<unk>|<pad>|</s>|\ufffd|\(cid:\d+\)")


@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def _tokens(text: str) -> list[str]:
    return text.lower().split()


def _normalized(text: str) -> str:
    return " ".join(_tokens(text))


def gate_chunk(chunk: ChunkData, settings: Settings | None = None) -> GateResult:
    """Quality gate before embedding. `ok=False` chunks are dropped or flagged."""
    s = settings or get_settings()
    reasons: list[str] = []
    text = chunk.content.strip()

    if len(text) < s.chunk_min_chars:
        reasons.append(f"too short ({len(text)} < {s.chunk_min_chars} chars)")
    if _JUNK.search(text):
        reasons.append("contains junk tokens")

    tokens = _tokens(text)
    if tokens:
        unique_ratio = len(set(tokens)) / len(tokens)
        if unique_ratio < s.chunk_min_unique_token_ratio:
            reasons.append(f"low unique-token ratio ({unique_ratio:.2f})")
        # A model loop compresses extremely well; real prose does not.
        import zlib

        data = text.encode("utf-8")
        if data and len(zlib.compress(data, 6)) / len(data) < s.chunk_min_compression_ratio:
            reasons.append("abnormally low compression ratio (likely a loop)")

    return GateResult(ok=not reasons, reasons=reasons)


def dedupe_sentences(text: str) -> str:
    """Drop sentences already seen earlier in the same chunk."""
    sentences = re.split(r"(?<=[.!?।])\s+", text)
    seen: set[str] = set()
    out: list[str] = []
    for sentence in sentences:
        key = _normalized(sentence)
        if len(key) > 10 and key in seen:  # ignore short/empty fragments
            continue
        if key:
            seen.add(key)
        out.append(sentence)
    return " ".join(out).strip()


def _near_duplicate(a: str, b: str, threshold: float) -> bool:
    """Sequence similarity >= threshold (cheap ratio first, full as needed)."""
    if not a or not b:
        return False
    matcher = SequenceMatcher(None, a, b)
    if matcher.quick_ratio() < threshold:
        return False
    return matcher.ratio() >= threshold


def dedupe_chunks(
    chunks: list[ChunkData],
    settings: Settings | None = None,
    dropped: list[str] | None = None,
) -> list[ChunkData]:
    """Remove exact (normalized-hash) and near-duplicate chunks in a document.

    The first occurrence wins; later duplicates are dropped. `dropped`, when
    given, collects a human-readable note per dropped chunk.
    """
    s = settings or get_settings()
    if not s.chunk_dedupe_enabled:
        return chunks
    seen_hashes: set[str] = set()
    kept: list[ChunkData] = []
    kept_norms: list[str] = []
    for chunk in chunks:
        norm = _normalized(chunk.content)
        h = hashlib.sha256(norm.encode("utf-8")).hexdigest()
        if h in seen_hashes:
            if dropped is not None:
                dropped.append(f"exact duplicate chunk dropped ({norm[:60]}…)")
            continue
        if any(
            _near_duplicate(norm, other, s.chunk_near_dup_threshold)
            for other in kept_norms
        ):
            if dropped is not None:
                dropped.append(f"near-duplicate chunk dropped ({norm[:60]}…)")
            continue
        seen_hashes.add(h)
        kept_norms.append(norm)
        kept.append(chunk)
    return kept


def apply_quality(
    chunks: list[ChunkData],
    settings: Settings | None = None,
    warnings: list[str] | None = None,
) -> list[ChunkData]:
    """The full Phase 5 pipeline: in-chunk sentence dedupe, in-document chunk
    dedupe, then the quality gate. Rejected chunks are reported, not silent."""
    s = settings or get_settings()
    notes = warnings if warnings is not None else []
    for chunk in chunks:
        chunk.content = dedupe_sentences(chunk.content)
    chunks = [c for c in chunks if c.content.strip()]
    chunks = dedupe_chunks(chunks, s, notes)
    kept: list[ChunkData] = []
    for chunk in chunks:
        gate = gate_chunk(chunk, s)
        if gate.ok:
            kept.append(chunk)
        else:
            notes.append(f"chunk rejected: {'; '.join(gate.reasons)}")
    return kept
