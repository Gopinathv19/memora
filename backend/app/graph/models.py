"""The objects that pass between the graph stages.

    content -> GraphTextUnit -> (LLM) GraphExtraction -> (resolution)
    ResolvedGraph -> FalkorDB

Nothing here knows about FalkorDB or the model provider.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


@dataclass
class GraphTextUnit:
    """One chunk of a document's content, the unit one LLM call reads.

    `char_start` / `char_end` are offsets into `source_extractions.content`
    (the span the chunk's text was taken from; page markers inside the span
    are not part of `text`).
    """

    id: str
    source_id: str
    index: int
    text: str
    char_start: int
    char_end: int
    page_start: int | None = None
    page_end: int | None = None


# --- what the model returns (validated leniently, item by item) -----------------


class ExtractedEntity(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    type: str = Field(default="", max_length=64)
    description: str | None = Field(default=None, max_length=1000)
    aliases: list[str] = Field(default_factory=list)


class ExtractedRelationship(BaseModel):
    source: str = Field(min_length=1, max_length=300)
    source_type: str = Field(default="", max_length=64)
    relation: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=300)
    target_type: str = Field(default="", max_length=64)
    description: str | None = Field(default=None, max_length=1000)
    confidence: float | None = Field(default=None, ge=0, le=1)


class GraphExtraction(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)


# --- after resolution: what gets written -----------------------------------------


@dataclass
class KnownEntity:
    """An entity already in the tenant graph, as resolution sees it."""

    id: str
    entity_type: str
    canonical_key: str
    display_name: str
    alias_keys: list[str] = field(default_factory=list)


@dataclass
class ResolvedEntity:
    id: str
    entity_type: str
    canonical_key: str
    display_name: str
    aliases: set[str] = field(default_factory=set)
    alias_keys: set[str] = field(default_factory=set)
    raw_types: set[str] = field(default_factory=set)
    is_new: bool = True


@dataclass
class Mention:
    """Chunk -> entity, with what this chunk says the entity is."""

    chunk_id: str
    entity_id: str
    description: str | None = None


@dataclass
class ResolvedRelationship:
    """One fact edge for this source; its chunks are every chunk stating it."""

    source_entity_id: str
    relation: str
    target_entity_id: str
    chunk_ids: list[str] = field(default_factory=list)
    description: str | None = None
    confidence: float | None = None
    raw_relation: str | None = None


@dataclass
class ResolvedGraph:
    entities: list[ResolvedEntity]
    mentions: list[Mention]
    relationships: list[ResolvedRelationship]
    stats: dict[str, int] = field(default_factory=dict)
