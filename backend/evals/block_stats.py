"""Size statistics of semantic blocks: input for the unified-chunking decision (plan Step 1).

Step 1 builds the graph from `SemanticBlock`s instead of separate 3000-character
units. Whether that works depends on how big blocks are: a block far above the
extraction model's comfortable input must be split at chunk boundaries, a block
far below it wastes an LLM call. Pure functions; `scripts/measure_blocks.py` loads rows.

Tokens use the repository's own heuristic (`app.chunking.representation.count_tokens`,
about 4 characters per token), so numbers match how chunks are sized.
"""

from __future__ import annotations

from app.chunking.representation import count_tokens

# Bucket edges in tokens. 750 tokens is today's graph unit (3000 characters).
BUCKETS = (100, 400, 750, 1200, 3000)


def percentile(sorted_values: list[int], p: float) -> int | None:
    if not sorted_values:
        return None
    return sorted_values[min(len(sorted_values) - 1, int(p * len(sorted_values)))]


def bucket_label(tokens: int) -> str:
    prev = 0
    for edge in BUCKETS:
        if tokens < edge:
            return f"{prev}-{edge}"
        prev = edge
    return f"{BUCKETS[-1]}+"


def summarize_blocks(blocks: list[dict], *, small: int = 100, large: int = 3000) -> dict:
    """`blocks`: dicts with `content`, `section_path`, `chunks` (list of chunk token counts),
    `has_table` (bool), optionally `filename` and `title`. `small` / `large` are the
    token limits the report counts blocks beyond; they are working assumptions, not facts.
    """
    tokens = sorted(count_tokens(b["content"]) for b in blocks)
    sized = [(count_tokens(b["content"]), b) for b in blocks]
    chunk_counts = sorted(len(b["chunks"]) for b in blocks)
    buckets: dict[str, int] = {}
    for t in tokens:
        buckets[bucket_label(t)] = buckets.get(bucket_label(t), 0) + 1
    n = len(blocks)
    return {
        "blocks": n,
        "tokens": {
            "min": tokens[0] if tokens else None, "p10": percentile(tokens, 0.10),
            "p50": percentile(tokens, 0.50), "p90": percentile(tokens, 0.90),
            "p99": percentile(tokens, 0.99), "max": tokens[-1] if tokens else None,
            "total": sum(tokens),
        },
        "buckets": dict(sorted(buckets.items(), key=lambda kv: int(kv[0].split("-")[0].rstrip("+")))),
        "below_small": sum(t < small for t in tokens),
        "above_large": sum(t > large for t in tokens),
        "chunks_per_block": {
            "p50": percentile(chunk_counts, 0.5), "p90": percentile(chunk_counts, 0.9),
            "max": chunk_counts[-1] if chunk_counts else None,
            "single_chunk_share": round(sum(c == 1 for c in chunk_counts) / n, 4) if n else None,
        },
        "table_share": round(sum(bool(b.get("has_table")) for b in blocks) / n, 4) if n else None,
        "no_section_path_share": round(sum(not b.get("section_path") for b in blocks) / n, 4) if n else None,
        "largest": [
            {"tokens": t, "file": b.get("filename"), "title": b.get("title")}
            for t, b in sorted(sized, key=lambda x: -x[0])[:5]
        ],
        "limits": {"small": small, "large": large},
    }
