"""The ontology: which entity types and relationship types the graph may hold.

It is service configuration, like the price list: `backend/ontology.json`, a
versioned file reviewed in git (`GRAPH_ONTOLOGY_FILE` can point elsewhere).
The model is shown the allowed types, but its answer is never trusted: every
type it returns is mapped here, and anything unknown falls back to
`fallback_entity_type` / `fallback_relation` with the model's own label kept as
`raw_type` / `raw_relation`, so nothing is lost and the list can be tuned later.

Validation matters for safety, not only tidiness: Cypher cannot take a
relationship type as a parameter, so relation names are written into query
text. The pattern below is what makes that safe -- only `[A-Z][A-Z0-9_]*`
names that appear in this file can ever reach a query.
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings

NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,47}$")
# Structural edges of the graph itself; a fact may not reuse their names.
RESERVED_RELATIONS = frozenset({"CONTAINS", "MENTIONS"})


class OntologyError(ValueError):
    """The ontology file is malformed. Raised at startup."""


def normalize_label(raw: str | None) -> str:
    """'works for' / 'Works-For' / 'worksFor' -> 'WORKS_FOR'."""
    if not raw:
        return ""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(raw).strip())
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return text.upper()[:48]


@dataclass(frozen=True)
class MappedRelation:
    relation: str
    # True when the model's label names the inverse ("OWNED_BY" for OWNS),
    # so source and target must be swapped.
    reversed: bool
    # The model's label when it was not already a canonical type.
    raw: str | None


@dataclass(frozen=True)
class Ontology:
    version: int
    entity_types: dict[str, str]
    relation_types: dict[str, str]
    entity_type_synonyms: dict[str, str]
    relation_synonyms: dict[str, str]
    inverse_relations: dict[str, str]
    fallback_entity_type: str
    fallback_relation: str

    def map_entity_type(self, raw: str | None) -> tuple[str, str | None]:
        """(allowed type, the model's label if it had to be mapped)."""
        label = normalize_label(raw)
        if label in self.entity_types:
            return label, None
        if label in self.entity_type_synonyms:
            return self.entity_type_synonyms[label], label
        return self.fallback_entity_type, label or None

    def map_relation(self, raw: str | None) -> MappedRelation:
        label = normalize_label(raw)
        if label in self.relation_types:
            return MappedRelation(label, False, None)
        if label in self.relation_synonyms:
            return MappedRelation(self.relation_synonyms[label], False, label)
        if label in self.inverse_relations:
            return MappedRelation(self.inverse_relations[label], True, label)
        return MappedRelation(self.fallback_relation, False, label or None)

    def is_relation(self, name: str) -> bool:
        """The whitelist check made right before a name is put into Cypher."""
        return name in self.relation_types and bool(NAME.fullmatch(name))


def _names(value, what: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise OntologyError(f"{what} must be a non-empty object")
    for name, description in value.items():
        if not NAME.fullmatch(name):
            raise OntologyError(f"{what}: {name!r} must match {NAME.pattern}")
        if not isinstance(description, str):
            raise OntologyError(f"{what}: the description of {name} must be a string")
    return dict(value)


def _mapping(value, what: str, targets: dict[str, str]) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise OntologyError(f"{what} must be an object")
    for alias, target in value.items():
        if normalize_label(alias) != alias:
            raise OntologyError(f"{what}: {alias!r} must be UPPER_SNAKE_CASE")
        if target not in targets:
            raise OntologyError(f"{what}: {alias} maps to unknown type {target!r}")
    return dict(value)


def parse_ontology(data: dict) -> Ontology:
    if not isinstance(data, dict):
        raise OntologyError("the ontology must be a JSON object")
    entity_types = _names(data.get("entity_types"), "entity_types")
    relation_types = _names(data.get("relation_types"), "relation_types")
    clash = RESERVED_RELATIONS & set(relation_types)
    if clash:
        raise OntologyError(f"relation_types: {sorted(clash)} are reserved by the graph")
    fallback_entity = data.get("fallback_entity_type")
    fallback_relation = data.get("fallback_relation")
    if fallback_entity not in entity_types:
        raise OntologyError("fallback_entity_type must be one of entity_types")
    if fallback_relation not in relation_types:
        raise OntologyError("fallback_relation must be one of relation_types")
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise OntologyError("version must be a positive integer")
    return Ontology(
        version=version,
        entity_types=entity_types,
        relation_types=relation_types,
        entity_type_synonyms=_mapping(
            data.get("entity_type_synonyms"), "entity_type_synonyms", entity_types
        ),
        relation_synonyms=_mapping(
            data.get("relation_synonyms"), "relation_synonyms", relation_types
        ),
        inverse_relations=_mapping(
            data.get("inverse_relations"), "inverse_relations", relation_types
        ),
        fallback_entity_type=fallback_entity,
        fallback_relation=fallback_relation,
    )


def load_ontology(path: str | Path) -> Ontology:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OntologyError(f"cannot read ontology file {path}: {exc}") from exc
    return parse_ontology(data)


@lru_cache
def get_ontology() -> Ontology:
    return load_ontology(get_settings().graph_ontology_file)
