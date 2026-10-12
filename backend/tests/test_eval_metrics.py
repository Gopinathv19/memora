"""Tests for evals.metrics: every metric's edge cases."""

from evals.metrics import (
    citation_precision,
    context_precision,
    context_recall,
    false_answer_rate,
    hit_rate,
    ndcg_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank,
    redundancy_rate,
    refusal_rate,
    summarize_retrieval,
    throughput,
    is_refusal,
)


# --- retrieval ----------------------------------------------------------------------


def test_recall_at_k_basic():
    assert recall_at_k(["a", "b", "c"], {"a"}, 5) == 1.0
    assert recall_at_k(["a", "b", "c"], {"a", "z"}, 5) == 0.5


def test_recall_at_k_respects_k():
    # "a" is rank 6, outside top-5
    assert recall_at_k(["x", "x", "x", "x", "x", "a"], {"a"}, 5) == 0.0
    assert recall_at_k(["x", "x", "x", "x", "x", "a"], {"a"}, 10) == 1.0


def test_recall_at_k_no_ground_truth_is_none():
    assert recall_at_k(["a"], set(), 5) is None
    assert recall_at_k([], {"a"}, 0) is None


def test_recall_empty_retrieved():
    assert recall_at_k([], {"a"}, 5) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(["a", "b"], {"a"}) == 1.0
    assert reciprocal_rank(["b", "a"], {"a"}) == 0.5
    assert reciprocal_rank(["b", "c", "a"], {"a"}) == pytest_approx(1 / 3)
    assert reciprocal_rank(["b", "c"], {"a"}) == 0.0
    assert reciprocal_rank(["a"], set()) is None


def pytest_approx(v):
    return v


def test_ndcg_perfect():
    assert ndcg_at_k(["a", "b"], {"a", "b"}, 10) == 1.0


def test_ndcg_rank_sensitive():
    # relevant at rank 1 beats relevant at rank 2
    top = ndcg_at_k(["a", "x"], {"a"}, 10)
    bottom = ndcg_at_k(["x", "a"], {"a"}, 10)
    assert top == 1.0
    assert bottom < 1.0
    # DCG(rank2) = 1/log2(3); IDCG = 1/log2(2) = 1
    assert abs(bottom - (1.0 / 1.584962500721156)) < 1e-9


def test_ndcg_no_ground_truth():
    assert ndcg_at_k(["a"], set(), 10) is None


def test_hit_rate():
    assert hit_rate(["a"], {"a"}) == 1.0
    assert hit_rate(["x"], {"a"}) == 0.0
    # defined (as 0) even with no ground truth
    assert hit_rate(["x"], set()) == 0.0


# --- context ------------------------------------------------------------------------


def test_context_precision():
    assert context_precision(["a", "b", "c"], {"a"}) == pytest_approx(1 / 3)
    assert context_precision(["a", "a2"], {"a", "a2"}) == 1.0
    assert context_precision([], {"a"}) is None


def test_context_recall():
    assert context_recall(["a"], {"a", "b"}) == 0.5
    assert context_recall(["a", "b"], {"a", "b"}) == 1.0
    assert context_recall(["a"], set()) is None


def test_redundancy_rate():
    texts = ["alpha beta gamma", "alpha beta gamma", "something else entirely"]
    # second chunk duplicates the first
    assert redundancy_rate(texts) == pytest_approx(1 / 3)
    assert redundancy_rate(["a b", "c d"]) == 0.0
    assert redundancy_rate(["only one"]) is None


# --- grounding ----------------------------------------------------------------------


def test_citation_precision_against_retrieved():
    # both citations point at retrieved chunks -> 1.0
    assert citation_precision(["c1", "c2"], ["c1", "c2", "c3"]) == 1.0
    # one of two citations points outside the retrieved set
    assert citation_precision(["c1", "c9"], ["c1", "c2"]) == pytest_approx(1 / 2)
    assert citation_precision(["c9"], ["c1"]) == 0.0
    # missing citations are None, not 0
    assert citation_precision([], ["c1"]) is None


def test_citation_precision_against_expected():
    # expected mode: |cited ∩ expected| / |expected|
    # cited {c1, c9} ∩ expected {c1} = {c1}; |expected| = 1 -> 1.0
    assert citation_precision(["c1", "c9"], ["c1"], expected=["c1"]) == 1.0
    # expected {c1, c2}, cited only {c1} -> 1/2
    assert citation_precision(["c1"], ["c1"], expected=["c1", "c2"]) == pytest_approx(1 / 2)
    assert citation_precision(["c1"], ["c1"], expected=[]) is None


# --- abstention ---------------------------------------------------------------------


def test_is_refusal_patterns():
    assert is_refusal("I could not find any information about that.")[0] is True
    assert is_refusal("The available knowledge is insufficient.")[0] is True
    assert is_refusal("The answer is 42.")[0] is False
    assert is_refusal("")[0] is False


def test_refusal_rates():
    answers = ["I could not find any information.", "The answer is 42."]
    assert refusal_rate(answers) == 0.5
    assert false_answer_rate(answers) == 0.5
    assert refusal_rate([]) is None


# --- operational --------------------------------------------------------------------


def test_percentile():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    # nearest-rank with banker's rounding: round(4.5) -> 4 -> value 5.0
    assert percentile(vals, 50) == 5.0
    assert percentile(vals, 95) == 10.0
    assert percentile([], 50) is None
    assert percentile([42.0], 50) == 42.0


def test_throughput():
    assert throughput(10, 5.0) == 2.0
    assert throughput(10, 0) is None


# --- aggregation --------------------------------------------------------------------


def test_summarize_retrieval_excludes_unanswerable():
    per_example = [
        {"retrieved_ids": ["a"], "relevant_ids": ["a"]},
        {"retrieved_ids": ["x"], "relevant_ids": []},  # unanswerable
    ]
    s = summarize_retrieval(per_example)
    assert s["n"] == 2
    assert s["answerable"] == 1
    assert s["unanswerable"] == 1
    assert s["recall_at_5"] == 1.0
    # hit rate counts both, unanswerable expected 0
    assert s["hit_rate"] == 0.5


def test_summarize_retrieval_multiple_relevant():
    per_example = [
        {"retrieved_ids": ["a", "b", "x"], "relevant_ids": ["a", "b", "z"]},
    ]
    s = summarize_retrieval(per_example)
    # values are rounded to 4 decimals by the aggregator
    assert abs(s["recall_at_5"] - 2 / 3) < 1e-4
    assert abs(s["context_precision"] - 2 / 3) < 1e-4
    assert abs(s["context_recall"] - 2 / 3) < 1e-4
