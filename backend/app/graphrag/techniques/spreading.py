"""Technique 1: multi-seed spreading activation with hop decay.

    seeds (vector + key + full-text)  ->  one Cypher statement activates every
    entity within `max_hops` of a seed:

        S(v) = sum over paths P from a seed s of
               w(s) * prod over edges e in P of (decay * w_type(e) * confidence(e))

    divided by (1 + ln(1 + degree(v))) when hub damping is on.

Summing over paths is what separates this from plain "decay by distance": an
entity reachable by several short paths from several seeds outranks one at the
end of a single chain, which is the multi-seed behaviour a two-part question
needs. The cost is cycles and hubs, hence the damping term and the hop cap.
"""

from app.graphrag.seeds import gather_seeds
from app.graphrag.techniques.common import Ctx, Timer, evidence_from_scores
from app.graphrag.types import GraphEvidence


def run(ctx: Ctx) -> GraphEvidence:
    p = ctx.params
    evidence = GraphEvidence(technique="spreading")
    timer = Timer(evidence)

    with timer("seeds"):
        seeds, counts = gather_seeds(
            ctx.store, ctx.tenant_id, ctx.source_ids, ctx.query, ctx.query_vector, p
        )
    evidence.seeds = seeds
    evidence.seed_counts = counts
    evidence.notes.append(f"seed signals: {counts}")
    if not seeds:
        evidence.notes.append("no seed entity matched the question")
        return evidence

    with timer("activation"):
        rows = ctx.store.activation(
            ctx.tenant_id,
            [{"id": s.id, "w": s.score} for s in seeds],
            ctx.source_ids,
            max_hops=p.max_hops,
            decay=p.decay,
            min_confidence=p.min_confidence,
            relation_weights=p.relation_weights,
            hub_damping=p.hub_damping,
            limit=p.subgraph_nodes,
        )

    scores = {s.id: s.score for s in seeds}
    hops = {s.id: 0 for s in seeds}
    names = {s.id: {"name": s.name, "entity_type": s.entity_type} for s in seeds}
    for row in rows:
        if row["id"] in scores:
            continue  # a seed reached from another seed keeps its seed weight
        scores[row["id"]] = float(row["score"])
        hops[row["id"]] = int(row["hop"])
        names[row["id"]] = {"name": row["name"], "entity_type": row["entity_type"]}
    return evidence_from_scores(ctx, evidence, scores, hops, names)
