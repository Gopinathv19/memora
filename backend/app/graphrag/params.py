"""Tunable retrieval parameters, shared by every technique.

Defaults are the baseline the dashboard benchmarks; every one can be
overridden per request so the Arena can show what a change does.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class Technique(StrEnum):
    SPREADING = "spreading"  # multi-seed spreading activation with hop decay
    PPR = "ppr"  # personalized PageRank (HippoRAG style)
    DUAL_LEVEL = "dual_level"  # LightRAG style entity + fact retrieval
    PATHS = "paths"  # constrained shortest-path retrieval
    COMMUNITY = "community"  # community summaries for broad questions


class Pipeline(StrEnum):
    VECTOR = "vector"
    GRAPH = "graph"
    HYBRID = "hybrid"


# The reranker's score for the best chunk of an answer. Measured on this
# project's corpus: questions the documents cannot answer score -5.7 or lower,
# questions they can answer mostly score above -3. Embedding cosine cannot make
# this call (0.58-0.70 for no match, 0.63-0.87 for a match: the ranges overlap),
# so this is the one calibrated "did we find anything relevant" signal.
WEAK_EVIDENCE_LOGIT = -5.0

# Fact types that carry more signal for "who/what is connected to X" questions
# than the generic ones. A weight of 1.0 is neutral; tune against an eval set,
# not by feel.
DEFAULT_RELATION_WEIGHTS: dict[str, float] = {}


@dataclass
class RetrievalParams:
    technique: Technique = Technique.SPREADING
    max_hops: int = 2
    # Per-hop multiplier in S(v) = S0 * prod(decay * w_type * confidence).
    decay: float = 0.7
    min_confidence: float = 0.0
    # A node's score is divided by 1 + ln(1 + degree): hubs (a company named in
    # every document) would otherwise swamp the ranking.
    hub_damping: bool = True
    seed_limit: int = 6
    # Neighbour budget for the subgraph PPR runs over.
    subgraph_nodes: int = 120
    ppr_alpha: float = 0.5  # restart probability
    ppr_iterations: int = 30
    path_max_len: int = 3
    path_pairs: int = 6
    relation_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RELATION_WEIGHTS))
    # How many graph chunks the technique may hand to the shared reranker.
    max_graph_chunks: int = 30
    # Weight of the graph list when fused with the vector list (hybrid).
    hybrid_graph_weight: int = 1
