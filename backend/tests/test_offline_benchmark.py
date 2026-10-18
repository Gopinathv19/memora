"""Scoring and comparing retrieval systems offline (pure)."""

import pytest

from evals.offline_benchmark import bootstrap_ci, paired, score, summarize


def row(qid, retrieved, relevant, qtype="factoid", unanswerable=False, abstained=False):
    return {"qid": qid, "query_type": qtype, "is_unanswerable": unanswerable, "relevant_ids": relevant,
            "retrieved_ids": retrieved, "abstained": abstained}


def test_score_recall_hit_and_reciprocal_rank():
    got = score(["x", "b", "a", "y"], ["a", "b", "z"], k=3)
    assert got == {"recall": pytest.approx(2 / 3), "hit": 1.0, "rr": 0.5}
    assert score(["x", "y"], ["a"], k=2) == {"recall": 0.0, "hit": 0.0, "rr": 0.0}
    assert score(["x", "a"], ["a"], k=1) == {"recall": 0.0, "hit": 0.0, "rr": 0.0}      # beyond k does not count
    assert score(["a"], [], k=3) is None


def test_a_repeated_retrieved_id_is_not_counted_twice():
    assert score(["a", "a", "a"], ["a", "b"], k=3)["recall"] == 0.5


def test_summarize_reports_overall_and_per_type_means():
    rows = {"vector": [row(1, ["a"], ["a"]), row(2, ["x"], ["b"], qtype="multi_hop"), row(3, ["q"], [], "unanswerable", True, abstained=True)]}
    out = summarize(rows, k=3)["systems"]["vector"]
    assert out["overall"]["answerable"] == 2 and out["overall"]["unanswerable"] == 1
    assert out["overall"]["recall"] == 0.5 and out["overall"]["abstain_on_unanswerable"] == 1.0
    assert out["by_type"]["factoid"]["recall"] == 1.0 and out["by_type"]["multi_hop"]["recall"] == 0.0


def test_abstaining_on_an_answerable_question_is_counted_separately():
    rows = {"s": [row(1, [], ["a"], abstained=True), row(2, ["a"], ["a"])]}
    assert summarize(rows)["systems"]["s"]["overall"]["false_abstain_on_answerable"] == 0.5


def test_paired_comparison_counts_wins_ties_and_losses_and_gives_an_interval():
    base = [row(1, ["x"], ["a"]), row(2, ["a"], ["a"]), row(3, ["x"], ["a"]), row(4, ["a"], ["a"])]
    better = [row(1, ["a"], ["a"]), row(2, ["a"], ["a"]), row(3, ["x"], ["a"]), row(4, ["x"], ["a"])]
    got = paired(better, base, k=3)
    assert (got["wins"], got["ties"], got["losses"], got["n"]) == (1, 2, 1, 4) and got["mean_recall_diff"] == 0.0
    assert got["ci95"] is not None and got["ci95"][0] <= 0.0 <= got["ci95"][1]


def test_paired_skips_unanswerable_and_unmatched_questions():
    base = [row(1, ["a"], ["a"]), row(2, [], [], "unanswerable", True)]
    other = [row(1, ["a"], ["a"]), row(2, [], [], "unanswerable", True), row(9, ["a"], ["a"])]
    assert paired(other, base, k=3)["n"] == 1
    assert paired([row(7, [], [], "unanswerable", True)], base, k=3) is None


def test_summarize_adds_the_baseline_comparison_overall_and_per_type():
    per = {"vector": [row(1, ["x"], ["a"]), row(2, ["x"], ["b"], "multi_hop")],
           "unified": [row(1, ["a"], ["a"]), row(2, ["b"], ["b"], "multi_hop")]}
    out = summarize(per, k=3)
    assert "vs_baseline" not in out["systems"]["vector"]
    u = out["systems"]["unified"]
    assert u["vs_baseline"]["wins"] == 2 and u["vs_baseline"]["mean_recall_diff"] == 1.0
    assert set(u["vs_baseline_by_type"]) == {"factoid", "multi_hop"}


def test_bootstrap_needs_two_values_and_is_deterministic():
    assert bootstrap_ci([0.3]) is None and bootstrap_ci([]) is None
    assert bootstrap_ci([0.0, 1.0, 0.5, 0.2]) == bootstrap_ci([0.0, 1.0, 0.5, 0.2])


def answer_row(unanswerable, refused, ok=True, cited=True, uncited=False, bad_number=False, ms=1000, cost=0.01):
    verification = {} if refused else {"ok": ok, "uncited_sentences": ["x"] if uncited else [], "unsupported_numbers": [1] if bad_number else []}
    return {"is_unanswerable": unanswerable, "refused": refused, "verification": verification, "has_citations": cited,
            "total_ms": ms, "cost_usd": cost}


def test_summarize_answers_separates_refusals_from_the_quality_of_answers_given():
    from evals.offline_benchmark import summarize_answers

    rows = [answer_row(True, True), answer_row(True, False, ok=False, uncited=True), answer_row(False, False),
            answer_row(False, False, ok=False, bad_number=True, cited=False), answer_row(False, True)]
    out = summarize_answers(rows)
    assert (out["unanswerable"], out["answerable"], out["answered"]) == (2, 3, 3)
    assert out["refused_on_unanswerable"] == 0.5 and out["refused_on_answerable"] == round(1 / 3, 4)
    assert out["verified_ok_rate"] == round(1 / 3, 4) and out["cited_rate"] == round(2 / 3, 4)
    assert out["uncited_rate"] == round(1 / 3, 4) and out["unsupported_number_rate"] == round(1 / 3, 4)
    assert out["mean_total_ms"] == 1000 and out["mean_cost_usd"] == 0.01


def test_summarize_answers_with_nothing_answered_has_no_quality_rates():
    from evals.offline_benchmark import summarize_answers

    out = summarize_answers([answer_row(True, True)])
    assert out["answered"] == 0 and out["verified_ok_rate"] is None and out["cited_rate"] is None
    assert summarize_answers([])["mean_cost_usd"] is None
