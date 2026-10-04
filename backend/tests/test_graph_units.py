"""The graph pipeline's pure stages: ontology, normalization, chunking,
resolution and response validation. No graph database, no model."""

import json

import pytest

from app.agents.extraction_agent import UsageRecord
from app.core.config import get_settings
from app.graph.chunking import MARKER, chunk_content
from app.graph.extraction import GraphExtractor, parse_extraction
from app.graph.models import GraphTextUnit, KnownEntity
from app.graph.normalization import acronym, canonical_key, display_name
from app.graph.ontology import (
    OntologyError,
    get_ontology,
    load_ontology,
    normalize_label,
    parse_ontology,
)
from app.graph.resolution import EntityResolver, entity_id, resolve_graph
from app.schemas.enums import ModelRole
from tests.graph_fakes import FACTS, FakeGraphLLM

TENANT = "7b0c9b3e-2f11-4a57-9d1b-1d2f3e4a5b6c"


# --- ontology ---------------------------------------------------------------------------


def test_shipped_ontology_is_valid():
    ontology = load_ontology(get_settings().graph_ontology_file)
    assert "WORKS_FOR" in ontology.relation_types
    assert ontology.fallback_relation == "RELATED_TO"
    assert ontology.fallback_entity_type == "CONCEPT"


def test_types_are_mapped_never_invented():
    ontology = get_ontology()
    assert normalize_label("works at") == "WORKS_AT"
    assert normalize_label("worksFor") == "WORKS_FOR"
    assert ontology.map_entity_type("company") == ("COMPANY", None)
    assert ontology.map_entity_type("Corporation") == ("COMPANY", "CORPORATION")
    assert ontology.map_entity_type("spaceship") == ("CONCEPT", "SPACESHIP")
    assert ontology.map_relation("WORKS_FOR").relation == "WORKS_FOR"
    works_at = ontology.map_relation("works at")
    assert (works_at.relation, works_at.reversed, works_at.raw) == ("WORKS_FOR", False, "WORKS_AT")
    owned = ontology.map_relation("owned by")
    assert (owned.relation, owned.reversed) == ("OWNS", True)
    unknown = ontology.map_relation("is the nemesis of")
    assert (unknown.relation, unknown.raw) == ("RELATED_TO", "IS_THE_NEMESIS_OF")


def _valid():
    return {
        "version": 1,
        "fallback_entity_type": "CONCEPT",
        "fallback_relation": "RELATED_TO",
        "entity_types": {"CONCEPT": "x"},
        "relation_types": {"RELATED_TO": "x"},
    }


@pytest.mark.parametrize(
    "change",
    [
        {"relation_types": {"RELATED_TO": "x", "bad name": "x"}},
        {"relation_types": {"RELATED_TO": "x", "WORKS) DELETE (n": "x"}},
        {"relation_types": {"RELATED_TO": "x", "MENTIONS": "x"}},
        {"fallback_relation": "NOT_LISTED"},
        {"relation_synonyms": {"USES": "NOT_LISTED"}},
        {"version": 0},
    ],
)
def test_a_malformed_ontology_is_rejected(change):
    with pytest.raises(OntologyError):
        parse_ontology({**_valid(), **change})


def test_only_whitelisted_relations_reach_cypher():
    ontology = get_ontology()
    assert ontology.is_relation("USES")
    assert not ontology.is_relation("USES]->() DETACH DELETE (n) //")
    assert not ontology.is_relation("MADE_UP")


# --- normalization ------------------------------------------------------------------------


def test_canonical_keys():
    assert display_name('  "IBM   Corp."  ') == "IBM Corp."
    assert canonical_key("Microsoft", "COMPANY") == "microsoft"
    assert canonical_key("Microsoft Corporation", "COMPANY") == "microsoft"
    assert canonical_key("Microsoft Corp.", "COMPANY") == "microsoft"
    assert canonical_key("The Acme Pvt. Ltd.", "COMPANY") == "acme"
    assert canonical_key("I.B.M.", "COMPANY") == "ibm"
    assert canonical_key("Dr. Arun  Kumar", "PERSON") == "arun_kumar"
    # Suffixes are only legal forms on organizations; a person keeps every word.
    assert canonical_key("Maria Co", "PERSON") == "maria_co"
    assert canonical_key("AT&T", "COMPANY") == "at_and_t"
    assert canonical_key("Corp", "COMPANY") == "corp"  # never stripped to nothing
    assert acronym("international_business_machines") == "ibm"
    assert acronym("microsoft") is None


# --- chunking ------------------------------------------------------------------------------


def _doc(pages: dict[int, str]) -> str:
    return "\n\n".join(f"<!-- page {p} · easy · text -->\n{t}" for p, t in pages.items())


def test_chunks_carry_pages_and_offsets():
    text = _doc({1: "First page paragraph.", 2: "Second page.\n\nAnother paragraph."})
    units = chunk_content(text, source_id="s", version=3)
    assert len(units) == 1
    unit = units[0]
    assert unit.id == "s:3:0"
    assert (unit.page_start, unit.page_end) == (1, 2)
    assert "<!--" not in unit.text
    assert text[unit.char_start : unit.char_end].startswith("First page")
    assert text[unit.char_start : unit.char_end].endswith("Another paragraph.")


def test_no_text_is_lost_and_units_respect_the_size():
    paragraphs = [f"Paragraph {i} " + "word " * (20 + i * 7) for i in range(40)]
    text = _doc({1: "\n\n".join(paragraphs[:20]), 2: "\n\n".join(paragraphs[20:])})
    units = chunk_content(text, source_id="s", version=1, chunk_chars=500, overlap=100)
    assert len(units) > 5
    assert all(len(u.text) <= 500 for u in units)
    assert [u.index for u in units] == list(range(len(units)))
    covered = "\n".join(u.text for u in units)
    for paragraph in paragraphs:
        for word in paragraph.split()[:2]:
            assert word in covered
    body = MARKER.sub("", text)
    assert sum(len(p) for p in body.split()) <= sum(len(p) for p in covered.split())


def test_units_overlap():
    paragraphs = [f"P{i} " + "x" * 150 for i in range(6)]
    units = chunk_content(_doc({1: "\n\n".join(paragraphs)}), source_id="s", version=1,
                          chunk_chars=400, overlap=200)
    assert len(units) >= 3
    for before, after in zip(units, units[1:]):
        assert after.text.split("\n\n")[0] in before.text


def test_a_table_is_not_cut_when_it_fits():
    table = "\n".join(["| Item | Qty |", "| --- | --- |"] + [f"| widget {i} | {i} |" for i in range(12)])
    text = _doc({1: "Intro " + "word " * 60, 2: table, 3: "Outro " + "word " * 60})
    units = chunk_content(text, source_id="s", version=1, chunk_chars=400, overlap=0)
    holding = [u for u in units if "| Item | Qty |" in u.text]
    assert len(holding) == 1 and "widget 11" in holding[0].text


def test_an_oversized_block_is_split_not_dropped():
    words = " ".join(f"w{i}" for i in range(2000))
    units = chunk_content(_doc({1: words}), source_id="s", version=1, chunk_chars=500, overlap=0)
    assert all(len(u.text) <= 500 for u in units)
    assert " ".join(u.text for u in units).split() == words.split()


def test_empty_content_has_no_units():
    assert chunk_content("", source_id="s", version=1) == []
    assert chunk_content("<!-- page 1 · easy · text -->\n   \n", source_id="s", version=1) == []


# --- resolution ----------------------------------------------------------------------------


def _unit(index=0, text="") -> GraphTextUnit:
    return GraphTextUnit(id=f"s:1:{index}", source_id="s", index=index, text=text,
                         char_start=0, char_end=len(text), page_start=1, page_end=1)


def _extraction(*sentences):
    raw = {"entities": [], "relationships": []}
    for sentence in sentences:
        entities, relationships = FACTS[sentence]
        raw["entities"] += entities
        raw["relationships"] += relationships
    return parse_extraction(raw)[0]


def test_variants_of_one_company_resolve_to_one_entity():
    graph = resolve_graph(
        [
            (_unit(0), _extraction("Dell provides servers to Microsoft Corporation.")),
            (_unit(1), _extraction("Microsoft Corp. uses Azure.")),
            (_unit(2), _extraction("Azure is owned by Microsoft.")),
        ],
        tenant_id=TENANT, known=[], ontology=get_ontology(),
    )
    companies = [e for e in graph.entities if e.entity_type == "COMPANY"]
    assert sorted(e.display_name for e in companies) == ["Dell", "Microsoft Corporation"]
    microsoft = next(e for e in companies if e.canonical_key == "microsoft")
    # All three spellings share the key "microsoft", so no alias is needed.
    assert microsoft.aliases == set()
    assert "CORPORATION" in microsoft.raw_types
    facts = {(f.source_entity_id, f.relation, f.target_entity_id) for f in graph.relationships}
    azure = next(e for e in graph.entities if e.display_name == "Azure")
    dell = next(e for e in graph.entities if e.display_name == "Dell")
    assert facts == {
        (dell.id, "PROVIDES_TO", microsoft.id),
        (microsoft.id, "USES", azure.id),
        (microsoft.id, "OWNS", azure.id),  # "Azure OWNED_BY Microsoft", reversed
    }


def test_acronyms_and_aliases_join_organizations():
    graph = resolve_graph(
        [
            (_unit(0), _extraction("International Business Machines builds Watson.")),
            (_unit(1), _extraction("IBM sells Watson to Globex.")),
        ],
        tenant_id=TENANT, known=[], ontology=get_ontology(),
    )
    ibm = [e for e in graph.entities if "ibm" in e.alias_keys or e.canonical_key == "ibm"]
    assert len(ibm) == 1
    assert ibm[0].display_name == "International Business Machines"
    relations = {f.relation for f in graph.relationships}
    assert relations == {"PRODUCES", "PROVIDES_TO"}  # "sells to" -> PROVIDES_TO


def test_a_known_entity_in_the_graph_is_reused():
    known = [KnownEntity(id="existing-id", entity_type="COMPANY", canonical_key="acme",
                         display_name="ACME")]
    graph = resolve_graph([(_unit(0), _extraction("Alice works at Acme Corporation."))],
                          tenant_id=TENANT, known=known, ontology=get_ontology())
    acme = next(e for e in graph.entities if e.entity_type == "COMPANY")
    assert (acme.id, acme.is_new, acme.display_name) == ("existing-id", False, "ACME")
    assert graph.stats["entities_matched"] == 1 and graph.stats["entities_created"] == 1


def test_fuzzy_matching_is_strict_and_type_bound():
    known = [KnownEntity(id="k1", entity_type="COMPANY", canonical_key="infosys_technologies",
                         display_name="Infosys Technologies")]
    resolver = EntityResolver(TENANT, known, fuzzy_threshold=92)
    assert resolver.resolve("Infosys Technolgies", "COMPANY").id == "k1"  # OCR typo
    assert resolver.resolve("Infosys Technolgies", "PERSON").id != "k1"
    assert resolver.resolve("Infosys Consulting", "COMPANY").id != "k1"
    assert resolver.resolve("Infotech Solutions", "COMPANY").id != "k1"


def test_new_entity_ids_are_deterministic():
    first = EntityResolver(TENANT, []).resolve("Acme Corp", "COMPANY")
    second = EntityResolver(TENANT, []).resolve("ACME Corporation", "COMPANY")
    assert first.id == second.id == entity_id(TENANT, "COMPANY", "acme")
    other_tenant = EntityResolver("00000000-0000-0000-0000-000000000001", []).resolve("Acme", "COMPANY")
    assert other_tenant.id != first.id


def test_every_fact_records_the_chunks_that_state_it():
    sentence = "Alice works at Acme Corporation."
    graph = resolve_graph(
        [(_unit(0), _extraction(sentence)), (_unit(4), _extraction(sentence))],
        tenant_id=TENANT, known=[], ontology=get_ontology(),
    )
    (fact,) = graph.relationships
    assert fact.relation == "WORKS_FOR" and fact.raw_relation == "WORKS_AT"
    assert fact.chunk_ids == ["s:1:0", "s:1:4"]
    assert {m.chunk_id for m in graph.mentions} == {"s:1:0", "s:1:4"}


def test_relationship_endpoints_are_always_mentioned():
    raw = {"entities": [], "relationships": [
        {"source": "Bob", "source_type": "PERSON", "relation": "LIVES_IN",
         "target": "Chennai", "target_type": "LOCATION"},
        {"source": "Bob", "source_type": "PERSON", "relation": "KNOWS",
         "target": "Bob", "target_type": "PERSON"},
    ]}
    graph = resolve_graph([(_unit(0), parse_extraction(raw)[0])],
                          tenant_id=TENANT, known=[], ontology=get_ontology())
    assert len(graph.entities) == 2 and len(graph.mentions) == 2
    assert graph.stats["relationships_skipped"] == 1  # the self-loop


# --- model response validation -------------------------------------------------------------


def test_malformed_items_are_dropped_not_fatal():
    extraction, dropped = parse_extraction({
        "entities": [
            {"name": "Acme", "type": "COMPANY", "aliases": "ACME"},
            {"name": "", "type": "COMPANY"},
            "not-a-dict",
            {"name": "x" * 400},
        ],
        "relationships": [
            {"source": "Acme", "relation": "USES", "target": "Azure", "confidence": 85},
            {"source": "Acme", "relation": "", "target": "Azure"},
        ],
    })
    assert [e.name for e in extraction.entities] == ["Acme"]
    assert extraction.entities[0].aliases == ["ACME"]
    assert extraction.relationships[0].confidence == 0.85
    assert dropped == 4
    assert parse_extraction({})[0].entities == []


def test_extractor_records_usage_and_failures():
    settings, ontology = get_settings(), get_ontology()
    llm = FakeGraphLLM(fail_when={"boom"})
    extractor = GraphExtractor(llm, settings, ontology)
    good = extractor.extract(_unit(0, "Alice works at Acme Corporation."))
    assert good.ok and len(good.extraction.entities) == 2
    assert isinstance(good.usage, UsageRecord)
    assert (good.usage.role, good.usage.model) == (ModelRole.GRAPH, settings.llm_graph_model)
    assert "WORKS_FOR" in llm.calls[0]["system"] and "<chunk>" in llm.calls[0]["user"]

    bad = extractor.extract(_unit(1, "boom"))
    assert not bad.ok and bad.error == "graph model down"
    assert bad.usage.status == "failed" and bad.usage.prompt_tokens == 7


def test_the_graph_model_must_be_nvidia():
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(llm_graph_model="openai/gpt-x")


def test_graph_calls_are_not_priced_as_images(tmp_path, monkeypatch):
    from app.core import pricing

    path = tmp_path / "p.json"
    path.write_text(json.dumps({"versions": [{"effective_from": "2026-01-01", "providers": {
        "build-nvidia": {"*": {"input_per_1m": 1.0, "per_image": 5.0}}}}]}))
    monkeypatch.setattr(get_settings(), "pricing_file", str(path))
    pricing.get_price_list.cache_clear()
    cost, _ = pricing.price_call("build-nvidia", "nvidia/x", "graph", 1_000_000, 0)
    assert cost == 1.0
    pricing.get_price_list.cache_clear()

