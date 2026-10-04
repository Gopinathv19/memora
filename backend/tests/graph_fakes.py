"""A fake graph model: answers from a table of known sentences, never the network.

`FakeGraphLLM.chat_json` looks for each known sentence in the chunk it was
given and returns the entities and relationships that sentence states -- a
stand-in for Nemotron that is deterministic enough to assert on.
"""

from app.llm.client import LLMError, LLMUsage


def _entity(name, type_, description=None, aliases=()):
    return {"name": name, "type": type_, "description": description, "aliases": list(aliases)}


def _fact(source, source_type, relation, target, target_type, description=None, confidence=0.9):
    return {
        "source": source,
        "source_type": source_type,
        "relation": relation,
        "target": target,
        "target_type": target_type,
        "description": description,
        "confidence": confidence,
    }


FACTS = {
    "Alice works at Acme Corporation.": (
        [_entity("Alice", "PERSON", "An engineer"), _entity("Acme Corporation", "COMPANY")],
        # WORKS_AT is a synonym the ontology maps to WORKS_FOR.
        [_fact("Alice", "PERSON", "works at", "Acme Corporation", "COMPANY", "Alice works at Acme")],
    ),
    "Acme Corp. uses Azure.": (
        [_entity("Acme Corp.", "COMPANY"), _entity("Azure", "TECHNOLOGY")],
        [_fact("Acme Corp.", "COMPANY", "USES", "Azure", "TECHNOLOGY")],
    ),
    "Dell provides servers to Microsoft Corporation.": (
        [_entity("Dell", "COMPANY"), _entity("Microsoft Corporation", "COMPANY")],
        [_fact("Dell", "COMPANY", "PROVIDES_TO", "Microsoft Corporation", "COMPANY",
               "Dell provides servers to Microsoft", 0.8)],
    ),
    "Microsoft Corp. uses Azure.": (
        [_entity("Microsoft Corp.", "CORPORATION"), _entity("Azure", "TECHNOLOGY")],
        [_fact("Microsoft Corp.", "CORPORATION", "USES", "Azure", "TECHNOLOGY")],
    ),
    "Azure is owned by Microsoft.": (
        [_entity("Azure", "TECHNOLOGY"), _entity("Microsoft", "COMPANY")],
        # OWNED_BY is an inverse: stored as Microsoft -OWNS-> Azure.
        [_fact("Azure", "TECHNOLOGY", "OWNED_BY", "Microsoft", "COMPANY")],
    ),
    "Bob lives in Chennai.": (
        [_entity("Bob", "PERSON"), _entity("Chennai", "LOCATION")],
        [_fact("Bob", "PERSON", "LIVES_IN", "Chennai", "LOCATION")],
    ),
    "International Business Machines builds Watson.": (
        [_entity("International Business Machines", "COMPANY", aliases=["IBM"]),
         _entity("Watson", "PRODUCT")],
        [_fact("International Business Machines", "COMPANY", "PRODUCES", "Watson", "PRODUCT")],
    ),
    "IBM sells Watson to Globex.": (
        [_entity("IBM", "COMPANY"), _entity("Watson", "PRODUCT"), _entity("Globex", "COMPANY")],
        [_fact("IBM", "COMPANY", "sells to", "Globex", "COMPANY")],
    ),
}


class FakeGraphLLM:
    provider = "build-nvidia"

    def __init__(self, facts=None, fail_when=()):
        self.facts = facts if facts is not None else FACTS
        # Any chunk containing one of these strings fails, like a model outage.
        self.fail_when = set(fail_when)
        self.calls: list[dict] = []

    def chat_json(self, model, system, user):
        self.calls.append({"model": model, "system": system, "user": user})
        if any(marker in user for marker in self.fail_when):
            raise LLMError("graph model down", usage=LLMUsage(7, 0, 3))
        entities, relationships = [], []
        for sentence, (ents, rels) in self.facts.items():
            if sentence in user:
                entities.extend(ents)
                relationships.extend(rels)
        return (
            {"entities": entities, "relationships": relationships},
            LLMUsage(prompt_tokens=500, completion_tokens=100, latency_ms=10),
        )

    def read_image(self, model, image, mime, prompt):  # pragma: no cover - unused
        raise LLMError("the graph model reads no images")


def content(*paragraphs: str, page: int = 1) -> str:
    """Extraction content in the agent's format, one paragraph per line group."""
    return f"<!-- page {page} · easy · text -->\n" + "\n\n".join(paragraphs)


def padded(sentence: str, filler: int = 120) -> str:
    """~170 chars: at chunk_chars=200 and no overlap, each is a chunk of its own."""
    return sentence + " " + ("lorem " * (filler // 6)).strip()
