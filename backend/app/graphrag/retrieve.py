"""The unified retrieval core: three channels, weighted fusion (plan Step 5).

    vector    the vector pipeline's chunk list, handed in (this module never runs it)
    atoms     the atomic facts nearest the question; each fact's `SUPPORTED_BY` chunks, best fact first
    walk      personalized PageRank over entities AND chunks, restarting at the entities the nearest
              facts are about, at the entities the question names, and at the chunks dense search
              liked (HippoRAG 2's passage nodes): chunks close to the question both in the graph and
              in embedding space collect mass from several directions

The channels are fused with weighted reciprocal rank fusion. The weights come from the question's
kind (`analyse`) and are a starting point to be tuned on the benchmark, not measured values.

Seeding from atomic facts, not from entity names, is deliberate: on a real corpus the nearest
*entities* to "How does Bitcoin prevent double spending?" were WannaCry and Schnorr signatures at
cosine 0.70, while the nearest *facts* were on topic and came with their evidence chunks.

Everything the walk needs from the graph is an existing store query (`activation`, `facts_among`,
`entity_chunks`) plus `vector_atoms`, `chunk_entities` and `vector_communities`. This module
touches no database but the graph.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

from app.graphrag.analyse import Analysis
from app.graphrag.params import RetrievalParams
from app.graphrag.seeds import gather_seeds
from app.graphrag.store import GraphRagStore
from app.graphrag.techniques.common import combine
from app.graphrag.techniques.ppr import CHUNK_EDGE_WEIGHT, FACT_EDGE_WEIGHT, personalized_pagerank

RRF_K = 60
PASSAGE_SEEDS = 5          # dense-search chunks that become restart nodes
ATOM_SEED_FLOOR = 0.85     # an atom seeds the walk if its similarity is within this share of the best
COMMUNITIES = 3
WIDE_FACTOR = 3            # a widened search asks for this many times more of everything

# channel weights by question kind: a first guess, to be tuned on the benchmark
DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "lookup": {"vector": 1.0, "atoms": 1.0, "walk": 0.4},
    "relational": {"vector": 0.6, "atoms": 1.0, "walk": 1.0},
    "corpus": {"vector": 0.5, "atoms": 0.8, "walk": 0.3},
}


def weights_for(analysis: Analysis, params: RetrievalParams) -> dict[str, float]:
    if params.unified_weights:
        base = dict(DEFAULT_WEIGHTS["lookup"])
        base.update({k: float(v) for k, v in params.unified_weights.items() if k in base})
        return base
    return dict(DEFAULT_WEIGHTS.get(analysis.kind, DEFAULT_WEIGHTS["lookup"]))


def weighted_rrf(lists: list[tuple[list[str], float]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal rank fusion with a weight per list: score(d) = sum over lists of weight / (k + rank).

    Ranks start at 1; a list with weight 0 contributes nothing. Ties break on the id so the order is stable.
    """
    scores: dict[str, float] = {}
    for ids, weight in lists:
        if weight <= 0:
            continue
        for rank, doc in enumerate(ids, start=1):
            scores[doc] = scores.get(doc, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def chunks_from_atoms(atoms: list[dict]) -> list[tuple[str, float]]:
    """Chunks ranked by the facts they support: the best fact counts fully, others a quarter each."""
    per_chunk: dict[str, list[float]] = {}
    for a in atoms:
        for chunk_id in a.get("chunk_ids") or []:
            per_chunk.setdefault(chunk_id, []).append(float(a["similarity"]))
    ranked = [(c, combine(v)) for c, v in per_chunk.items()]
    return sorted(ranked, key=lambda kv: (-kv[1], kv[0]))


def _link(edges: dict[str, dict[str, float]], a: str, b: str, w: float) -> None:
    edges.setdefault(a, {})
    edges.setdefault(b, {})
    edges[a][b] = edges[a].get(b, 0.0) + w
    edges[b][a] = edges[b].get(a, 0.0) + w


def walk(
    store: GraphRagStore, tenant_id, source_ids: list[str], entity_seeds: dict[str, float],
    passage_seeds: dict[str, float], params: RetrievalParams,
) -> tuple[dict[str, float], dict[str, float]]:
    """Personalized PageRank over entities and chunks. Returns (chunk mass, entity mass), both by id."""
    if not entity_seeds and not passage_seeds:
        return {}, {}
    ids = set(entity_seeds)
    if entity_seeds:
        rows = store.activation(
            tenant_id, [{"id": e, "w": w} for e, w in entity_seeds.items()], source_ids,
            max_hops=params.max_hops, decay=1.0, min_confidence=params.min_confidence,
            relation_weights={}, hub_damping=False, limit=params.subgraph_nodes,
        )
        ids |= {r["id"] for r in rows}
    if passage_seeds:
        ids |= {p["entity_id"] for p in store.chunk_entities(tenant_id, list(passage_seeds), source_ids)}
    if not ids:
        return {}, {}
    facts = store.facts_among(tenant_id, list(ids), source_ids, params.subgraph_nodes * 6)
    mentions = store.entity_chunks(tenant_id, list(ids), source_ids)

    edges: dict[str, dict[str, float]] = {}
    for f in facts:
        conf = f.get("confidence")
        _link(edges, f["source_entity_id"], f["target_entity_id"],
              FACT_EDGE_WEIGHT * params.relation_weights.get(f["relation"], 1.0) * (conf if conf is not None else 0.6))
    mention_count: dict[str, int] = {}
    for m in mentions:
        mention_count[m["entity_id"]] = mention_count.get(m["entity_id"], 0) + 1
        _link(edges, m["entity_id"], "chunk:" + m["chunk_id"], CHUNK_EDGE_WEIGHT)

    restart: dict[str, float] = {}
    for e, w in entity_seeds.items():
        damp = 1.0 / (1.0 + math.log(1.0 + mention_count.get(e, 0))) if params.hub_damping else 1.0
        restart[e] = w * damp                                    # a hub entity restarts the walk less
    for c, w in passage_seeds.items():
        restart["chunk:" + c] = restart.get("chunk:" + c, 0.0) + w
    mass = personalized_pagerank(edges, restart, params.ppr_alpha, params.ppr_iterations)
    chunk_mass = {n[6:]: m for n, m in mass.items() if n.startswith("chunk:")}
    entity_mass = {n: m for n, m in mass.items() if not n.startswith("chunk:")}
    return chunk_mass, entity_mass


@dataclass
class Retrieval:
    fused: list[str]                                  # chunk ids, best first
    fused_scores: dict[str, float]
    lists: dict[str, list[str]]                       # per channel, best first
    weights: dict[str, float]
    analysis: Analysis
    atoms: list[dict] = field(default_factory=list)
    communities: list[dict] = field(default_factory=list)
    entity_mass: dict[str, float] = field(default_factory=dict)
    seeds: dict = field(default_factory=dict)
    timings_ms: dict = field(default_factory=dict)
    wide: bool = False

    def channels_of(self, chunk_id: str) -> list[str]:
        return [name for name, ids in self.lists.items() if chunk_id in ids]

    def trace(self) -> dict:
        return {
            "analysis": {"kind": self.analysis.kind, "entities": self.analysis.entities,
                         "sub_questions": self.analysis.sub_questions, "source": self.analysis.source},
            "weights": self.weights, "wide": self.wide,
            "channel_sizes": {k: len(v) for k, v in self.lists.items()},
            "seeds": self.seeds,
            "atoms": [{"text": a["text"], "similarity": round(a["similarity"], 4), "chunk_ids": a["chunk_ids"]}
                      for a in self.atoms[:10]],
            "communities": [{"id": c["id"], "title": c["title"], "size": c["size"],
                             "similarity": round(c["similarity"], 4)} for c in self.communities],
            "timings_ms": self.timings_ms,
        }


def gather(
    store: GraphRagStore, tenant_id, source_ids: list[str], subject_id: str, query: str,
    query_vector: list[float], vector_ids: list[str], analysis: Analysis, params: RetrievalParams, *,
    embed_query: Callable[[str], list[float]] | None = None, wide: bool = False,
) -> Retrieval:
    """Run the atom and walk channels, fuse them with the vector list, and collect the context.

    `vector_ids` is the vector pipeline's chunk ranking for this question (computed by the caller).
    `embed_query` is only needed for `unified_multi_hop`. `wide` asks for `WIDE_FACTOR` times more.
    """
    timings: dict[str, int] = {}
    clock = time.monotonic()

    def lap(name: str) -> None:
        nonlocal clock
        now = time.monotonic()
        timings[name] = timings.get(name, 0) + int((now - clock) * 1000)
        clock = now

    factor = WIDE_FACTOR if wide else 1
    weights = weights_for(analysis, params)

    atoms = store.vector_atoms(tenant_id, query_vector, source_ids, params.atom_k * factor)
    lap("atoms")

    entity_seeds: dict[str, float] = {}
    if atoms:
        best = max(a["similarity"] for a in atoms)
        for a in atoms:
            if a["similarity"] >= ATOM_SEED_FLOOR * best:
                for e in a.get("entity_ids") or []:
                    entity_seeds[e] = max(entity_seeds.get(e, 0.0), a["similarity"])
    named, signal_counts = gather_seeds(
        store, tenant_id, source_ids, query, query_vector, params, extra_keys=analysis.entities
    )
    for s in named:
        entity_seeds[s.id] = max(entity_seeds.get(s.id, 0.0), s.score)

    sub_atoms: list[dict] = []
    if params.unified_multi_hop and embed_query and analysis.sub_questions:
        for i, sub in enumerate(analysis.sub_questions):
            found = store.vector_atoms(tenant_id, embed_query(sub), source_ids, 3)
            for a in found:
                for e in a.get("entity_ids") or []:
                    entity_seeds[e] = max(entity_seeds.get(e, 0.0), a["similarity"] * 0.7 ** (i + 1))
            sub_atoms += found
    top = max(entity_seeds.values(), default=1.0) or 1.0
    entity_seeds = {e: w / top for e, w in entity_seeds.items()}
    lap("seeds")

    passage_seeds = {
        c: params.walk_passage_weight / (rank + 1) for rank, c in enumerate(vector_ids[:PASSAGE_SEEDS])
    }
    chunk_mass, entity_mass = walk(store, tenant_id, source_ids, entity_seeds, passage_seeds, params)
    walk_ids = sorted(chunk_mass, key=lambda c: (-chunk_mass[c], c))[: params.max_graph_chunks * factor]
    lap("walk")

    # one entry per fact (the same fact can answer the question and a sub-question), best first, so the evidence
    # builder, which takes facts in list order, shows the most similar ones
    best: dict[str, dict] = {}
    for a in atoms + sub_atoms:
        if a["id"] not in best or a["similarity"] > best[a["id"]]["similarity"]:
            best[a["id"]] = a
    all_atoms = sorted(best.values(), key=lambda a: (-a["similarity"], a["id"]))
    atom_ids = [c for c, _ in chunks_from_atoms(all_atoms)]

    communities: list[dict] = []
    if analysis.kind == "corpus" and subject_id:
        communities = store.vector_communities(tenant_id, query_vector, [str(subject_id)], COMMUNITIES)
    lap("communities")

    lists = {"vector": list(vector_ids), "atoms": atom_ids, "walk": walk_ids}
    fused = weighted_rrf([(lists[name], weights[name]) for name in ("vector", "atoms", "walk")])
    return Retrieval(
        fused=[c for c, _ in fused], fused_scores=dict(fused), lists=lists, weights=weights, analysis=analysis,
        atoms=all_atoms, communities=communities, entity_mass=entity_mass,
        seeds={"entity_seeds": len(entity_seeds), "passage_seeds": len(passage_seeds), "name_signals": signal_counts},
        timings_ms=timings, wide=wide,
    )
