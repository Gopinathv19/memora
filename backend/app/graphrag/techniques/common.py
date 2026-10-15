"""Pieces every technique shares: the context it runs in, and turning a set of
scored entities into scored facts and scored chunks."""

import time
from dataclasses import dataclass, field

from app.graphrag.params import RetrievalParams
from app.graphrag.store import GraphRagStore
from app.graphrag.types import GraphEvidence, ScoredEntity, ScoredFact

MAX_FACTS = 60
MAX_SHOWN_ENTITIES = 40


@dataclass
class Ctx:
    store: GraphRagStore
    tenant_id: object
    source_ids: list[str]
    subject_id: str
    query: str
    query_vector: list[float] | None
    embedding_cfg: object | None
    params: RetrievalParams
    # filled by a technique that falls back to another
    notes: list[str] = field(default_factory=list)


class Timer:
    """`with timer("expand"):` records milliseconds on the evidence."""

    def __init__(self, evidence: GraphEvidence):
        self.evidence = evidence

    def __call__(self, name: str):
        return _Span(self.evidence, name)


class _Span:
    def __init__(self, evidence: GraphEvidence, name: str):
        self.evidence, self.name = evidence, name

    def __enter__(self):
        self.t0 = time.monotonic()

    def __exit__(self, *exc):
        key = f"{self.name}_ms"
        self.evidence.timings_ms[key] = self.evidence.timings_ms.get(key, 0) + int(
            (time.monotonic() - self.t0) * 1000
        )


def combine(contributions: list[float]) -> float:
    """Several pieces of evidence for one chunk: the best one counts fully, the
    rest add a quarter each, so a chunk supported many ways ranks above one
    supported once, without letting a hub chunk dominate."""
    if not contributions:
        return 0.0
    best = max(contributions)
    return best + 0.25 * (sum(contributions) - best)


def fact_from_row(row: dict, score: float = 0.0) -> ScoredFact:
    return ScoredFact(
        source_entity_id=row["source_entity_id"],
        source_name=row["source_name"],
        relation=row["relation"],
        target_entity_id=row["target_entity_id"],
        target_name=row["target_name"],
        description=row.get("description"),
        confidence=row.get("confidence"),
        source_id=row["source_id"],
        chunk_ids=list(row.get("chunk_ids") or []),
        score=score,
    )


def score_facts(rows: list[dict], entity_scores: dict[str, float]) -> list[ScoredFact]:
    """A fact scores as the weaker endpoint times its confidence: it is only as
    relevant as the less relevant entity it joins."""
    facts = []
    for row in rows:
        a = entity_scores.get(row["source_entity_id"], 0.0)
        b = entity_scores.get(row["target_entity_id"], 0.0)
        conf = row.get("confidence")
        score = min(a, b) * (conf if conf is not None else 0.6)
        facts.append(fact_from_row(row, round(score, 5)))
    facts.sort(key=lambda f: -f.score)
    return facts


def evidence_from_scores(
    ctx: Ctx,
    evidence: GraphEvidence,
    entity_scores: dict[str, float],
    hops: dict[str, int],
    names: dict[str, dict],
    *,
    mention_weight: float = 0.35,
    extra_facts: list[ScoredFact] | None = None,
) -> GraphEvidence:
    """Fill `entities`, `facts` and `chunk_scores` from per-entity scores.

    Chunk evidence comes from two places: the chunks that support a retrieved
    fact (full weight: the fact is the reason it matters), and the chunks that
    merely mention a retrieved entity (`mention_weight`: weaker, but it is how
    an entity with no extracted facts still brings evidence).
    """
    ids = sorted(entity_scores, key=lambda e: -entity_scores[e])[:MAX_SHOWN_ENTITIES]
    seed_ids = {s.id for s in evidence.seeds}
    evidence.entities = [
        ScoredEntity(
            id=e,
            name=names.get(e, {}).get("name", e),
            entity_type=names.get(e, {}).get("entity_type", "CONCEPT"),
            score=round(entity_scores[e], 5),
            hop=hops.get(e),
            seed=e in seed_ids,
        )
        for e in ids
    ]
    with Timer(evidence)("facts"):
        rows = ctx.store.facts_among(ctx.tenant_id, ids, ctx.source_ids, MAX_FACTS * 3)
    facts = score_facts(rows, entity_scores)
    facts = (extra_facts or []) + [
        f for f in facts if (f.source_entity_id, f.relation, f.target_entity_id)
        not in {(x.source_entity_id, x.relation, x.target_entity_id) for x in (extra_facts or [])}
    ]
    evidence.facts = facts[:MAX_FACTS]

    contributions: dict[str, list[float]] = {}
    for fact in evidence.facts:
        for chunk_id in fact.chunk_ids:
            contributions.setdefault(chunk_id, []).append(max(fact.score, 0.0))
    with Timer(evidence)("mentions"):
        mentions = ctx.store.entity_chunks(ctx.tenant_id, ids, ctx.source_ids)
    for row in mentions:
        contributions.setdefault(row["chunk_id"], []).append(
            mention_weight * entity_scores.get(row["entity_id"], 0.0)
        )
    scored = {c: combine(v) for c, v in contributions.items() if combine(v) > 0}
    top = sorted(scored, key=lambda c: (-scored[c], c))[: ctx.params.max_graph_chunks]
    peak = scored[top[0]] if top else 1.0
    evidence.chunk_scores = {c: round(scored[c] / peak, 5) for c in top}
    return evidence
