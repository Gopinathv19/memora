"""Window extraction: quote verification, entity linking, type mapping, gleaning (no LLM, no DB)."""

import pytest

from app.core.config import get_settings
from app.graph.ontology import get_ontology
from app.graphrag import extract
from app.graphrag.extract import (
    WindowExtractor, glean_message, merge, needs_gleaning, norm_quote, parse_reply, system_prompt,
)
from app.graphrag.windows import ChunkRef, pack_windows
from app.llm.client import LLMError

C1 = ("Section 4.2 - Termination. Either party may terminate this Agreement with 30 days' written notice.")
C2 = ('Acme Corp ("Supplier") shall refund any prepaid fees to Beta Ltd within 14 days of termination.')


def make_window(*texts):
    chunks = [ChunkRef(id=f"c{i}", index=i, text=t, tokens=max(1, len(t) // 4)) for i, t in enumerate(texts)]
    block = {"id": "b1", "section_path": ["Contract", "Termination"], "title": None, "chunks": chunks}
    return pack_windows([block], source_id="s1", extraction_id="e1", source_version=1, filename="contract.pdf")[0]


ONT = get_ontology()
GOOD = {
    "entities": [
        {"name": "Acme Corp", "type": "COMPANY", "description": "The supplier.", "aliases": ["Supplier"]},
        {"name": "Beta Ltd", "type": "company"},
        {"name": "Agreement", "type": "DOCUMENT"},
    ],
    "relationships": [
        {"source": "Acme Corp", "relation": "PROVIDES_TO", "target": "Beta Ltd",
         "quote": 'Acme Corp ("Supplier") shall refund any prepaid fees to Beta Ltd'},
    ],
    "facts": [
        {"text": "Either party may terminate the Agreement with 30 days' written notice.",
         "entities": ["Agreement"], "quote": "Either party may terminate this Agreement with 30 days' written notice",
         "qualifiers": {"duration": "30 days", "date": "", "amount": ""}},
        {"text": "Acme Corp must refund prepaid fees to Beta Ltd within 14 days of termination.",
         "entities": ["Acme Corp", "Beta Ltd"], "quote": "within 14 days of termination",
         "qualifiers": {"deadline": "14 days after termination"}},
    ],
}


def test_good_reply_is_kept_with_exact_chunk_provenance():
    out = parse_reply(GOOD, make_window(C1, C2), ONT)
    assert [e.name for e in out.entities] == ["Acme Corp", "Beta Ltd", "Agreement"]
    acme = out.entities[0]
    assert acme.entity_type == "COMPANY" and acme.chunk_ids == ["c1"]
    assert out.entities[1].entity_type == "COMPANY"               # "company" mapped to the ontology type
    assert out.entities[2].chunk_ids == ["c0"]                    # "Agreement" appears in chunk 0 only
    assert out.relations[0].chunk_ids == ["c1"]
    assert [f.chunk_ids for f in out.facts] == [["c0"], ["c1"]]
    assert out.facts[0].qualifiers == {"duration": "30 days"}      # empty qualifiers are dropped
    assert out.stats["dropped_quote_missing"] == 0


def test_item_with_a_quote_not_in_the_window_is_dropped():
    bad = {"entities": GOOD["entities"], "relationships": [
        {"source": "Acme Corp", "relation": "OWNS", "target": "Beta Ltd",
         "quote": "Acme Corp owns Beta Ltd outright since 2019"}],
        "facts": [{"text": "Fees are 5 percent.", "entities": [], "quote": "fees are five percent of revenue"}]}
    out = parse_reply(bad, make_window(C1, C2), ONT)
    assert out.relations == [] and out.facts == []
    assert out.stats["dropped_quote_missing"] == 2


def test_short_quote_and_malformed_items_are_dropped_not_fatal():
    raw = {"entities": ["junk", {"name": ""}, {"name": "Acme Corp", "type": "COMPANY"}],
           "relationships": [{"source": "Acme Corp", "relation": "OWNS", "target": "Beta Ltd", "quote": "Acme"},
                             "nonsense", {"source": "", "relation": "X", "target": "Y"}],
           "facts": [{"text": "x", "quote": ""}, 7]}
    out = parse_reply(raw, make_window(C1, C2), ONT)
    assert [e.name for e in out.entities] == ["Acme Corp"]
    assert out.stats["dropped_quote_short"] == 2 and out.stats["dropped_malformed"] >= 4


def test_quote_matching_folds_case_whitespace_quotes_and_ellipsis():
    w = make_window("The  Supplier shall\nrefund the “prepaid” fees promptly after notice.")
    raw = {"entities": [{"name": "Supplier", "type": "CONCEPT"}], "relationships": [], "facts": [
        {"text": "The Supplier refunds prepaid fees.", "entities": ["Supplier"],
         "quote": '...the supplier shall refund the "prepaid" fees...'}]}
    out = parse_reply(raw, w, ONT)
    assert len(out.facts) == 1
    assert norm_quote(" …Hello  World. ") == "hello world"


def test_quote_may_span_two_adjacent_chunks_but_no_further():
    w = make_window("The notice period is thirty days", "from the date of written notice by either party.", "Unrelated tail text here.")
    spanning = {"entities": [], "relationships": [], "facts": [
        {"text": "Notice is thirty days.", "entities": [],
         "quote": "notice period is thirty days from the date of written notice"}]}
    out = parse_reply(spanning, w, ONT)
    assert out.facts[0].chunk_ids == ["c0", "c1"]
    far = {"entities": [], "relationships": [], "facts": [
        {"text": "x y z", "entities": [], "quote": "thirty days from the date of written notice by either party. unrelated tail"}]}
    assert parse_reply(far, w, ONT).facts == []


def test_entity_no_chunk_states_is_dropped_and_relations_to_it_go_with_it():
    raw = {"entities": [{"name": "Ghost Inc", "type": "COMPANY"}, {"name": "Acme Corp", "type": "COMPANY"}],
           "relationships": [], "facts": []}
    out = parse_reply(raw, make_window(C1, C2), ONT)
    assert [e.name for e in out.entities] == ["Acme Corp"]
    assert out.stats["entities_unlinked"] == 1


def test_endpoint_missing_from_entities_is_added_when_its_relation_is_supported():
    raw = {"entities": [{"name": "Acme Corp", "type": "COMPANY"}], "facts": [], "relationships": [
        {"source": "Acme Corp", "relation": "PROVIDES_TO", "target": "Beta Ltd", "target_type": "COMPANY",
         "quote": "shall refund any prepaid fees to Beta Ltd within 14 days"}]}
    out = parse_reply(raw, make_window(C1, C2), ONT)
    assert {e.name for e in out.entities} == {"Acme Corp", "Beta Ltd"}
    assert out.relations[0].target == "Beta Ltd"


def test_inverse_relation_is_swapped_and_unknown_relation_falls_back_and_is_counted():
    raw = {"entities": [], "facts": [], "relationships": [
        {"source": "Beta Ltd", "relation": "CUSTOMER_OF", "target": "Acme Corp",
         "quote": "refund any prepaid fees to Beta Ltd"},
        {"source": "Acme Corp", "relation": "OTHER:REFUNDS", "target": "Beta Ltd",
         "quote": "shall refund any prepaid fees"},
        {"source": "Acme Corp", "relation": "invoices", "target": "Beta Ltd",
         "quote": "Acme Corp (\"Supplier\") shall refund"}]}
    out = parse_reply(raw, make_window(C1, C2), ONT)
    first = out.relations[0]
    assert (first.source, first.relation, first.target) == ("Acme Corp", "PROVIDES_TO", "Beta Ltd")  # CUSTOMER_OF inverted
    assert {r.relation for r in out.relations[1:]} == {ONT.fallback_relation}
    assert all(ONT.is_relation(r.relation) for r in out.relations)                                 # Cypher-safe names only
    assert out.proposed_relations["REFUNDS"] == 1 and out.proposed_relations["INVOICES"] == 1


def test_duplicate_relations_and_facts_collapse():
    rel = GOOD["relationships"][0]
    raw = {"entities": GOOD["entities"], "relationships": [rel, dict(rel)], "facts": [GOOD["facts"][1], dict(GOOD["facts"][1])]}
    out = parse_reply(raw, make_window(C1, C2), ONT)
    assert len(out.relations) == 1 and len(out.facts) == 1 and out.stats["duplicate"] == 2


def test_system_prompt_lists_ontology_escape_hatch_and_version():
    p = system_prompt(ONT)
    assert extract.PROMPT_VERSION in p and "OTHER:<LABEL>" in p
    assert all(name in p for name in ("WORKS_FOR", "COMPANY"))


# --- gleaning ----------------------------------------------------------------------------


def _dense(n):
    return {"entities": [{"name": f"Entity{i} Corp", "type": "COMPANY"} for i in range(n)], "relationships": [], "facts": []}


def test_needs_gleaning_is_coverage_density_and_size_gated():
    text = " ".join(f"Entity{i} Corp" for i in range(10)) + " " + "padding " * 200
    w = make_window(text)
    assert needs_gleaning(parse_reply(_dense(10), w, ONT), w)             # 10 entities, nothing else: under-extracted
    assert not needs_gleaning(parse_reply(_dense(3), w, ONT), w)          # too few items to bother
    rich = _dense(10)
    rich["relationships"] = [
        {"source": f"Entity{i} Corp", "relation": "PROVIDES_TO", "target": f"Entity{i + 1} Corp",
         "quote": f"Entity{i} Corp Entity{i + 1} Corp"} for i in range(8)
    ]
    assert not needs_gleaning(parse_reply(rich, w, ONT), w)               # 8 relations for 10 entities: well covered
    tiny = make_window("Entity0 Corp Entity1 Corp Entity2 Corp Entity3 Corp Entity4 Corp Entity5 Corp Entity6 Corp Entity7 Corp")
    assert not needs_gleaning(parse_reply(_dense(8), tiny, ONT), tiny)       # window too small to bother


def test_merge_adds_only_what_is_new():
    w = make_window(C1, C2)
    a = parse_reply(GOOD, w, ONT)
    extra = parse_reply({"entities": [{"name": "Acme Corp", "type": "COMPANY", "aliases": ["Seller"]},
                                      {"name": "Agreement", "type": "DOCUMENT"}],
                         "relationships": GOOD["relationships"], "facts": GOOD["facts"]}, w, ONT)
    n_entities, n_relations, n_facts = len(a.entities), len(a.relations), len(a.facts)
    merged = merge(a, extra)
    assert (len(merged.entities), len(merged.relations), len(merged.facts)) == (n_entities, n_relations, n_facts)
    assert "Seller" in merged.entities[0].aliases and merged.gleaned


def test_glean_message_lists_what_was_found():
    w = make_window(C1, C2)
    msg = glean_message(w, parse_reply(GOOD, w, ONT))
    assert "Acme Corp" in msg and "PROVIDES_TO" in msg and "ONLY what is missing" in msg


# --- the extractor with a fake model -----------------------------------------------------


class FakeUsage:
    prompt_tokens, completion_tokens, latency_ms = 10, 5, 1


def patch_calls(monkeypatch, replies):
    calls = []

    def fake(client, model, system, user):
        calls.append(user)
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply, FakeUsage(), False

    monkeypatch.setattr(extract, "cached_chat_json", fake)
    return calls


def extractor(**kw):
    return WindowExtractor(client=None, settings=get_settings(), ontology=ONT, **kw)


def test_extractor_makes_one_call_for_a_sparse_window(monkeypatch):
    calls = patch_calls(monkeypatch, [GOOD])
    result = extractor().extract(make_window(C1, C2))
    assert result.ok and len(calls) == 1 and not result.extraction.gleaned
    assert "Document: contract.pdf" in calls[0] and "[Section: Contract > Termination]" in calls[0]
    assert len(result.usage) == 1


def test_extractor_gleans_a_dense_window_once_and_merges(monkeypatch):
    text = " ".join(f"Entity{i} Corp" for i in range(10)) + " " + "padding " * 200
    extra = {"entities": [{"name": "Entity0 Corp", "type": "COMPANY", "aliases": ["E0"]}], "relationships": [], "facts": []}
    calls = patch_calls(monkeypatch, [_dense(10), extra])
    result = extractor().extract(make_window(text))
    assert len(calls) == 2 and "ONLY what is missing" in calls[1]
    assert result.extraction.gleaned and len(result.extraction.entities) == 10
    assert "E0" in result.extraction.entities[0].aliases


def test_gleaning_can_be_switched_off(monkeypatch):
    text = " ".join(f"Entity{i} Corp" for i in range(10)) + " " + "padding " * 200
    calls = patch_calls(monkeypatch, [_dense(10)])
    assert extractor(glean=False).extract(make_window(text)).ok and len(calls) == 1


def test_failed_first_call_returns_an_error_result_and_failed_glean_keeps_the_first_pass(monkeypatch):
    patch_calls(monkeypatch, [LLMError("boom")])
    failed = extractor().extract(make_window(C1, C2))
    assert not failed.ok and "boom" in failed.error

    text = " ".join(f"Entity{i} Corp" for i in range(10)) + " " + "padding " * 200
    patch_calls(monkeypatch, [_dense(10), LLMError("second look failed")])
    kept = extractor().extract(make_window(text))
    assert kept.ok and len(kept.extraction.entities) == 10 and not kept.extraction.gleaned


# --- findings from the first real-model run ----------------------------------------------


def test_markdown_emphasis_in_the_text_does_not_break_quote_or_entity_matching():
    # real chunk text: "**3. Create the interface contract.** Define a simple PromptConfig dataclass ..."
    w = make_window("**3. Create the interface contract.** Define a simple `PromptConfig` dataclass for **Acme Corp** pipelines.")
    raw = {"entities": [{"name": "Acme Corp", "type": "COMPANY"}], "relationships": [], "facts": [
        {"text": "A PromptConfig dataclass defines the interface contract.", "entities": ["Acme Corp"],
         "quote": "Create the interface contract. Define a simple PromptConfig dataclass"}]}
    out = parse_reply(raw, w, ONT)
    assert len(out.facts) == 1 and out.stats["dropped_quote_missing"] == 0
    assert [e.name for e in out.entities] == ["Acme Corp"] and out.entities[0].chunk_ids == ["c0"]


def test_merge_counts_items_the_second_pass_only_repeated():
    w = make_window(C1, C2)
    first = parse_reply(GOOD, w, ONT)
    again = parse_reply(GOOD, w, ONT)
    merged = merge(first, again)
    assert merged.stats["glean_repeated"] == len(GOOD["relationships"]) + len(GOOD["facts"])


def test_extract_windows_keeps_order_and_isolates_failures():
    from app.graphrag.extract import WindowResult, extract_windows

    class Fake:
        def extract(self, window):
            if window.id.endswith(":1"):
                return WindowResult(window, None, [], "boom")
            return WindowResult(window, extract.WindowExtraction(window.id))

    wins = []
    for i in range(3):
        w = make_window(C1)
        w.id = f"e1:{i}"
        wins.append(w)
    results = extract_windows(Fake(), wins, concurrency=2)
    assert [r.window.id for r in results] == ["e1:0", "e1:1", "e1:2"]
    assert [r.ok for r in results] == [True, False, True]
    assert extract_windows(Fake(), []) == []


def test_rejected_items_are_kept_for_audit_with_their_reason():
    raw = {"entities": [], "relationships": [
        {"source": "Acme Corp", "relation": "OWNS", "target": "Beta Ltd", "quote": "Acme Corp owns Beta Ltd outright since 2019"},
        {"source": "Acme Corp", "relation": "OWNS", "target": "Beta Ltd", "quote": "Acme"}],
        "facts": [{"text": "Fees are 5 percent.", "entities": [], "quote": "fees are five percent of revenue"}]}
    out = parse_reply(raw, make_window(C1, C2), ONT)
    assert [(r["kind"], r["reason"]) for r in out.rejected] == [("relation", "missing"), ("relation", "short"), ("fact", "missing")]
    assert out.rejected[0]["quote"].startswith("Acme Corp owns") and out.stats["dropped_quote_missing"] == 2


def test_leading_bullets_brackets_and_control_characters_do_not_break_a_quote():
    w = make_window("(\x08lacktriangleright) Each subscriber only receives a subset of the messages.\n• Bitcoin uses SHA-256 to secure data.")
    raw = {"entities": [], "relationships": [], "facts": [
        {"text": "Subscribers receive a subset.", "entities": [], "quote": "Each subscriber only receives a subset of the messages."},
        {"text": "Bitcoin uses SHA-256.", "entities": [], "quote": "• Bitcoin uses **SHA-256** to secure data."},
        {"text": "x", "entities": [], "quote": "(\x08lacktriangleright) Each subscriber only receives a subset"}]}
    out = parse_reply(raw, w, ONT)
    assert len(out.facts) == 3 and out.stats["dropped_quote_missing"] == 0
    assert norm_quote("- (Hello World); ") == "hello world"


def test_prompt_asks_for_verbatim_quotes_from_the_text_not_the_labels():
    p = system_prompt(ONT)
    assert "[Section: ...] label" in p and "Document line" in p and "graphrag-extract-4" in p
    assert "at most 25 words" in p          # fewer rejected quotes at the same kept count (see the module docstring)


def test_bullet_glyphs_and_hyphens_match_across_list_markers():
    w = make_window("Without blockchain:\n* There would be **no immutable ledger** to store history.\n- Co-op nodes verify blocks.")
    raw = {"entities": [], "relationships": [], "facts": [
        {"text": "No ledger without blockchain.", "entities": [],
         "quote": "Without blockchain: \u2022 There would be **no immutable ledger** to store history."},
        {"text": "Co-op nodes verify blocks.", "entities": [], "quote": "Coop nodes verify blocks"}]}
    out = parse_reply(raw, w, ONT)
    assert len(out.facts) == 2 and out.stats["dropped_quote_missing"] == 0


# --- replies of the wrong shape (untrusted model output) ------------------------------------------


@pytest.mark.parametrize("reply", [
    {"entities": {"a": 1}, "relationships": {"x": 1}, "facts": {"y": 2}},
    {"entities": "Acme", "relationships": 7, "facts": None},
    {"entities": [None, 3, [], "x"], "relationships": [None, [], 5], "facts": [None, "text", []]},
    [], "not a dict", None, 42,
])
def test_a_reply_of_the_wrong_shape_gives_an_empty_extraction_not_an_exception(reply):
    out = parse_reply(reply, make_window(C1, C2), ONT)
    assert out.entities == [] and out.relations == [] and out.facts == []


def test_an_unreadable_reply_fails_that_window_only(monkeypatch):
    from app.graphrag import extract as X

    def explode(raw, window, ontology):
        raise ValueError("boom while reading")

    patch_calls(monkeypatch, [GOOD])
    monkeypatch.setattr(X, "parse_reply", explode)
    result = extractor().extract(make_window(C1, C2))
    assert not result.ok and "unreadable reply" in result.error and "boom while reading" in result.error


def test_an_extractor_that_raises_costs_one_window_not_the_whole_batch():
    from app.graphrag.extract import WindowResult, extract_windows

    class Sometimes:
        def extract(self, window):
            if window.id.endswith(":1"):
                raise RuntimeError("kaboom")
            return WindowResult(window, extract.WindowExtraction(window.id))

    wins = []
    for i in range(3):
        w = make_window(C1)
        w.id = f"e1:{i}"
        wins.append(w)
    results = extract_windows(Sometimes(), wins, concurrency=2)
    assert [r.ok for r in results] == [True, False, True] and "kaboom" in results[1].error and results[1].window is wins[1]
