"""Stopword removal for the lexical (tsvector) branch of hybrid retrieval.

The tsvector is generated with PostgreSQL's 'simple' config -- no stemming,
no stopword list -- because enterprise documents contain product names, error
codes and identifiers that stemming would damage. The trade-off is that
'simple' also indexes every common word ("what", "the", "is", ...) like any
other token, which adds noise to ts_rank_cd and dilutes exact-token matching.

This module closes that gap in application code: stopwords are stripped from
both what is indexed (`build_search_text`) and what is queried (the query
passed to websearch_to_tsquery), so a stopword never reaches the tsvector
comparison. The list is deliberately small and conservative: only words that
carry no retrieval signal on their own. Domain terms are never removed.

Kept lowercase; matching is case-insensitive on both sides.
"""

# A conservative English stopword list. No domain-adjacent words (e.g. "not",
# "no" are kept -- negation can matter for retrieval).
STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "being",
        "by", "can", "could", "did", "do", "does", "for", "from", "had",
        "has", "have", "having", "he", "her", "hers", "him", "his", "how",
        "i", "if", "in", "into", "is", "it", "its", "me", "my", "of",
        "on", "or", "our", "ours", "she", "so", "than", "that", "the",
        "their", "theirs", "them", "they", "this", "these", "those",
        "to", "us", "was", "we", "were", "what", "when", "where", "which",
        "who", "whom", "why", "will", "with", "would", "you", "your",
        "yours",
    }
)

def strip_stopwords(text: str) -> str:
    """Remove stopwords from free text, preserving everything else verbatim.

    Splits on runs of whitespace (str.split() with no argument), so
    punctuation, hyphens and case inside a token are untouched:
    "INV-2024-0091" and "E-421" stay single tokens. If every token is a
    stopword, returns "" (the caller decides what an empty lexical query
    means).
    """
    if not text:
        return ""
    kept = [
        token
        for token in text.split()
        if token.lower() not in STOPWORDS
    ]
    return " ".join(kept)
