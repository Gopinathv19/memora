"""Pure metric functions: no database, no LLM, no I/O.

Every function here is deterministic and unit-testable. The runner feeds
them retrieved chunk IDs (ranked), verified supporting chunk IDs, answer
texts and timings; they return numbers.

Groups:

* retrieval  -- Recall@k, MRR, nDCG@k, hit rate (ground truth =
                ``supporting_chunk_ids``)
* context    -- context precision / recall, redundancy rate
* grounding  -- citation precision (deterministic), faithfulness /
                unsupported-claim rate (judge-based, labeled as such)
* abstention -- refusal detection, refusal rate, false-answer rate
* operational-- percentiles, throughput

Denominators and edge cases are documented per function. Unanswerable
examples are excluded from retrieval-ranking metrics (they have no
relevant chunks); they are the *only* input to abstention metrics.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

# --- Retrieval metrics -----------------------------------------------------------


def recall_at_k(retrieved: list[str], relevant: set[str] | list[str], k: int) -> float | None:
    """Fraction of relevant chunks present in the first ``k`` retrieved.

    Returns None when there is no ground truth (empty ``relevant``):
    a missing annotation is NOT evidence that nothing retrieved was
    relevant, so no score is reported rather than a fake 0.
    """
    relevant = set(relevant)
    if not relevant or k <= 0:
        return None
    top = list(retrieved)[:k]
    return len(relevant & set(top)) / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str] | list[str]) -> float | None:
    """1 / rank of the first relevant result (1-based). None if no ground
    truth or nothing relevant was retrieved."""
    relevant = set(relevant)
    if not relevant:
        return None
    for rank, cid in enumerate(retrieved, start=1):
        if cid in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: set[str] | list[str], k: int) -> float | None:
    """nDCG@k with binary relevance (graded labels are not part of the
    schema yet; when they are, pass them in as relevance grades).

    DCG  = sum(rel_i / log2(i + 1)) for i = 1..k (1-based rank)
    IDCG = DCG of the ideal ordering (all relevant chunks first).
    """
    relevant = set(relevant)
    if not relevant or k <= 0:
        return None
    top = list(retrieved)[:k]
    dcg = sum(1.0 / math.log2(i + 1) for i, cid in enumerate(top, start=1) if cid in relevant)
    ideal = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal + 1))
    return dcg / idcg if idcg > 0 else None


def hit_rate(retrieved: list[str], relevant: set[str] | list[str]) -> float:
    """1 if at least one relevant chunk was retrieved, else 0.

    Unlike recall/MRR/nDCG this is defined for empty ground truth too
    (it is then 0): "did the system find anything relevant" is a
    meaningful question even when the annotation set is partial. For
    unanswerable examples the expected value is 0 by definition.
    """
    relevant = set(relevant)
    return 1.0 if relevant and (relevant & set(retrieved)) else 0.0


# --- Context metrics ---------------------------------------------------------------


def context_precision(retrieved: list[str], relevant: set[str] | list[str]) -> float | None:
    """How much of the retrieved context is relevant:

        |retrieved ∩ relevant| / |retrieved|

    None when nothing was retrieved (no context to judge). Chunk-ID based:
    this measures *identity* precision, not semantic precision -- a
    paraphrase of a relevant chunk scores 0 here. Documented limitation.
    """
    relevant = set(relevant)
    if not retrieved:
        return None
    return len(set(retrieved) & relevant) / len(set(retrieved))


def context_recall(retrieved: list[str], relevant: set[str] | list[str]) -> float | None:
    """How much of the known supporting evidence was covered:

        |retrieved ∩ relevant| / |relevant|

    None when there is no ground truth (same rule as recall@k).
    """
    relevant = set(relevant)
    if not relevant:
        return None
    return len(set(retrieved) & relevant) / len(relevant)


def _word_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").casefold()))


def redundancy_rate(texts: list[str], threshold: float = 0.85) -> float | None:
    """Fraction of retrieved chunks that are near-duplicates of an earlier
    chunk in the list.

    Method (documented approximation): a chunk is redundant when its
    Jaccard word-set similarity to any *earlier* chunk in the retrieved
    list exceeds ``threshold``. Lexical, not semantic: a paraphrase is
    not caught. None when fewer than two chunks were retrieved.
    """
    if len(texts) < 2:
        return None
    seen: list[set[str]] = []
    redundant = 0
    for text in texts:
        words = _word_set(text)
        if any(words and other and len(words & other) / len(words | other) > threshold
               for other in seen):
            redundant += 1
        else:
            seen.append(words)
    return redundant / len(texts)


# --- Grounding: deterministic part ---------------------------------------------------


def citation_precision(
    cited: list[str], retrieved: list[str], expected: list[str] | None = None
) -> float | None:
    """Fraction of the answer's citations that point at actually-retrieved
    chunks. ``cited`` are the chunk IDs the answer cites; ``retrieved`` are
    the chunk IDs that were in its context.

    With ``expected`` (verified citations) the metric instead measures
    how many of the expected citations the answer got right:
        |cited ∩ expected| / |expected|
    and a citation that points outside the retrieved set counts against
    it (treated as incorrect, not just missing).

    None when the answer cites nothing (missing citations are reported
    separately, not scored as 0 here).
    """
    if not cited:
        return None
    if expected is not None:
        expected = set(expected)
        if not expected:
            return None
        return len(set(cited) & expected) / len(expected)
    return len(set(cited) & set(retrieved)) / len(set(cited))


# --- Abstention ----------------------------------------------------------------------


# The pipeline's two canned no-context messages plus the wording the
# system prompt tells the model to use. Case-insensitive substring match.
REFUSAL_PATTERNS = [
    "could not find any information",       # _no_context_answer (empty HNSW)
    "does not contain enough information",  # _generate_answer (empty final)
    "insufficient",                          # system prompt rule 1
    "not mentioned in",                      # common model phrasing
    "no information",                        # common model phrasing
]
_REFUSAL_RE = re.compile("|".join(map(re.escape, REFUSAL_PATTERNS)), re.IGNORECASE)


def is_refusal(answer: str) -> tuple[bool, str | None]:
    """(was_refusal, matched_pattern) for one answer text."""
    m = _REFUSAL_RE.search(answer or "")
    return (m is not None, m.group(0) if m else None)


def refusal_rate(answers: Iterable[str]) -> float | None:
    """Fraction of answers that are refusals. None when the input is empty."""
    answers = list(answers)
    if not answers:
        return None
    return sum(1 for a in answers if is_refusal(a)[0]) / len(answers)


def false_answer_rate(answers: Iterable[str]) -> float | None:
    """Fraction of answers that are NOT refusals. None when empty."""
    r = refusal_rate(answers)
    return None if r is None else 1.0 - r


# --- Operational ----------------------------------------------------------------------


def percentile(values: list[float], p: float) -> float | None:
    """The p-th percentile (0-100), nearest-rank. None when empty."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    idx = min(len(vals) - 1, max(0, int(round((p / 100.0) * (len(vals) - 1)))))
    return vals[idx]


def mean(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def throughput(n: int, total_seconds: float) -> float | None:
    """Queries per second over the whole run (sequential measurement:
    total wall time divided by query count). None when no time elapsed."""
    if total_seconds <= 0:
        return None
    return n / total_seconds


# --- Aggregation helpers ---------------------------------------------------------------


def _avg_or_none(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def summarize_retrieval(per_example: list[dict]) -> dict:
    """Aggregate per-example retrieval metric dicts into a summary.

    Each per-example dict carries: retrieved_ids, relevant_ids (may be
    empty for unanswerable), and the metric values. Unanswerable examples
    are excluded from recall/MRR/nDCG (no ground truth) and included in
    hit rate with expected 0.
    """
    answerable = [p for p in per_example if p.get("relevant_ids")]
    unanswerable = [p for p in per_example if not p.get("relevant_ids")]
    return {
        "n": len(per_example),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "recall_at_5": _avg_or_none([recall_at_k(p["retrieved_ids"], p["relevant_ids"], 5)
                                     for p in answerable]),
        "recall_at_10": _avg_or_none([recall_at_k(p["retrieved_ids"], p["relevant_ids"], 10)
                                      for p in answerable]),
        "mrr": _avg_or_none([reciprocal_rank(p["retrieved_ids"], p["relevant_ids"])
                             for p in answerable]),
        "ndcg_at_10": _avg_or_none([ndcg_at_k(p["retrieved_ids"], p["relevant_ids"], 10)
                                     for p in answerable]),
        "hit_rate": _avg_or_none([hit_rate(p["retrieved_ids"], p["relevant_ids"])
                                  for p in per_example]),
        "context_precision": _avg_or_none(
            [context_precision(p["retrieved_ids"], p["relevant_ids"]) for p in answerable]),
        "context_recall": _avg_or_none(
            [context_recall(p["retrieved_ids"], p["relevant_ids"]) for p in answerable]),
    }


def group_by(per_example: list[dict], key: str) -> dict[str, dict]:
    """summarize_retrieval broken down by a per-example key
    (query_type / difficulty / tenant_id)."""
    groups: dict[str, list[dict]] = {}
    for p in per_example:
        groups.setdefault(str(p.get(key, "?")), []).append(p)
    return {g: summarize_retrieval(items) for g, items in sorted(groups.items())}
