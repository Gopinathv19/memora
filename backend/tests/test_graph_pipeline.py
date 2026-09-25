"""Ingestion and retrieval against a real FalkorDB, with a fake graph model.

Covers the graph's contract: extraction lands as entities and typed facts,
variants dedupe, every fact keeps its provenance, multi-hop retrieval,
tenant and subject isolation, idempotent rebuilds, partial failure and retry,
and deletion that keeps shared entities alive.
"""

import uuid

from tests.graph_fakes import content, padded

TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())


def _ingest(graph, text, *, tenant=TENANT_A, source=None, version=1, **kwargs):
    source = source or str(uuid.uuid4())
    result = graph.ingestion.ingest_document(
        tenant_id=tenant,
        application_id="app",
        subject_id="subject",
        source_id=source,
        extraction_id=str(uuid.uuid4()),
        extraction_version=version,
        build_id=str(uuid.uuid4()),
        content=text,
        filename="doc.pdf",
        **kwargs,
    )
    return source, result


def _names(result):
    return sorted(e.name for e in result.entities)


def _facts(result):
    return sorted((r.source_name, r.relation, r.target_name) for r in result.relationships)


def test_extraction_becomes_entities_and_typed_facts(graph):
    source, result = _ingest(graph, content("Alice works at Acme Corporation."))
    assert result.status.value == "completed"
    assert (result.entity_count, result.relationship_count) == (2, 1)

    view = graph.retrieval.view(tenant_id=TENANT_A, source_ids=[source])
    assert _names(view) == ["Acme Corporation", "Alice"]
    assert _facts(view) == [("Alice", "WORKS_FOR", "Acme Corporation")]
    (fact,) = view.relationships
    assert fact.raw_relation == "WORKS_AT"
    assert fact.source_ids == [source] and fact.source_chunk_ids == [f"{source}:1:0"]
    alice = next(e for e in view.entities if e.name == "Alice")
    assert alice.entity_type == "PERSON" and alice.description == "An engineer"


def test_company_variants_are_one_node_across_sources(graph):
    one, _ = _ingest(graph, content("Dell provides servers to Microsoft Corporation."))
    two, _ = _ingest(graph, content("Microsoft Corp. uses Azure."))
    three, _ = _ingest(graph, content("Azure is owned by Microsoft."))
    view = graph.retrieval.view(tenant_id=TENANT_A, source_ids=[one, two, three])
    assert _names(view) == ["Azure", "Dell", "Microsoft Corporation"]
    microsoft = next(e for e in view.entities if e.name == "Microsoft Corporation")
    assert sorted(microsoft.source_ids) == sorted([one, two, three])
    assert _facts(view) == [
        ("Dell", "PROVIDES_TO", "Microsoft Corporation"),
        ("Microsoft Corporation", "OWNS", "Azure"),
        ("Microsoft Corporation", "USES", "Azure"),
    ]


def test_multi_hop_retrieval_is_bounded_by_max_hops(graph):
    source, _ = _ingest(
        graph,
        content("Dell provides servers to Microsoft Corporation.", "Microsoft Corp. uses Azure."),
    )
    two = graph.retrieval.retrieve(
        tenant_id=TENANT_A, source_ids=[source], query="Who does Dell provide to?", max_hops=2
    )
    assert _names(two) == ["Azure", "Dell", "Microsoft Corporation"]
    assert _facts(two) == [
        ("Dell", "PROVIDES_TO", "Microsoft Corporation"),
        ("Microsoft Corporation", "USES", "Azure"),
    ]
    assert two.source_chunk_ids == [f"{source}:1:0"]
    assert "Dell provides servers" in two.chunks[0].text
    assert two.seed_entity_ids == [e.entity_id for e in two.entities if e.name == "Dell"]

    one = graph.retrieval.retrieve(
        tenant_id=TENANT_A, source_ids=[source], query="Dell", max_hops=1
    )
    assert _names(one) == ["Dell", "Microsoft Corporation"]

    capped = graph.retrieval.retrieve(
        tenant_id=TENANT_A, source_ids=[source], query="Dell", max_hops=2, max_entities=2
    )
    assert len(capped.entities) == 2


def test_alias_and_fulltext_find_seed_entities(graph):
    source, _ = _ingest(
        graph,
        content("International Business Machines builds Watson.", "IBM sells Watson to Globex."),
    )
    by_alias = graph.retrieval.retrieve(
        tenant_id=TENANT_A, source_ids=[source], query="What does IBM make?", max_hops=1
    )
    assert "International Business Machines" in _names(by_alias)
    by_word = graph.retrieval.retrieve(
        tenant_id=TENANT_A, source_ids=[source], query="watson customers", max_hops=1
    )
    assert "Watson" in _names(by_word)
    nothing = graph.retrieval.retrieve(
        tenant_id=TENANT_A, source_ids=[source], query="what is the weather", max_hops=1
    )
    assert nothing.entities == [] and nothing.relationships == []


def test_tenants_never_see_each_other(graph):
    a, _ = _ingest(graph, content("Alice works at Acme Corporation."), tenant=TENANT_A)
    b, _ = _ingest(graph, content("Bob lives in Chennai."), tenant=TENANT_B)
    assert graph.store.graph_name(TENANT_A) != graph.store.graph_name(TENANT_B)
    # Even handed tenant A's source id, tenant B's graph has nothing of it.
    leak = graph.retrieval.retrieve(tenant_id=TENANT_B, source_ids=[a, b], query="Alice Acme")
    assert leak.entities == []
    assert _names(graph.retrieval.view(tenant_id=TENANT_B, source_ids=[a, b])) == ["Bob", "Chennai"]


def test_views_only_show_facts_from_sources_in_scope(graph):
    """Entities are tenant-wide; facts are filtered by the sources in scope."""
    workspace_1, _ = _ingest(graph, content("Microsoft Corp. uses Azure."))
    workspace_2, _ = _ingest(graph, content("Azure is owned by Microsoft."))
    view = graph.retrieval.view(tenant_id=TENANT_A, source_ids=[workspace_1])
    assert _facts(view) == [("Microsoft Corp.", "USES", "Azure")]
    azure = next(e for e in view.entities if e.name == "Azure")
    assert azure.source_ids == [workspace_1]
    assert graph.retrieval.view(tenant_id=TENANT_A, source_ids=[]).entities == []


def test_rebuilding_is_idempotent(graph):
    text = content("Dell provides servers to Microsoft Corporation.", "Microsoft Corp. uses Azure.")
    source, first = _ingest(graph, text)
    _, second = _ingest(graph, text, source=source)
    assert (first.entity_count, first.relationship_count) == (second.entity_count, second.relationship_count)
    counts = graph.store._read(
        TENANT_A,
        "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n ORDER BY label",
    )
    assert {r["label"]: r["n"] for r in counts} == {"Chunk": 1, "Document": 1, "Entity": 3}
    edges = graph.store._read(TENANT_A, "MATCH (:Entity)-[r]->(:Entity) RETURN count(r) AS n")
    assert edges[0]["n"] == 2


def test_a_new_version_replaces_the_old_facts(graph):
    source, _ = _ingest(graph, content("Alice works at Acme Corporation."), version=1)
    _ingest(graph, content("Bob lives in Chennai."), source=source, version=2)
    view = graph.retrieval.view(tenant_id=TENANT_A, source_ids=[source])
    assert _names(view) == ["Bob", "Chennai"]
    assert view.relationships[0].source_chunk_ids == [f"{source}:2:0"]


def test_deleting_a_source_keeps_entities_other_sources_support(graph):
    one, _ = _ingest(graph, content("Microsoft Corp. uses Azure.", "Bob lives in Chennai."))
    two, _ = _ingest(graph, content("Azure is owned by Microsoft."))
    removed = graph.store.delete_source(TENANT_A, one)
    assert removed["entities"] == 2  # Bob and Chennai: nothing else mentions them
    view = graph.retrieval.view(tenant_id=TENANT_A, source_ids=[one, two])
    assert _names(view) == ["Azure", "Microsoft Corp."]
    assert _facts(view) == [("Microsoft Corp.", "OWNS", "Azure")]
    assert graph.store.document_ids(TENANT_A) == [two]


def test_a_failed_chunk_makes_the_build_partial_and_is_retryable(graph):
    text = content(
        padded("Alice works at Acme Corporation."),
        padded("Acme Corp. uses Azure. FAIL-ME"),
        padded("Bob lives in Chennai."),
    )
    graph.llm.fail_when = {"FAIL-ME"}
    source, first = _ingest(graph, text, chunk_chars=200, chunk_overlap=0)
    assert first.status.value == "partial"
    assert [f["index"] for f in first.failed_chunks] == [1]
    assert first.failed_chunks[0]["error"] == "graph model down"
    assert len(first.usage) == 3 and first.usage[1].status == "failed"
    assert ("Acme Corporation", "USES", "Azure") not in _facts(
        graph.retrieval.view(tenant_id=TENANT_A, source_ids=[source])
    )

    graph.llm.fail_when = set()
    calls_before = len(graph.llm.calls)
    _, retry = _ingest(graph, text, source=source, chunk_chars=200, chunk_overlap=0, only_chunks={1})
    assert retry.status.value == "completed"
    assert len(graph.llm.calls) - calls_before == 1  # only the failed chunk was re-read
    view = graph.retrieval.view(tenant_id=TENANT_A, source_ids=[source])
    assert _facts(view) == [
        ("Acme Corporation", "USES", "Azure"),
        ("Alice", "WORKS_FOR", "Acme Corporation"),
        ("Bob", "LOCATED_IN", "Chennai"),
    ]


def test_when_every_chunk_fails_the_old_graph_survives(graph):
    source, _ = _ingest(graph, content("Alice works at Acme Corporation."))
    graph.llm.fail_when = {"Chennai"}
    _, result = _ingest(graph, content("Bob lives in Chennai."), source=source, version=2)
    assert result.status.value == "failed" and "nothing was written" in result.error
    assert _names(graph.retrieval.view(tenant_id=TENANT_A, source_ids=[source])) == [
        "Acme Corporation",
        "Alice",
    ]


def test_empty_content_fails_without_calling_the_model(graph):
    _, result = _ingest(graph, "<!-- page 1 · easy · text -->\n  ")
    assert result.status.value == "failed" and graph.llm.calls == []


def test_chunks_over_the_limit_are_recorded_not_dropped(graph, monkeypatch):
    monkeypatch.setattr(graph.ingestion.settings, "graph_max_chunks", 2)
    text = content(*(padded(f"Sentence {i}.") for i in range(4)))
    _, result = _ingest(graph, text, chunk_chars=200, chunk_overlap=0)
    assert result.status.value == "partial"
    assert [f["index"] for f in result.failed_chunks] == [2, 3]
    assert "GRAPH_MAX_CHUNKS" in result.failed_chunks[0]["error"]
