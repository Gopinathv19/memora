"""Technique 2: personalized PageRank over the question's neighbourhood
(HippoRAG / HippoRAG 2 style).

    seeds -> bounded neighbourhood from FalkorDB (entities, fact edges, and the
    text units that mention them) -> personalized PageRank with restart on the
    seeds -> the chunks' PageRank mass is their relevance.

As in HippoRAG 2, text units are nodes in the walk, not just labels on edges,
so relevance flows entity -> chunk -> entity and a chunk that mentions several
relevant entities collects mass from all of them.

Hub handling is the known weak point of PPR (a walk drifts into high-degree
nodes), so the restart vector of each seed is divided by
`1 + ln(1 + chunks mentioning it)` (HippoRAG's "node specificity"), and fact
edge weights carry the extraction confidence.

Distance attenuation is already inside PPR (mass decays with every step), so
this technique does NOT also apply per-hop decay.
"""

import math

from app.graphrag.seeds import gather_seeds
from app.graphrag.techniques.common import Ctx, Timer, evidence_from_scores
from app.graphrag.types import GraphEvidence

CHUNK_EDGE_WEIGHT = 1.0
FACT_EDGE_WEIGHT = 1.0


def personalized_pagerank(
    edges: dict[str, dict[str, float]],
    restart: dict[str, float],
    alpha: float,
    iterations: int,
) -> dict[str, float]:
    """Power iteration. `edges[u][v]` is the weight of the undirected edge u-v.

    p <- alpha * r + (1 - alpha) * sum over neighbours u of p[u] * w(u,v) / W(u)
    Dangling mass is returned to the restart distribution, so the scores sum to 1.
    """
    total = sum(restart.values())
    if total <= 0:
        return {}
    r = {n: w / total for n, w in restart.items()}
    nodes = set(edges) | {v for nbrs in edges.values() for v in nbrs} | set(r)
    out_weight = {u: sum(nbrs.values()) for u, nbrs in edges.items()}
    p = {n: r.get(n, 0.0) for n in nodes}
    for _ in range(max(1, iterations)):
        nxt = {n: alpha * r.get(n, 0.0) for n in nodes}
        dangling = 0.0
        for u, mass in p.items():
            w_u = out_weight.get(u, 0.0)
            if w_u <= 0:
                dangling += mass
                continue
            share = (1 - alpha) * mass / w_u
            for v, w in edges[u].items():
                nxt[v] += share * w
        if dangling:
            for n, w in r.items():
                nxt[n] += (1 - alpha) * dangling * w
        p = nxt
    return p


def _add_edge(edges: dict[str, dict[str, float]], a: str, b: str, w: float) -> None:
    edges.setdefault(a, {})
    edges.setdefault(b, {})
    edges[a][b] = edges[a].get(b, 0.0) + w
    edges[b][a] = edges[b].get(a, 0.0) + w


def run(ctx: Ctx) -> GraphEvidence:
    p = ctx.params
    evidence = GraphEvidence(technique="ppr")
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

    with timer("neighbourhood"):
        rows = ctx.store.activation(
            ctx.tenant_id,
            [{"id": s.id, "w": s.score} for s in seeds],
            ctx.source_ids,
            max_hops=p.max_hops,
            decay=1.0,  # only used to pick the neighbourhood; PPR does the weighting
            min_confidence=p.min_confidence,
            relation_weights={},
            hub_damping=False,
            limit=p.subgraph_nodes,
        )
        ids = list({s.id for s in seeds} | {r["id"] for r in rows})
        names = {s.id: {"name": s.name, "entity_type": s.entity_type} for s in seeds}
        names.update({r["id"]: {"name": r["name"], "entity_type": r["entity_type"]} for r in rows})
        hops = {s.id: 0 for s in seeds}
        hops.update({r["id"]: int(r["hop"]) for r in rows if r["id"] not in hops})
        facts = ctx.store.facts_among(ctx.tenant_id, ids, ctx.source_ids, p.subgraph_nodes * 6)
        mentions = ctx.store.entity_chunks(ctx.tenant_id, ids, ctx.source_ids)

    edges: dict[str, dict[str, float]] = {}
    for fact in facts:
        conf = fact.get("confidence")
        w_type = p.relation_weights.get(fact["relation"], 1.0)
        _add_edge(
            edges, fact["source_entity_id"], fact["target_entity_id"],
            FACT_EDGE_WEIGHT * w_type * (conf if conf is not None else 0.6),
        )
    mention_count: dict[str, int] = {}
    for m in mentions:
        mention_count[m["entity_id"]] = mention_count.get(m["entity_id"], 0) + 1
        _add_edge(edges, m["entity_id"], "chunk:" + m["chunk_id"], CHUNK_EDGE_WEIGHT)

    restart = {}
    for s in seeds:
        specificity = (
            1.0 / (1.0 + math.log(1.0 + mention_count.get(s.id, 0))) if p.hub_damping else 1.0
        )
        restart[s.id] = s.score * specificity
    with timer("pagerank"):
        mass = personalized_pagerank(edges, restart, p.ppr_alpha, p.ppr_iterations)

    entity_scores = {n: m for n, m in mass.items() if not n.startswith("chunk:") and n in names}
    peak = max(entity_scores.values(), default=1.0) or 1.0
    entity_scores = {n: s / peak for n, s in entity_scores.items()}
    evidence = evidence_from_scores(ctx, evidence, entity_scores, hops, names)

    chunk_mass = {n[6:]: m for n, m in mass.items() if n.startswith("chunk:")}
    cpeak = max(chunk_mass.values(), default=1.0) or 1.0
    top = sorted(chunk_mass, key=lambda c: (-chunk_mass[c], c))[: p.max_graph_chunks]
    # PPR mass on a chunk is the technique's own ranking; it replaces the
    # fact/mention heuristic that `evidence_from_scores` computed.
    evidence.chunk_scores = {c: round(chunk_mass[c] / cpeak, 5) for c in top}
    evidence.notes.append(
        f"ppr over {len(edges)} nodes, alpha={p.ppr_alpha}, {p.ppr_iterations} iterations"
    )
    return evidence
