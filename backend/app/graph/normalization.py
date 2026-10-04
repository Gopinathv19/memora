"""Entity names: a tidy display name, and the canonical key used for identity.

The display name keeps the document's own spelling ("IBM Corp."); the key is
what identity is decided on ("ibm"). Normalization is deterministic and
conservative -- it only removes differences that never change *which* thing is
meant: case, spacing, punctuation, legal-form suffixes on organizations and
honorifics on people.
"""

import re
import unicodedata

ORGANIZATION_TYPES = frozenset({"COMPANY", "ORGANIZATION"})

# Legal forms, stripped from the end of organization names ("Acme Pvt Ltd").
_LEGAL_SUFFIXES = frozenset(
    {
        "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
        "limited", "llc", "llp", "lp", "plc", "pvt", "private", "pte", "gmbh",
        "ag", "sa", "sas", "bv", "nv", "oy", "ab", "kk", "srl", "spa",
    }
)
_HONORIFICS = frozenset(
    {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "shri", "sri", "smt",
     "kumari", "thiru", "tmt", "selvi"}
)
_ACRONYM_SKIP = frozenset({"of", "and", "the", "for", "&"})

_SPACE = re.compile(r"\s+")
_QUOTES = "\"'`“”‘’«»"


def display_name(name: str) -> str:
    """Trim, collapse whitespace and drop wrapping quotes; keep the spelling."""
    text = _SPACE.sub(" ", unicodedata.normalize("NFKC", name or "")).strip()
    return text.strip(_QUOTES + " ").strip()


def _words(name: str) -> list[str]:
    text = unicodedata.normalize("NFKC", name or "").casefold()
    text = text.replace("&", " and ")
    # Punctuation inside a word ("i.b.m", "o'neil") joins rather than splits.
    text = re.sub(r"(?<=\w)[.'’](?=\w)", "", text)
    return [w for w in re.split(r"[^\w]+", text) if w and w != "_"]


def canonical_key(name: str, entity_type: str | None = None) -> str:
    """The identity key: 'Microsoft Corp.' -> 'microsoft', 'Dr. A. Kumar' -> 'a_kumar'."""
    words = _words(name)
    if entity_type in ORGANIZATION_TYPES:
        stripped = list(words)
        if stripped and stripped[0] == "the":
            stripped = stripped[1:]
        while len(stripped) > 1 and stripped[-1] in _LEGAL_SUFFIXES:
            stripped.pop()
        words = stripped or words
    elif entity_type == "PERSON":
        stripped = list(words)
        while len(stripped) > 1 and stripped[0] in _HONORIFICS:
            stripped.pop(0)
        words = stripped or words
    return "_".join(words)


def acronym(key: str) -> str | None:
    """'international_business_machines' -> 'ibm'; None for one-word names."""
    words = [w for w in key.split("_") if w not in _ACRONYM_SKIP]
    if len(words) < 2:
        return None
    return "".join(w[0] for w in words)
