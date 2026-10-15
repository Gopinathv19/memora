"""What every technique returns: scored graph evidence, nothing about SQL."""

from dataclasses import dataclass, field


@dataclass
class ScoredEntity:
    id: str
    name: str
    entity_type: str
    score: float
    hop: int | None = None  # None for entities reached some other way
    seed: bool = False
    community: str | None = None


@dataclass
class ScoredFact:
    source_entity_id: str
    source_name: str
    relation: str
    target_entity_id: str
    target_name: str
    description: str | None
    confidence: float | None
    source_id: str
    chunk_ids: list[str] = field(default_factory=list)
    score: float = 0.0

    def sentence(self) -> str:
        text = f"{self.source_name} --{self.relation}--> {self.target_name}"
        return f"{text}: {self.description}" if self.description else text


@dataclass
class GraphEvidence:
    """The graph's answer to a question, before it is turned into SQL chunks."""

    technique: str
    seeds: list[ScoredEntity] = field(default_factory=list)
    entities: list[ScoredEntity] = field(default_factory=list)
    facts: list[ScoredFact] = field(default_factory=list)
    # graph text-unit id -> relevance, best first
    chunk_scores: dict[str, float] = field(default_factory=dict)
    paths: list[dict] = field(default_factory=list)
    communities: list[dict] = field(default_factory=list)
    # Extra prompt text (community summaries); facts are added by the caller.
    context: list[str] = field(default_factory=list)
    timings_ms: dict[str, int] = field(default_factory=dict)
    # LLM calls made by the technique itself: {"prompt":..,"completion":..,"model":..}
    llm_usage: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # how many seeds each signal found: {"vector": n, "key": n, "fulltext": n}
    seed_counts: dict[str, int] = field(default_factory=dict)

    @property
    def anchored(self) -> bool:
        """True when a name in the question matched an entity (exact key or full-text).

        Vector similarity alone does not anchor a question: with this embedding model
        every entity is about 0.6 similar to every question."""
        return bool(self.seed_counts.get("key") or self.seed_counts.get("fulltext"))

    def ranked_chunk_ids(self, limit: int | None = None) -> list[str]:
        ids = sorted(self.chunk_scores, key=lambda c: (-self.chunk_scores[c], c))
        return ids[:limit] if limit else ids
