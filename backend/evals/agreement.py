"""How far an LLM judge agrees with a human grader.

The benchmark's judge scores 0, 0.5 or 1. Hand-label a sample the same way and
compare: raw agreement is the share of identical scores, Cohen's kappa corrects it
for the agreement two graders would reach by chance (0 = chance, 1 = perfect;
below about 0.4 the judge should not be trusted to rank systems).
Pure functions, no database, no LLM.
"""

from __future__ import annotations

from collections import Counter

SCORES = (0.0, 0.5, 1.0)


def agreement(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b):
        raise ValueError("score lists differ in length")
    return round(sum(x == y for x, y in zip(a, b)) / len(a), 4) if a else None


def cohens_kappa(a: list[float], b: list[float]) -> float | None:
    """Unweighted Cohen's kappa; None when undefined (no data, or both graders constant and equal)."""
    if len(a) != len(b):
        raise ValueError("score lists differ in length")
    n = len(a)
    if n == 0:
        return None
    observed = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum((ca[s] / n) * (cb[s] / n) for s in set(ca) | set(cb))
    if expected == 1.0:
        return None
    return round((observed - expected) / (1 - expected), 4)


def compare(human: list[float | None], judge: list[float | None]) -> dict:
    """Agreement over the pairs where both graders gave a score."""
    pairs = [(h, j) for h, j in zip(human, judge) if h is not None and j is not None]
    hs, js = [p[0] for p in pairs], [p[1] for p in pairs]
    return {
        "n": len(pairs), "skipped": len(human) - len(pairs),
        "agreement": agreement(hs, js), "kappa": cohens_kappa(hs, js),
        "judge_mean": round(sum(js) / len(js), 4) if js else None,
        "human_mean": round(sum(hs) / len(hs), 4) if hs else None,
    }
