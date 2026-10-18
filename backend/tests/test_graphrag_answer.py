"""The graph answer step: numbered evidence, evidence closure, citations, checks (no database, no model)."""

import uuid
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.graphrag import answer as A
from app.llm.client import LLMError
from app.services.retrieval_service import Candidate

SRC = uuid.uuid4()
TITLES = {str(SRC): "Supply Agreement"}


def cand(text="text", section=("Termination", "Refunds"), page=4, tokens=50, source=SRC):
    return Candidate(
        chunk_id=uuid.uuid4(), source_id=source, subject_id=uuid.uuid4(), extraction_id=uuid.uuid4(), content=text,
        embedding_text=text, token_count=tokens, content_type="text", section_path=list(section), page_start=page,
        page_end=page,
    )


def atom(text, chunk, evidence="quote"):
    return {"text": text, "chunk_ids": [str(chunk.chunk_id)] if hasattr(chunk, "chunk_id") else list(chunk), "evidence": evidence, "similarity": 0.8}


# --- evidence ----------------------------------------------------------------------------------


def test_blocks_are_numbered_in_rank_order_with_document_section_and_page():
    c1, c2 = cand("Refunds are due within 14 days."), cand("Notice is 30 days.", section=("Notice",), page=5)
    ev = A.build_evidence([c1, c2], [], [], TITLES)
    assert [b.marker for b in ev.blocks] == ["C1", "C2"]
    assert ev.get("C1").text.startswith("Document: Supply Agreement · Section: Termination › Refunds · page 4\n")
    assert "Refunds are due within 14 days." in ev.get("C1").text and "Section: Notice" in ev.get("C2").text


def test_a_page_range_and_a_missing_title_or_section_are_handled():
    c = cand(section=(), page=3)
    c.page_end = 5
    text = A.build_evidence([c], [], [], {}).get("C1").text
    assert text.startswith("Document: unknown · pages") is False and "Document: unknown · page 3–5" in text
    assert "Section" not in text


def test_a_fact_is_shown_with_the_passages_that_state_it():
    c1, c2 = cand("one"), cand("two")
    a = atom("Acme must refund within 14 days.", [str(c2.chunk_id), str(c1.chunk_id)])
    ev = A.build_evidence([c1, c2], [a], [], TITLES)
    assert ev.get("F1").text == "Acme must refund within 14 days. (stated in C2, C1)" and ev.get("F1").kind == "fact"
    assert ev.dropped_facts == 0


def test_a_fact_whose_passage_is_not_shown_is_pulled_in_or_dropped():
    shown, other = cand("shown"), cand("the passage behind the fact")
    a = atom("A fact.", other)
    without = A.build_evidence([shown], [a], [], TITLES)
    assert without.get("F1") is None and without.dropped_facts == 1 and len(without.blocks) == 1

    pulled = A.build_evidence([shown], [a], [], TITLES, load_chunk=lambda i: other if i == str(other.chunk_id) else None)
    assert pulled.get("C2").text.endswith("the passage behind the fact") and pulled.pulled_chunks == 1
    assert pulled.get("F1").text == "A fact. (stated in C2)"

    gone = A.build_evidence([shown], [a], [], TITLES, load_chunk=lambda i: None)
    assert gone.get("F1") is None and gone.dropped_facts == 1 and gone.pulled_chunks == 0


def test_the_token_budget_stops_pulling_in_more_passages_and_the_fact_goes_with_it():
    shown, other = cand("a", tokens=90), cand("b", tokens=90)
    ev = A.build_evidence([shown], [atom("A fact.", other)], [], TITLES, load_chunk=lambda i: other, budget=100)
    assert ev.get("C2") is None and ev.get("F1") is None and ev.dropped_facts == 1


def test_at_most_max_facts_are_shown_and_a_fact_with_no_chunks_is_dropped():
    c = cand()
    atoms = [atom(f"Fact {i}.", c) for i in range(5)] + [atom("Orphan.", [])]
    ev = A.build_evidence([c], atoms, [], TITLES, max_facts=3)
    assert [b.marker for b in ev.blocks if b.kind == "fact"] == ["F1", "F2", "F3"]
    ev = A.build_evidence([c], [atom("Orphan.", [])], [], TITLES)
    assert ev.dropped_facts == 1


def test_overviews_are_labelled_as_context_and_one_without_a_summary_is_skipped():
    ev = A.build_evidence([cand()], [], [{"id": "cm1", "title": "Supplier risk", "summary": "Late delivery."},
                                         {"id": "cm2", "title": "Empty", "summary": None}], TITLES)
    g = ev.get("G1")
    assert g.kind == "overview" and "context only, not proof" in g.text and "Supplier risk — Late delivery." in g.text
    assert ev.get("G2") is None and g.chunk_ids == []


def test_the_prompt_lists_every_block_then_the_question():
    ev = A.build_evidence([cand("body")], [atom("A fact.", [])], [], TITLES)
    prompt = ev.prompt("What is due?")
    assert prompt.index("[C1]") < prompt.index("Question: What is due?") and "cite it with the markers" in prompt


def test_the_system_prompt_requires_citations_and_forbids_citing_overviews_as_proof():
    assert "immediately after that sentence" in A.SYSTEM and "[C1][F3]" in A.SYSTEM and "not proof" in A.SYSTEM
    assert "disagree" in A.SYSTEM and "200 words" in A.SYSTEM and A.ANSWER_VERSION in A.SYSTEM


# --- reading the answer ------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("Due in 14 days [C1][F2].", ["C1", "F2"]),
    ("Due in 14 days [C1, C2; F3].", ["C1", "C2", "F3"]),
    ("Due [ C3 ] and again [C3].", ["C3"]),
    ("See [1] and [Chapter 1] and [C] and [X2].", []),
    ("No markers here.", []),
])
def test_markers_in(text, expected):
    assert A.markers_in(text) == expected


def test_claims_split_on_sentences_bullets_and_numbered_lines_but_not_on_decimals():
    answer = "Refunds take 3.5 days [C1]. Notice is 30 days [C2].\n- A bullet claim [F1]\n2. A numbered claim [C1]"
    claims = A.split_claims(answer)
    assert [c["text"] for c in claims] == ["Refunds take 3.5 days [C1].", "Notice is 30 days [C2].", "A bullet claim [F1]", "A numbered claim [C1]"]
    assert [c["markers"] for c in claims] == [["C1"], ["C2"], ["F1"], ["C1"]]


@pytest.mark.parametrize("text,expected", [
    ("within 14 days", {"14"}),
    ("Rs. 9,85,000/- was paid", {"985000"}),
    ("a 5% fee and 3.5 percent", {"5", "3.5"}),
    ("two methods and 7 items", set()),                     # single-digit whole numbers are skipped
    ("see [C12] and [F3][C4]", set()),                      # marker digits are not numbers
    ("on 01/03/2023", {"1", "3", "2023"}),
    ("", set()),
])
def test_numbers_in(text, expected):
    assert A.numbers_in(text) == expected


# --- the checks --------------------------------------------------------------------------------


def evidence_with(*texts, atoms=(), communities=()):
    chunks = [cand(t) for t in texts]
    return A.build_evidence(chunks, [atom(t, chunks[0]) for t in atoms], list(communities), TITLES), chunks


def test_a_well_cited_answer_with_supported_numbers_passes():
    ev, _ = evidence_with("Refunds are due within 14 days of termination.", "Notice is 30 days.")
    v = A.verify("Refunds are due within 14 days [C1]. Notice is 30 days [C2].", ev)
    assert v["ok"] and v["claims"] == 2 and not any(v[k] for k in ("unknown_markers", "uncited_sentences", "unsupported_numbers"))


def test_a_number_that_the_cited_evidence_does_not_contain_is_flagged_with_its_sentence():
    ev, _ = evidence_with("Refunds are due within 14 days of termination.")
    v = A.verify("Refunds are due within 30 days [C1].", ev)
    assert not v["ok"] and v["unsupported_numbers"] == [{"sentence": "Refunds are due within 30 days [C1].", "number": "30"}]


def test_a_number_is_checked_against_what_the_sentence_cites_not_against_everything():
    ev, _ = evidence_with("Refunds are due within 14 days.", "Notice is 30 days.")
    v = A.verify("Refunds are due within 30 days [C1].", ev)           # 30 is in C2, but the sentence cites C1
    assert v["unsupported_numbers"] and v["unsupported_numbers"][0]["number"] == "30"


def test_an_uncited_sentence_is_checked_against_all_the_evidence():
    ev, _ = evidence_with("Refunds are due within 14 days.", "Notice is 30 days.")
    assert A.verify("The notice period is 30 days in total.", ev)["unsupported_numbers"] == []
    assert A.verify("The notice period is 45 days in total.", ev)["unsupported_numbers"][0]["number"] == "45"


def test_a_fact_counts_its_evidence_quote_and_the_passages_behind_it():
    ev, chunks = evidence_with("The deposit is 500 dollars here.", atoms=["Acme holds a deposit."])
    assert A.verify("Acme holds a deposit of 500 dollars [F1].", ev)["unsupported_numbers"] == []


def test_unknown_markers_uncited_substantive_sentences_and_overview_only_support_are_flagged():
    ev, _ = evidence_with("Body text.", communities=[{"id": "g", "title": "T", "summary": "Overview of terms."}])
    v = A.verify("A claim that cites nothing at all here. Another claim [C9]. An overview claim [G1].", ev)
    assert v["uncited_sentences"] == ["A claim that cites nothing at all here."]
    assert v["unknown_markers"] == ["C9"] and v["overview_only_sentences"] == ["An overview claim [G1]."] and not v["ok"]


def test_short_and_abstaining_sentences_need_no_citation():
    ev, _ = evidence_with("Body.")
    assert A.verify("Yes. The available knowledge is insufficient to say.", ev)["uncited_sentences"] == []
    assert A.verify("I could not find this in your documents.", ev)["ok"]


def test_an_overview_does_not_make_its_numbers_checkable_but_does_not_stop_the_check():
    ev, _ = evidence_with("Body 14 days.", communities=[{"id": "g", "title": "T", "summary": "About 99 things."}])
    v = A.verify("There are 99 things [C1][G1].", ev)
    assert v["unsupported_numbers"][0]["number"] == "99"


def test_citations_resolve_markers_and_skip_unknown_ones():
    ev, chunks = evidence_with("Refunds are due.", atoms=["A fact."], communities=[{"id": "cm7", "title": "T", "summary": "S."}])
    got = A.citations("Due [C1][F1][G1][C9].", ev)
    assert [c["marker"] for c in got] == ["C1", "F1", "G1"]
    assert got[0]["document"] == "Supply Agreement" and got[0]["chunk_ids"] == [str(chunks[0].chunk_id)] and got[0]["page_start"] == 4
    assert got[1]["kind"] == "fact" and got[1]["evidence"] == "quote" and got[2]["community_id"] == "cm7"


# --- generating --------------------------------------------------------------------------------


class FakeClient:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def chat_text(self, model, system, user, interactive=False):
        self.calls.append((model, system, user))
        return self.reply, SimpleNamespace(prompt_tokens=100, completion_tokens=20, latency_ms=5)


def test_generate_sends_the_graph_prompt_and_returns_citations_claims_and_checks():
    ev, chunks = evidence_with("Refunds are due within 14 days of termination.")
    client = FakeClient("Refunds are due within 14 days [C1].")
    got = A.generate(client, get_settings(), "When are refunds due?", ev)
    model, system, user = client.calls[0]
    assert system == A.SYSTEM and "[C1]" in user and "Question: When are refunds due?" in user
    assert got.answer.endswith("[C1].") and got.verification["ok"] and got.citations[0]["marker"] == "C1"
    assert got.claims == [{"text": "Refunds are due within 14 days [C1].", "markers": ["C1"]}] and got.llm_check is None
    assert got.usage.prompt_tokens == 100


def test_the_optional_model_check_reports_claims_it_finds_unsupported(monkeypatch):
    ev, _ = evidence_with("Refunds are due within 14 days.", "Notice is 30 days.")
    reply = {"claims": [{"id": 0, "supported": True}, {"id": 1, "supported": False}]}
    seen = []
    monkeypatch.setattr(A, "cached_chat_json", lambda client, model, system, user: seen.append(user) or (reply, None, False))
    got = A.generate(FakeClient("Refunds are due in 14 days [C1]. Notice is 30 days [C2]. An uncited remark here."),
                     get_settings(), "q", ev, check=True)
    assert got.llm_check == {"checked": 2, "unsupported": ["Notice is 30 days [C2]."]}
    assert len(seen) == 1 and "Refunds are due within 14 days." in seen[0]       # the cited evidence was sent


def test_the_model_check_failing_or_having_nothing_to_check_is_not_an_error(monkeypatch):
    ev, _ = evidence_with("Body.")
    monkeypatch.setattr(A, "cached_chat_json", lambda *a: (_ for _ in ()).throw(LLMError("down")))
    assert A.llm_check(None, get_settings(), [{"text": "x [C1]", "markers": ["C1"]}], ev) == {"checked": 0, "unsupported": [], "error": True}
    assert A.llm_check(None, get_settings(), [{"text": "no markers", "markers": []}], ev) == {"checked": 0, "unsupported": []}


def test_pulling_is_capped_so_the_prompt_does_not_balloon():
    shown = cand("shown")
    others = [cand(f"behind fact {i}") for i in range(5)]
    atoms = [atom(f"Fact {i}.", o) for i, o in enumerate(others)]
    by_id = {str(o.chunk_id): o for o in others}
    ev = A.build_evidence([shown], atoms, [], TITLES, load_chunk=by_id.get)
    assert ev.pulled_chunks == A.MAX_PULLED == 2 and ev.dropped_facts == 3
    assert len([b for b in ev.blocks if b.kind == "chunk"]) == 3 and len([b for b in ev.blocks if b.kind == "fact"]) == 2
    unlimited = A.build_evidence([shown], atoms, [], TITLES, load_chunk=by_id.get, max_pulled=9)
    assert unlimited.pulled_chunks == 5 and unlimited.dropped_facts == 0


def test_a_marker_after_the_final_full_stop_belongs_to_the_sentence_before_it():
    claims = A.split_claims("Bitcoin prevents double spending with a ledger. [C1][C2]")
    assert claims == [{"text": "Bitcoin prevents double spending with a ledger. [C1][C2]", "markers": ["C1", "C2"]}]
    claims = A.split_claims("First claim [C1]. Second claim. [F2]")
    assert [c["markers"] for c in claims] == [["C1"], ["F2"]] and claims[1]["text"].startswith("Second claim")


def test_abbreviations_do_not_end_a_sentence():
    claims = A.split_claims("Hacks such as Mt. Gox and Rs. 9,85,000 payments. Dr. Sharma signed it [C1].")
    assert [c["text"] for c in claims] == ["Hacks such as Mt. Gox and Rs. 9,85,000 payments.", "Dr. Sharma signed it [C1]."]


def test_a_trailing_marker_group_no_longer_leaves_its_sentence_uncited():
    ev, _ = evidence_with("Refunds are due within 14 days of termination.")
    answer = "Refunds are due within 14 days of termination, and that is the rule for every case. [C1]"
    assert A.verify(answer, ev)["uncited_sentences"] == [] and A.verify(answer, ev)["ok"]


def test_a_paragraph_with_markers_only_at_the_end_still_flags_the_sentences_before_them():
    ev, _ = evidence_with("Body text one. Body text two.")
    v = A.verify("The first claim is stated here in full. The second claim is also stated in full. [C1]", ev)
    assert v["uncited_sentences"] == ["The first claim is stated here in full."] and not v["ok"]


def test_markers_that_follow_a_full_stop_close_the_sentence_before_them_not_open_the_next_one():
    claims = A.split_claims("Hashing uses SHA-256 [C1]. The ledger is public. [C2][F1] Miners validate blocks. [C3]")
    assert [(c["text"], c["markers"]) for c in claims] == [
        ("Hashing uses SHA-256 [C1].", ["C1"]),
        ("The ledger is public. [C2][F1]", ["C2", "F1"]),
        ("Miners validate blocks. [C3]", ["C3"]),
    ]


def test_the_sha256_case_from_a_real_answer_is_checked_against_its_own_markers():
    ev, _ = evidence_with("Hashing uses SHA-256 to secure block data.", "Unrelated passage about privacy.")
    answer = "Privacy risks arise from the public ledger [C2]. Hashing uses SHA-256 here. [C1] Another claim follows [C2]."
    v = A.verify(answer, ev)
    assert v["unsupported_numbers"] == [] and v["uncited_sentences"] == []


@pytest.mark.parametrize("reply", [
    {"claims": 5}, {"claims": "x"}, {"claims": {"id": 0}}, {"claims": [None, 3, {"id": [0], "supported": False}, {"id": "0"}]}, [], None,
])
def test_the_model_check_survives_replies_of_the_wrong_shape(monkeypatch, reply):
    ev, _ = evidence_with("Body 14 days.")
    monkeypatch.setattr(A, "cached_chat_json", lambda *a: (reply, None, False))
    got = A.llm_check(None, get_settings(), [{"text": "A claim [C1].", "markers": ["C1"]}], ev)
    assert got["unsupported"] == []


def test_numbers_in_a_header_or_a_label_do_not_support_a_claim():
    c = cand("The notice period is stated here without any figure.", page=87)            # header says "page 87"
    ev = A.build_evidence([c], [], [], {str(c.source_id): "report_2024.pdf"})
    assert A.verify("The notice period is 87 days [C1].", ev)["unsupported_numbers"][0]["number"] == "87"       # the page
    assert A.verify("The notice period is 2024 days [C1].", ev)["unsupported_numbers"][0]["number"] == "2024"   # the file name
    ev2 = A.build_evidence([c], [atom("Notice applies.", c)], [], TITLES)
    assert A.verify("Notice applies for 12 days [F1].", ev2)["unsupported_numbers"][0]["number"] == "12"        # "stated in C12"-style labels


def test_numbers_in_the_evidence_body_still_support_a_claim():
    c = cand("The notice period is 87 days.")
    ev = A.build_evidence([c], [], [], TITLES)
    assert A.verify("The notice period is 87 days [C1].", ev)["ok"]
