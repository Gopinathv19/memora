"""Entity / relationship extraction: one LLM call per graph text unit.

The model is the configured `LLM_GRAPH_MODEL` (an NVIDIA model, through the
same `LLMClient` as the Extraction Agent, at temperature 0 with one JSON repair
retry). Its reply is validated item by item: malformed entities or
relationships are dropped, the rest kept -- the same lenient rule the
extraction agent applies, so one bad item never costs a whole chunk.
"""

from dataclasses import dataclass

from pydantic import ValidationError

from app.agents.extraction_agent import UsageRecord
from app.core.config import Settings
from app.graph import prompts
from app.graph.models import (
    ExtractedEntity,
    ExtractedRelationship,
    GraphExtraction,
    GraphTextUnit,
)
from app.graph.ontology import Ontology
from app.llm.client import LLMClient, LLMError
from app.schemas.enums import ModelRole

MAX_ITEMS = 200  # per list, per chunk: a runaway reply cannot flood the graph


@dataclass
class ChunkExtraction:
    unit: GraphTextUnit
    extraction: GraphExtraction | None
    usage: UsageRecord
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.extraction is not None


def _confidence(value):
    try:
        c = float(value)
    except (TypeError, ValueError):
        return None
    if c > 1:  # a model answering in percent
        c = c / 100
    return min(max(c, 0.0), 1.0)


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int)) and str(v).strip()][:20]


def parse_extraction(raw: dict) -> tuple[GraphExtraction, int]:
    """Validate the model's JSON leniently. Returns (extraction, items dropped)."""
    entities, relationships, dropped = [], [], 0
    for item in (raw.get("entities") or [])[:MAX_ITEMS]:
        if not isinstance(item, dict):
            dropped += 1
            continue
        try:
            entities.append(
                ExtractedEntity(
                    name=str(item.get("name") or "").strip(),
                    type=str(item.get("type") or "").strip()[:64],
                    description=(str(item["description"]).strip()[:1000] or None)
                    if item.get("description")
                    else None,
                    aliases=_strings(item.get("aliases")),
                )
            )
        except ValidationError:
            dropped += 1
    for item in (raw.get("relationships") or [])[:MAX_ITEMS]:
        if not isinstance(item, dict):
            dropped += 1
            continue
        try:
            relationships.append(
                ExtractedRelationship(
                    source=str(item.get("source") or "").strip(),
                    source_type=str(item.get("source_type") or "").strip()[:64],
                    relation=str(item.get("relation") or "").strip()[:64],
                    target=str(item.get("target") or "").strip(),
                    target_type=str(item.get("target_type") or "").strip()[:64],
                    description=(str(item["description"]).strip()[:1000] or None)
                    if item.get("description")
                    else None,
                    confidence=_confidence(item.get("confidence")),
                )
            )
        except ValidationError:
            dropped += 1
    return GraphExtraction(entities=entities, relationships=relationships), dropped


def _pages(unit: GraphTextUnit) -> str | None:
    if unit.page_start is None:
        return None
    if unit.page_end in (None, unit.page_start):
        return f"page {unit.page_start}"
    return f"pages {unit.page_start}-{unit.page_end}"


class GraphExtractor:
    def __init__(self, client: LLMClient, settings: Settings, ontology: Ontology):
        self.client = client
        self.model = settings.llm_graph_model
        self.system = prompts.system_prompt(ontology)

    @property
    def provider(self) -> str:
        return self.client.provider

    def extract(self, unit: GraphTextUnit) -> ChunkExtraction:
        try:
            raw, usage = self.client.chat_json(
                self.model, self.system, prompts.user_message(unit.text, _pages(unit))
            )
        except LLMError as exc:
            message = (exc.message or str(exc))[:500]
            record = UsageRecord.of(ModelRole.GRAPH, self.model, unit.page_start, exc.usage, message)
            return ChunkExtraction(unit, None, record, message)
        extraction, _ = parse_extraction(raw)
        record = UsageRecord.of(ModelRole.GRAPH, self.model, unit.page_start, usage)
        return ChunkExtraction(unit, extraction, record)
