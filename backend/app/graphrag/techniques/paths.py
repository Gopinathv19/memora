"""Technique 4: constrained path retrieval.

For a question that names two or more things ("Which contract belongs to the
client on this case?") the answer is the *chain* between them. This runs
`allShortestPaths` in FalkorDB between pairs of seed entities, within
`path_max_len` hops and over in-scope edges only, and returns the chains as
evidence: every edge on a path contributes its supporting chunks, weighted by
how short and how confident the path is.

    path score = prod(confidence of its edges) / length

With a single seed (or no path between any pair) there is nothing to connect, so
the technique falls back to spreading activation and says so in its notes,
rather than returning nothing.
"""

from itertools import combinations

from app.graphrag.seeds import gather_seeds
from app.graphrag.techniques import spreading
from app.graphrag.techniques.common import Ctx, Timer, combine, evidence_from_scores
from app.graphrag.types import GraphEvidence, ScoredFact

PATHS_PER_PAIR = 3


def run(ctx: Ctx) -> GraphEvidence:
    p = ctx.params
    evidence = GraphEvidence(technique="paths")
    timer = Timer(evidence)

    with timer("seeds"):
        seeds, counts = gather_seeds(
            ctx.store, ctx.tenant_id, ctx.source_ids, ctx.query, ctx.query_vector, p
        )
    evidence.seeds = seeds
    evidence.seed_counts = counts
    evidence.notes.append(f"seed signals: {counts}")
    if len(seeds) < 2:
        evidence.notes.append("fewer than two seeds: nothing to connect, used spreading activation")
        fallback = spreading.run(ctx)
        fallback.technique = "paths"
        fallback.notes = evidence.notes + fallback.notes
        return fallback

    pairs = list(combinations(seeds, 2))[: p.path_pairs]
    entity_scores: dict[str, float] = {s.id: s.score for s in seeds}
    names = {s.id: {"name": s.name, "entity_type": s.entity_type} for s in seeds}
    hops: dict[str, int] = {s.id: 0 for s in seeds}
    contributions: dict[str, list[float]] = {}
    facts: list[ScoredFact] = []

    with timer("paths"):
        for a, b in pairs:
            rows = ctx.store.shortest_paths(
                ctx.tenant_id, a.id, b.id, ctx.source_ids, p.path_max_len, PATHS_PER_PAIR
            )
            for row in rows:
                node_ids, node_names = row["node_ids"], row["node_names"]
                length = len(row["relations"])
                if length == 0:
                    continue
                confs = row["confidences"]
                score = 1.0
                for c in confs:
                    score *= c
                score = score * min(a.score, b.score) / length
                steps = []
                for i, relation in enumerate(row["relations"]):
                    forward = row["starts"][i] == node_ids[i]
                    src, dst = (i, i + 1) if forward else (i + 1, i)
                    chunk_ids = list(row["chunk_ids"][i] or [])
                    for cid in chunk_ids:
                        contributions.setdefault(cid, []).append(score)
                    facts.append(
                        ScoredFact(
                            source_entity_id=node_ids[src], source_name=node_names[src],
                            relation=relation,
                            target_entity_id=node_ids[dst], target_name=node_names[dst],
                            description=row["descriptions"][i], confidence=confs[i],
                            source_id=row["source_ids"][i], chunk_ids=chunk_ids,
                            score=round(score, 5),
                        )
                    )
                    steps.append(
                        {"from": node_names[src], "relation": relation, "to": node_names[dst],
                         "from_id": node_ids[src], "to_id": node_ids[dst]}
                    )
                for idx, (nid, nname) in enumerate(zip(node_ids, node_names)):
                    entity_scores[nid] = max(entity_scores.get(nid, 0.0), score)
                    names.setdefault(nid, {"name": nname, "entity_type": "CONCEPT"})
                    hops[nid] = min(hops.get(nid, idx), idx, len(node_ids) - 1 - idx)
                evidence.paths.append(
                    {
                        "from": a.name, "to": b.name, "length": length,
                        "score": round(score, 5), "steps": steps,
                    }
                )

    if not evidence.paths:
        evidence.notes.append("no path connects the seeds within the hop limit: used spreading activation")
        fallback = spreading.run(ctx)
        fallback.technique = "paths"
        fallback.notes = evidence.notes + fallback.notes
        return fallback

    info = ctx.store.entities_info(ctx.tenant_id, [e for e, n in names.items() if n["entity_type"] == "CONCEPT"])
    for eid, row in info.items():
        names[eid] = {"name": row["name"], "entity_type": row["entity_type"]}
    evidence.paths.sort(key=lambda path: -path["score"])
    evidence = evidence_from_scores(
        ctx, evidence, entity_scores, hops, names, mention_weight=0.2, extra_facts=facts
    )
    # Path-supported chunks outrank mention-only chunks: re-score them from the
    # path contributions alone, keeping mention chunks as weaker extras.
    scored = {c: combine(v) for c, v in contributions.items()}
    for cid, value in evidence.chunk_scores.items():
        scored.setdefault(cid, value * 0.3)
    top = sorted(scored, key=lambda c: (-scored[c], c))[: p.max_graph_chunks]
    peak = scored[top[0]] if top else 1.0
    evidence.chunk_scores = {c: round(scored[c] / peak, 5) for c in top}
    return evidence
