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


def count_tokens(text: str) -> int:
    """Approximate token count for the given text.

    Used on the final embedding representation. The ~4 chars/token heuristic
    is deliberately conservative (overestimates slightly) so chunks stay
    safely within model context limits.
    """
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN))
