"""From graph text units to the chunks the rest of Memora uses.

The knowledge graph reads each document as ~3000-character units
(`{source}:{version}:{index}`), independent of the retrieval chunks in Postgres
(`retrieval_chunks`, the unit the vector pipeline, the reranker, the eval sets
and the citations all use). To compare the two pipelines on one footing, graph
evidence is mapped onto retrieval chunks here, by **text containment**: a
retrieval chunk is covered by a graph unit to the degree its word 4-grams occur
in the unit's text. Each retrieval chunk takes the best `unit score *
containment` over the units that cover it.

Containment, not Jaccard: a 3000-character unit is bigger than most retrieval
chunks, so "how much of this chunk lies inside that unit" is the right question.
"""

import re
from dataclasses import dataclass

_WORD = re.compile(r"[^\W_]+")
SHINGLE = 4
MIN_CONTAINMENT = 0.35


def shingles(text: str, n: int = SHINGLE) -> set[tuple[str, ...]]:
    words = _WORD.findall((text or "").casefold())
    if len(words) < n:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def containment(chunk: set, unit: set) -> float:
    return len(chunk & unit) / len(chunk) if chunk else 0.0


@dataclass
class Mapped:
    chunk_id: str
    score: float
    via: list[str]  # graph unit ids that cover it


def map_units_to_chunks(
    unit_scores: dict[str, float],
    unit_texts: dict[str, str],
    unit_sources: dict[str, str],
    chunks: list[dict],
    min_containment: float = MIN_CONTAINMENT,
) -> list[Mapped]:
    """`chunks` are retrieval chunks: dicts with `id`, `source_id` and `content`.

    Returns retrieval chunks best first. A chunk belonging to another source
    than the unit never matches, whatever the text overlap.
    """
    unit_shingles = {u: shingles(t) for u, t in unit_texts.items()}
    out: list[Mapped] = []
    for chunk in chunks:
        cs = shingles(chunk["content"])
        if not cs:
            continue
        best, via = 0.0, []
        for unit, score in unit_scores.items():
            if unit_sources.get(unit) != chunk["source_id"]:
                continue
            c = containment(cs, unit_shingles.get(unit, set()))
            if c < min_containment:
                continue
            value = score * c
            if value > best:
                best = value
            via.append(unit)
        if best > 0:
            out.append(Mapped(chunk_id=str(chunk["id"]), score=best, via=via))
    out.sort(key=lambda m: (-m.score, m.chunk_id))
    return out
