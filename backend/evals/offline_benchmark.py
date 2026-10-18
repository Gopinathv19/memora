"""Scoring and comparing retrieval systems on an eval set, without any answer generation.

Each row is one (question, system) result: the chunks the system finally chose and whether it would have
abstained. `summarize` reports, per system and per query type:

* answerable questions: Recall@k (share of the question's supporting chunks found), hit rate (any found), MRR
* unanswerable questions: how often the system abstains (higher is better)
* answerable questions the system abstains on anyway (lower is better)
* against a baseline system, paired per question: mean recall difference with a 95% bootstrap interval, and
  wins / ties / losses (a difference under `TIE_MARGIN` is a tie)

Retrieval only, so a "win" says the right passages were found, not that the answer was good. Pure functions.
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict

TIE_MARGIN = 0.05


def score(retrieved: list[str], relevant: list[str], k: int) -> dict | None:
    """None when the question has no supporting chunks (unanswerable)."""
    if not relevant:
        return None
    top = retrieved[:k]
    wanted = set(relevant)
    found = [c for c in top if c in wanted]
    rank = next((i for i, c in enumerate(top, 1) if c in wanted), None)
    return {"recall": len(set(found)) / len(wanted), "hit": 1.0 if found else 0.0, "rr": 1.0 / rank if rank else 0.0}


def bootstrap_ci(values: list[float], n: int = 2000, seed: int = 11) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(n))
    return round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4)


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def _block(rows: list[dict], k: int) -> dict:
    scored = [s for r in rows if not r["is_unanswerable"] and (s := score(r["retrieved_ids"], r["relevant_ids"], k))]
    answerable = [r for r in rows if not r["is_unanswerable"]]
    unanswerable = [r for r in rows if r["is_unanswerable"]]
    return {
        "n": len(rows), "answerable": len(answerable), "unanswerable": len(unanswerable),
        "recall": _mean([s["recall"] for s in scored]), "hit": _mean([s["hit"] for s in scored]),
        "mrr": _mean([s["rr"] for s in scored]),
        "abstain_on_unanswerable": _mean([1.0 if r["abstained"] else 0.0 for r in unanswerable]),
        "false_abstain_on_answerable": _mean([1.0 if r["abstained"] else 0.0 for r in answerable]),
    }


def paired(rows: list[dict], base_rows: list[dict], k: int) -> dict | None:
    """Per-question recall differences of `rows` against `base_rows` (matched on `qid`), answerable questions only."""
    base = {r["qid"]: r for r in base_rows}
    diffs, wins, ties, losses = [], 0, 0, 0
    for r in rows:
        b = base.get(r["qid"])
        if b is None or r["is_unanswerable"]:
            continue
        s, t = score(r["retrieved_ids"], r["relevant_ids"], k), score(b["retrieved_ids"], b["relevant_ids"], k)
        if s is None or t is None:
            continue
        d = s["recall"] - t["recall"]
        diffs.append(d)
        wins += d > TIE_MARGIN
        losses += d < -TIE_MARGIN
        ties += abs(d) <= TIE_MARGIN
    if not diffs:
        return None
    return {"n": len(diffs), "mean_recall_diff": round(statistics.fmean(diffs), 4), "ci95": bootstrap_ci(diffs),
            "wins": wins, "ties": ties, "losses": losses}


def summarize(per_system: dict[str, list[dict]], *, k: int = 6, baseline: str = "vector") -> dict:
    out: dict = {"k": k, "baseline": baseline, "systems": {}}
    for name, rows in per_system.items():
        by_type: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_type[r["query_type"]].append(r)
        entry = {"overall": _block(rows, k), "by_type": {t: _block(rs, k) for t, rs in sorted(by_type.items())}}
        if name != baseline and baseline in per_system:
            entry["vs_baseline"] = paired(rows, per_system[baseline], k)
            entry["vs_baseline_by_type"] = {
                t: p for t, rs in sorted(by_type.items())
                if (p := paired(rs, [b for b in per_system[baseline] if b["query_type"] == t], k)) is not None
            }
        out["systems"][name] = entry
    return out


def _rate(rows: list[dict], key: str) -> float | None:
    values = [1.0 if r[key] else 0.0 for r in rows]
    return round(statistics.fmean(values), 4) if values else None


def summarize_answers(rows: list[dict]) -> dict:
    """End-to-end answer behaviour of one setting. Each row: is_unanswerable, refused, verification (the answer
    step's checks, empty when it refused), has_citations, total_ms, cost_usd.

    Refusal counts both the retrieval-level abstention and the model saying the knowledge is insufficient, which is
    what a user would see. The citation and check rates are over the questions that were answered, not refused.
    """
    unanswerable = [r for r in rows if r["is_unanswerable"]]
    answerable = [r for r in rows if not r["is_unanswerable"]]
    answered = [r for r in rows if not r["refused"] and r.get("verification")]
    flags = lambda r, k: bool((r["verification"] or {}).get(k))          # noqa: E731
    return {
        "unanswerable": len(unanswerable), "answerable": len(answerable), "answered": len(answered),
        "refused_on_unanswerable": _rate(unanswerable, "refused"),
        "refused_on_answerable": _rate(answerable, "refused"),
        "cited_rate": _rate(answered, "has_citations"),
        "verified_ok_rate": round(statistics.fmean([1.0 if flags(r, "ok") else 0.0 for r in answered]), 4) if answered else None,
        "uncited_rate": round(statistics.fmean([1.0 if flags(r, "uncited_sentences") else 0.0 for r in answered]), 4) if answered else None,
        "unsupported_number_rate": round(statistics.fmean([1.0 if flags(r, "unsupported_numbers") else 0.0 for r in answered]), 4) if answered else None,
        "mean_cost_usd": round(statistics.fmean([r.get("cost_usd", 0.0) for r in rows]), 6) if rows else None,
        "mean_total_ms": round(statistics.fmean([r.get("total_ms", 0) for r in rows])) if rows else None,
    }
