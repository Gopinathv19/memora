"""Unit tests for the pure parts of hybrid retrieval: RRF fusion and the
lexical search-text representation. The SQL branches (HNSW, tsvector) need a
live PostgreSQL with pgvector and are exercised by the eval pipeline instead.
"""

from app.chunking.representation import build_embedding_text, build_search_text
from app.core.stopwords import STOPWORDS, strip_stopwords
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.lexical import build_lexical_tsquery


def test_rrf_fuses_two_lists_by_rank():
    dense = ["a", "b", "c"]
    lexical = ["b", "d"]

    fused = reciprocal_rank_fusion([dense, lexical], k=60)

    ids = [cid for cid, _ in fused]
    # 'b' appears in both lists -> highest fused score, first.
    assert ids[0] == "b"
    # Every id from both lists is present, exactly once.
    assert sorted(ids) == ["a", "b", "c", "d"]
    # Scores are the sum of 1/(k+rank) contributions: 'b' is rank 2 in the
    # dense list and rank 1 in the lexical list.
    scores = dict(fused)
    assert abs(scores["b"] - (1 / 62 + 1 / 61)) < 1e-12
    assert abs(scores["a"] - 1 / 61) < 1e-12
    assert abs(scores["c"] - 1 / 63) < 1e-12
    assert abs(scores["d"] - 1 / 62) < 1e-12


def test_rrf_ties_are_deterministic():
    one = ["x"]
    other = ["y"]

    fused = reciprocal_rank_fusion([one, other], k=60)
    assert [cid for cid, _ in fused] == ["x", "y"]

    # Same input, same order (id tiebreak).
    assert [cid for cid, _ in reciprocal_rank_fusion([one, other], k=60)] == [
        cid for cid, _ in fused
    ]


def test_rrf_empty_lists():
    assert reciprocal_rank_fusion([[], []], k=60) == []
    assert reciprocal_rank_fusion([], k=60) == []


def test_rrf_duplicate_ids_within_one_list_accumulate():
    # A repeated id in a single list still only sums its per-occurrence
    # contributions; RRF does not deduplicate within a list.
    fused = reciprocal_rank_fusion([["a", "a"]], k=60)
    assert dict(fused)["a"] == 1 / 61 + 1 / 62


def test_build_search_text_includes_embedding_text_and_content():
    content = "Invoice INV-2024-0091 total 1200 USD."
    embedding_text = build_embedding_text(["Finance", "Invoices"], content)

    search_text = build_search_text(["Finance", "Invoices"], content)

    # Both the section-prefixed representation and the raw content are
    # indexed, so exact tokens match even when the prefix reorders them.
    assert "INV-2024-0091" in search_text
    assert "Invoice" in search_text
    assert "Finance" in search_text
    assert "1200" in search_text
    # Stopwords never reach the tsvector.
    assert strip_stopwords(embedding_text) in search_text


def test_build_search_text_strips_stopwords():
    content = "What is the total amount on the invoice?"
    search_text = build_search_text([], content)
    for word in ("What", "is", "the", "on"):
        assert word not in search_text.split()
    # Content-bearing tokens survive.
    assert "total" in search_text
    assert "invoice" in search_text


def test_build_search_text_without_section_path():
    content = "Error code E-421 means thermal shutdown."
    # No section path: the embedding representation is the content itself,
    # so search_text is the content twice, both stopword-free.
    expected = strip_stopwords(content)
    assert build_search_text([], content) == expected + " " + expected


def test_build_search_text_empty_content():
    # Only the section prefix remains; no crash, no stray separators. "A" is
    # itself a stopword, so it is stripped like any other token.
    assert build_search_text(["A"], "") == "Section:"


def test_strip_stopwords_preserves_technical_tokens():
    text = "Error code E-421 on invoice INV-2024-0091"
    out = strip_stopwords(text)
    # Hyphenated identifiers stay intact; only the stopword "on" is gone.
    assert "E-421" in out
    assert "INV-2024-0091" in out
    assert " on " not in f" {out} "


def test_strip_stopwords_all_stopwords():
    # A query that is entirely stopwords: empty string, caller decides.
    assert strip_stopwords("what is the") == ""


def test_strip_stopwords_case_insensitive():
    assert strip_stopwords("The THE the") == ""
    assert "Invoice" in strip_stopwords("The Invoice")


def test_build_lexical_tsquery_or_semantics():
    # Stopwords stripped; remaining terms OR-ed, each single-quoted.
    # Punctuation stays attached to its token (split is whitespace-only).
    q = build_lexical_tsquery("What is the total amount?")
    assert q == "'total' | 'amount?'"


def test_build_lexical_tsquery_escapes_quotes_and_operators():
    # Raw user input can never inject tsquery syntax: quotes are escaped,
    # operator characters stay inside quoted terms (inert).
    q = build_lexical_tsquery("invoice & (total) | 'due'")
    assert "&" not in q.replace("'&'", "")
    assert "'" in q  # terms are quoted
    # No unquoted operator survives outside quotes.
    import re
    assert not re.search(r"(?<!')(?:^| )&(?= |$)", q)


def test_build_lexical_tsquery_all_stopwords():
    assert build_lexical_tsquery("what is the") == ""
