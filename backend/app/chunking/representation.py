"""Build the embedding representation and count tokens on it.

The embedding representation is the actual text that will be embedded: a
section-context prefix ("Section: A > B") followed by the chunk's content.
Token counting is done on this final representation, after the prefix is
added (strategy §14).

There is no local Nemotron tokenizer, so token counts are approximate:
~4 characters per token with a small safety margin. This is calibrated
later against provider-reported usage.
"""

# Approximate characters per token. English text averages ~4 chars/token for
# most tokenizers; the margin keeps us safely under model limits.
_CHARS_PER_TOKEN = 4.0


def build_embedding_text(section_path: list[str], content: str) -> str:
    """Section context prefix + content -- what gets embedded.

    Strategy §11, §15: semantic context (section path) improves understanding
    and is part of the embedding; operational metadata (IDs, tenant) is not.
    """
    if section_path:
        prefix = "Section: " + " > ".join(section_path)
        return f"{prefix}\n\n{content}"
    return content


def build_search_text(section_path: list[str], content: str) -> str:
    """The lexical-search representation: embedding_text + content.

    Hybrid retrieval (docs/embedding-retrieval.md) indexes this into a
    tsvector ('simple' config) so exact identifiers, error codes, product
    names and rare proper nouns match lexically as well as semantically.
    `content` itself is never changed: it stays the generation/citation text.

    Stopwords are stripped (app.core.stopwords): the 'simple' config has no
    stopword handling of its own, and common words would otherwise index
    like any other token and add noise to ts_rank_cd.
    """
    from app.core.stopwords import strip_stopwords

    return strip_stopwords(
        "\n".join(
            part
            for part in (build_embedding_text(section_path, content), content)
            if part
        )
    )


def count_tokens(text: str) -> int:
    """Approximate token count for the given text.

    Used on the final embedding representation. The ~4 chars/token heuristic
    is deliberately conservative (overestimates slightly) so chunks stay
    safely within model context limits.
    """
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN))
