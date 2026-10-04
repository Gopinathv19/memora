"""Entity resolution: decide which graph node every extracted name refers to.

Entities are shared across the tenant (one "Microsoft" per tenant graph), so a
name is resolved against the entities already in the graph *and* the ones
created earlier in the same build. Within one entity type, in order:

1. **Exact** -- the canonical key, or any alias key the model gave, equals the
   key or an alias key of a known entity.
2. **Acronym** (organizations only, 3+ letters) -- "IBM" and "International
   Business Machines" meet on "ibm".
3. **Fuzzy** -- a strict similarity match on the keys (`GRAPH_FUZZY_THRESHOLD`),
   for spelling variants such as "Infosys Technologies" / "Infosys Technology".
4. Otherwise a **new** entity, whose id is derived from (tenant, type, key), so
   rebuilding the same document yields the same ids.

No LLM call is made here: resolution is deterministic and testable. A false
merge is worse than a duplicate, which is why every step is conservative.
"""

import uuid
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from app.graph.models import (
    GraphExtraction,
    GraphTextUnit,
    KnownEntity,
    Mention,
    ResolvedEntity,
    ResolvedGraph,
    ResolvedRelationship,
)
from app.graph.normalization import (
    ORGANIZATION_TYPES,
    acronym,
    canonical_key,
    display_name,
)
from app.graph.ontology import Ontology

MIN_FUZZY_KEY_CHARS = 5


def entity_id(tenant_id: str, entity_type: str, key: str) -> str:
    return str(uuid.uuid5(uuid.UUID(str(tenant_id)), f"{entity_type}:{key}"))


@dataclass
class _TypeIndex:
    keys: dict[str, str]  # canonical or alias key -> entity id
    acronyms: dict[str, str]  # acronym of a multi-word key -> entity id


class EntityResolver:
    def __init__(
        self,
        tenant_id: str,
        known: list[KnownEntity],
        *,
        fuzzy_threshold: float = 92.0,
    ):
        self.tenant_id = str(tenant_id)
        self.fuzzy_threshold = fuzzy_threshold
        self._index: dict[str, _TypeIndex] = {}
        self._known: dict[str, KnownEntity] = {}
        self.entities: dict[str, ResolvedEntity] = {}
        for entity in known:
            self._known[entity.id] = entity
            self._register(entity.entity_type, entity.id, entity.canonical_key, entity.alias_keys)

    def _type_index(self, entity_type: str) -> _TypeIndex:
        return self._index.setdefault(entity_type, _TypeIndex({}, {}))

    def _register(self, entity_type: str, eid: str, key: str, alias_keys) -> None:
        index = self._type_index(entity_type)
        for k in (key, *alias_keys):
            if k:
                index.keys.setdefault(k, eid)
                if entity_type in ORGANIZATION_TYPES:
                    short = acronym(k)
                    if short and len(short) >= 3:
                        index.acronyms.setdefault(short, eid)

    def _match(self, entity_type: str, keys: list[str]) -> str | None:
        index = self._index.get(entity_type)
        if index is None:
            return None
        for k in keys:
            if k in index.keys:
                return index.keys[k]
        if entity_type in ORGANIZATION_TYPES:
            for k in keys:
                if len(k) >= 3 and k in index.acronyms:  # "ibm" -> International B. M.
                    return index.acronyms[k]
                short = acronym(k)
                if short and len(short) >= 3 and short in index.keys:
                    return index.keys[short]
        candidates = [k for k in index.keys if len(k) >= MIN_FUZZY_KEY_CHARS]
        for k in keys:
            if len(k) < MIN_FUZZY_KEY_CHARS or not candidates:
                continue
            best = process.extractOne(
                k, candidates, scorer=fuzz.ratio, score_cutoff=self.fuzzy_threshold
            )
            if best is not None:
                return index.keys[best[0]]
        return None

    def resolve(
        self, name: str, entity_type: str, aliases: list[str] | None = None
    ) -> ResolvedEntity | None:
        shown = display_name(name)
        key = canonical_key(shown, entity_type)
        if not key:
            return None
        alias_names = [display_name(a) for a in aliases or [] if isinstance(a, str)]
        alias_names = [a for a in alias_names if a and len(a) <= 300]
        alias_keys = [k for k in (canonical_key(a, entity_type) for a in alias_names) if k]

        eid = self._match(entity_type, [key, *alias_keys])
        if eid is None:
            eid = entity_id(self.tenant_id, entity_type, key)
        entity = self.entities.get(eid)
        if entity is None:
            known = self._known.get(eid)
            if known is not None:
                entity = ResolvedEntity(
                    id=eid,
                    entity_type=known.entity_type,
                    canonical_key=known.canonical_key,
                    display_name=known.display_name,
                    is_new=False,
                )
            else:
                entity = ResolvedEntity(
                    id=eid, entity_type=entity_type, canonical_key=key, display_name=shown
                )
            self.entities[eid] = entity
        for alias, alias_key in [(shown, key), *zip(alias_names, alias_keys)]:
            if alias_key != entity.canonical_key:
                entity.aliases.add(alias)
                entity.alias_keys.add(alias_key)
        self._register(entity.entity_type, eid, key, alias_keys)
        return entity


def resolve_graph(
    extractions: list[tuple[GraphTextUnit, GraphExtraction]],
    *,
    tenant_id: str,
    known: list[KnownEntity],
    ontology: Ontology,
    fuzzy_threshold: float = 92.0,
) -> ResolvedGraph:
    """Turn every chunk's extraction into deduplicated entities, mentions and facts."""
    resolver = EntityResolver(tenant_id, known, fuzzy_threshold=fuzzy_threshold)
    mentions: dict[tuple[str, str], Mention] = {}
    facts: dict[tuple[str, str, str], ResolvedRelationship] = {}
    stats = {
        "entities_extracted": 0,
        "entities_skipped": 0,
        "relationships_extracted": 0,
        "relationships_skipped": 0,
    }

    def mention(chunk_id: str, entity: ResolvedEntity, description: str | None) -> None:
        found = mentions.get((chunk_id, entity.id))
        if found is None:
            mentions[(chunk_id, entity.id)] = Mention(chunk_id, entity.id, description)
        elif not found.description and description:
            found.description = description

    for unit, extraction in extractions:
        local: dict[str, ResolvedEntity] = {}  # this chunk's names -> entity
        for item in extraction.entities:
            stats["entities_extracted"] += 1
            entity_type, raw_type = ontology.map_entity_type(item.type)
            entity = resolver.resolve(item.name, entity_type, item.aliases)
            if entity is None:
                stats["entities_skipped"] += 1
                continue
            if raw_type:
                entity.raw_types.add(raw_type)
            mention(unit.id, entity, (item.description or "").strip() or None)
            for name in (item.name, *item.aliases):
                local.setdefault(display_name(name).casefold(), entity)

        def endpoint(name: str, raw_type: str) -> ResolvedEntity | None:
            found = local.get(display_name(name).casefold())
            if found is not None:
                return found
            entity_type, mapped_from = ontology.map_entity_type(raw_type)
            entity = resolver.resolve(name, entity_type)
            if entity is not None:
                if mapped_from:
                    entity.raw_types.add(mapped_from)
                mention(unit.id, entity, None)
                local[display_name(name).casefold()] = entity
            return entity

        for item in extraction.relationships:
            stats["relationships_extracted"] += 1
            source = endpoint(item.source, item.source_type)
            target = endpoint(item.target, item.target_type)
            mapped = ontology.map_relation(item.relation)
            if mapped.reversed:
                source, target = target, source
            if source is None or target is None or source.id == target.id:
                stats["relationships_skipped"] += 1
                continue
            edge_key = (source.id, mapped.relation, target.id)
            fact = facts.get(edge_key)
            if fact is None:
                fact = facts[edge_key] = ResolvedRelationship(
                    source_entity_id=source.id,
                    relation=mapped.relation,
                    target_entity_id=target.id,
                    raw_relation=mapped.raw,
                )
            if unit.id not in fact.chunk_ids:
                fact.chunk_ids.append(unit.id)
            if not fact.description and item.description:
                fact.description = item.description.strip() or None
            if item.confidence is not None:
                fact.confidence = max(fact.confidence or 0.0, item.confidence)

    entities = list(resolver.entities.values())
    stats["entities_created"] = sum(1 for e in entities if e.is_new)
    stats["entities_matched"] = sum(1 for e in entities if not e.is_new)
    return ResolvedGraph(
        entities=entities,
        mentions=list(mentions.values()),
        relationships=list(facts.values()),
        stats=stats,
    )
