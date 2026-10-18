"""Stage 1 baseline: abstention scoring and the lexical-solvability report (pure, no DB)."""

from app.graphrag.benchmark import aggregate, is_abstention, is_unanswerable
from evals.solvability import BM25, solvability


def test_is_abstention_matches_the_prompts_own_wording():
    assert is_abstention("The available knowledge does not contain enough information to answer that question.")
    assert is_abstention("The available knowledge is insufficient to say.")
    assert is_abstention("I could not find this in your documents.")
    assert not is_abstention("Acme must refund prepaid fees within 14 days.")
    assert not is_abstention("")
    assert not is_abstention(None)


def test_is_unanswerable_accepts_flag_or_type():
    assert is_unanswerable({"is_unanswerable": True})
    assert is_unanswerable({"query_type": "unanswerable"})
    assert not is_unanswerable({"query_type": "factoid"})


def _row(abstained, status="ok"):
    return {
        "status": status, "retrieved_ids": [], "total_ms": 10, "cost_usd": 0.0,
        "prompt_tokens": 1, "completion_tokens": 1, "recall_at_5": None,
        "correctness": None, "abstained": abstained,
    }


def test_aggregate_reports_abstention_split():
    per_question = [
        {"query": "a", "query_type": "unanswerable", "relevant_ids": [], "is_unanswerable": True,
         "pipelines": {"vector": _row(True)}},
        {"query": "b", "query_type": "unanswerable", "relevant_ids": [], "is_unanswerable": True,
         "pipelines": {"vector": _row(False)}},
        {"query": "c", "query_type": "factoid", "relevant_ids": ["x"], "is_unanswerable": False,
         "pipelines": {"vector": _row(False)}},
        {"query": "d", "query_type": "factoid", "relevant_ids": ["y"], "is_unanswerable": False,
         "pipelines": {"vector": _row(True)}},
    ]
    aggregates, _ = aggregate(per_question, ["vector"])
    agg = aggregates["vector"]
    assert agg["abstain_on_unanswerable"] == 0.5
    assert agg["unanswerable_n"] == 2
    assert agg["false_abstain_on_answerable"] == 0.5


def test_aggregate_without_unanswerable_questions_has_no_rate():
    per_question = [
        {"query": "c", "query_type": "factoid", "relevant_ids": ["x"], "pipelines": {"vector": _row(False)}},
    ]
    agg = aggregate(per_question, ["vector"])[0]["vector"]
    assert agg["abstain_on_unanswerable"] is None
    assert agg["unanswerable_n"] == 0


CHUNKS = {
    "c1": "Acme Corp shall refund prepaid fees within 14 days of termination.",
    "c2": "Beta Ltd acquired Gamma Inc in 2025 after a long negotiation.",
    "c3": "Delta Corp supplies steel components to Gamma Inc.",
    "c4": "The weather report for the region is mild and dry this week.",
}


def test_bm25_ranks_the_matching_chunk_first():
    assert BM25(CHUNKS).rank("refund of prepaid fees after termination", 2)[0] == "c1"


def test_solvability_splits_any_and_all_for_two_passage_questions():
    questions = [
        {"query": "refund prepaid fees termination", "supporting_chunk_ids": ["c1"], "query_type": "factoid"},
        # needs c2 and c3; lexical search with k=1 finds only one of them
        {"query": "Who supplies the company Beta acquired Gamma", "supporting_chunk_ids": ["c2", "c3"],
         "query_type": "multi_hop"},
        {"query": "anything", "supporting_chunk_ids": [], "query_type": "unanswerable", "is_unanswerable": True},
        {"query": "gone", "supporting_chunk_ids": ["missing"], "query_type": "factoid"},
    ]
    report = solvability(questions, CHUNKS, k=1)
    assert report["scored"] == 2
    assert report["stale_support"] == 1
    assert report["by_type"]["factoid"] == {"n": 1, "any": 1.0, "all": 1.0}
    hop = report["by_type"]["multi_hop"]
    assert hop["any"] == 1.0
    assert hop["all"] == 0.0


# --- corpus-wide grouping and the new schema type ---------------------------------------

import random  # noqa: E402

from evals.graph_questions import answerable_from, theme_groups  # noqa: E402
from evals.schemas import RecordError, validate_record  # noqa: E402
import pytest  # noqa: E402


def _chunk(cid, src, text):
    return {"id": cid, "source_id": src, "content": text}


THEME = [
    _chunk("a1", "f1", "Supplier contract risk: late delivery penalties and termination clauses apply."),
    _chunk("a2", "f2", "Supplier contract risk: indemnity limits and liability caps are negotiated yearly."),
    _chunk("a3", "f3", "Supplier contract risk: currency exposure and payment delays affect margins."),
    _chunk("a4", "f1", "Supplier contract risk: force majeure wording excludes strikes and port closures."),
    _chunk("b1", "f4", "Cafeteria menu rotates weekly with vegetarian options and fresh fruit."),
    _chunk("b2", "f4", "Parking permits are issued by the facilities team every quarter."),
]


def test_theme_groups_span_files_and_do_not_reuse_chunks():
    groups = theme_groups(THEME, 3, random.Random(1), size=(3, 4))
    assert groups, "the supplier-risk chunks should form a group"
    for g in groups:
        assert len({c["source_id"] for c in g}) >= 2
    used = [c["id"] for g in groups for c in g]
    assert len(used) == len(set(used))
    assert all(c["id"].startswith("a") for g in groups for c in g)  # off-theme chunks stay out
    assert all(len(g) >= 3 for g in groups)


def test_theme_groups_empty_corpus():
    assert theme_groups([], 3, random.Random(1)) == []


def test_answerable_from_returns_best_lexical_matches():
    index = BM25({c["id"]: c["content"] for c in THEME})
    hits = answerable_from(index, {c["id"]: c["content"] for c in THEME}, "currency exposure payment delays", k=1)
    assert hits[0][0] == "a3"


def test_corpus_wide_is_a_valid_query_type_and_unanswerable_rules_hold():
    ok = validate_record({"query": "Main supplier risks?", "tenant_id": "t", "expected_answer": "Late delivery, liability caps.",
                          "supporting_chunk_ids": ["a1", "a2", "a3"], "query_type": "corpus_wide", "difficulty": "hard"})
    assert ok.query_type == "corpus_wide"
    with pytest.raises(RecordError):
        validate_record({"query": "x?", "tenant_id": "t", "is_unanswerable": True, "expected_answer": "oops",
                         "query_type": "unanswerable", "difficulty": "medium"})


# --- judge hygiene ----------------------------------------------------------------------

from evals.agreement import agreement, cohens_kappa, compare as compare_graders  # noqa: E402


def test_kappa_perfect_chance_and_undefined():
    assert cohens_kappa([1, 0, 1, 0.5], [1, 0, 1, 0.5]) == 1.0
    # both say "1" every time: expected agreement is 1, kappa undefined
    assert cohens_kappa([1, 1, 1], [1, 1, 1]) is None
    assert cohens_kappa([], []) is None
    # 50% raw agreement with balanced marginals is chance level
    assert cohens_kappa([1, 1, 0, 0], [1, 0, 1, 0]) == 0.0
    assert agreement([1, 1, 0, 0], [1, 0, 1, 0]) == 0.5


def test_compare_skips_unlabelled_pairs():
    out = compare_graders([1.0, None, 0.0, 0.5], [1.0, 1.0, None, 0.5])
    assert out["n"] == 2 and out["skipped"] == 2
    assert out["agreement"] == 1.0


def test_judge_model_defaults_to_answer_model_and_flags_it(monkeypatch):
    from app.core.config import get_settings
    from app.graphrag import benchmark

    s = get_settings()
    monkeypatch.setattr(s, "graphrag_judge_model", "")
    assert benchmark.judge_info() == {"judge_model": s.llm_answer_model, "judge_same_as_answer": True}
    monkeypatch.setattr(s, "graphrag_judge_model", "nvidia/some-other-model")
    assert benchmark.judge_info() == {"judge_model": "nvidia/some-other-model", "judge_same_as_answer": False}


def test_judge_model_setting_must_be_nvidia_or_empty():
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(graphrag_judge_model="gpt-4o")
    assert Settings(graphrag_judge_model="").graphrag_judge_model == ""


# --- block size statistics --------------------------------------------------------------

from evals.block_stats import bucket_label, summarize_blocks  # noqa: E402


def _block(n_chars, chunks=(100,), table=False, path=("A",)):
    return {"content": "x" * n_chars, "chunks": list(chunks), "has_table": table, "section_path": list(path)}


def test_bucket_labels():
    assert bucket_label(50) == "0-100"
    assert bucket_label(750) == "750-1200"
    assert bucket_label(5000) == "3000+"


def test_summarize_blocks_counts_small_large_and_shares():
    blocks = [
        _block(200),                                   # 50 tokens: small
        _block(2000, chunks=(120, 130), table=True),   # 500 tokens
        _block(16000, chunks=(400,) * 10, path=()),    # 4000 tokens: large, no section path
        _block(4000),                                  # 1000 tokens
    ]
    r = summarize_blocks(blocks)
    assert r["blocks"] == 4
    assert r["below_small"] == 1 and r["above_large"] == 1
    assert r["tokens"]["max"] == 4000 and r["tokens"]["min"] == 50
    assert r["chunks_per_block"]["single_chunk_share"] == 0.5
    assert r["table_share"] == 0.25 and r["no_section_path_share"] == 0.25
    assert r["largest"][0]["tokens"] == 4000


def test_summarize_blocks_empty():
    r = summarize_blocks([])
    assert r["blocks"] == 0 and r["tokens"]["p50"] is None and r["table_share"] is None


# --- the unified pipeline in the benchmark ----------------------------------------------------------

import subprocess  # noqa: E402
import sys  # noqa: E402

from app.graphrag.benchmark import _answer_checks, pipeline_key  # noqa: E402


def test_answer_checks_are_booleans_for_graph_answers_and_none_for_the_other_pipelines():
    assert _answer_checks({"answer": "x"}) == {
        "verification_ok": None, "has_citations": None, "has_unsupported_number": None, "has_uncited_sentence": None}
    got = _answer_checks({"verification": {"ok": False, "unsupported_numbers": [{"number": "30"}], "uncited_sentences": []},
                          "citations": [{"marker": "C1"}]})
    assert got == {"verification_ok": False, "has_citations": True, "has_unsupported_number": True, "has_uncited_sentence": False}


def test_aggregate_reports_citation_and_verification_rates_only_over_checked_answers():
    def row(ok, cited, bad_number, uncited):
        r = _row(False)
        r.update(verification_ok=ok, has_citations=cited, has_unsupported_number=bad_number, has_uncited_sentence=uncited)
        return r

    per_question = [
        {"query": "a", "query_type": "factoid", "relevant_ids": [], "pipelines": {"unified:unified": row(True, True, False, False), "vector": _row(False)}},
        {"query": "b", "query_type": "factoid", "relevant_ids": [], "pipelines": {"unified:unified": row(False, True, True, False), "vector": _row(False)}},
        {"query": "c", "query_type": "factoid", "relevant_ids": [], "pipelines": {"unified:unified": row(False, False, False, True), "vector": _row(False)}},
    ]
    agg, _ = aggregate(per_question, ["vector", "unified:unified"])
    u = agg["unified:unified"]
    assert u["answers_checked"] == 3 and u["verified_ok_rate"] == round(1 / 3, 4) and u["cited_answer_rate"] == round(2 / 3, 4)
    assert u["unsupported_number_rate"] == round(1 / 3, 4) and u["uncited_answer_rate"] == round(1 / 3, 4)
    assert agg["vector"]["answers_checked"] == 0 and agg["vector"]["verified_ok_rate"] is None


def test_the_unified_pipeline_has_its_own_benchmark_key():
    assert pipeline_key("unified", "unified") == "unified:unified" and pipeline_key("vector", None) == "vector"


@pytest.mark.parametrize("module", ["answer", "unified", "benchmark", "compare", "retrieve", "pipelines", "ingest", "abstain"])
def test_every_graphrag_module_imports_first_in_a_fresh_interpreter(module):
    """A circular import hid behind import order once (benchmark -> compare -> unified -> answer -> benchmark)."""
    done = subprocess.run([sys.executable, "-c", f"import app.graphrag.{module}"], capture_output=True, text=True,
                          env={**__import__("os").environ, "API_SECRET": "x"}, timeout=120)
    assert done.returncode == 0, done.stderr[-400:]
