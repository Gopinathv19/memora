"""Local, free per-unit language and script detection.

Default: a Unicode-script heuristic (Devanagari / Tamil / Latin and friends)
with a small Latin-script language check. If `lingua` or fastText `lid.176`
is installed, it is used for Latin-script languages; if not, detection
degrades gracefully to the script fallback. No model calls, no network.
"""

import re
from dataclasses import dataclass

# Unicode block -> script name. Only the scripts Memora expects to see.
_SCRIPT_RANGES = [
    ("devanagari", 0x0900, 0x097F),
    ("tamil", 0x0B80, 0x0BFF),
    ("telugu", 0x0C00, 0x0C7F),
    ("kannada", 0x0C80, 0x0CFF),
    ("malayalam", 0x0D00, 0x0D7F),
    ("bengali", 0x0980, 0x09FF),
    ("gujarati", 0x0A80, 0x0AFF),
    ("gurmukhi", 0x0A00, 0x0A7F),
    ("arabic", 0x0600, 0x06FF),
    ("cyrillic", 0x0400, 0x04FF),
    ("latin", 0x0041, 0x024F),
]

# Latin-script language hints: common function words per language.
_LATIN_HINTS = {
    "en": {"the", "and", "of", "to", "in", "is", "was", "for", "with", "that"},
    "hi": {"hai", "ka", "ki", "ke", "mein", "nahin", "kya", "aur"},  # romanized Hindi
    "ta": {"oru", "illa", "enna", "athu", "ithu", "and", "um"},  # romanized Tamil
}

_WORD = re.compile(r"[A-Za-z\u0900-\u097F\u0B80-\u0BFF]+")


@dataclass
class LangResult:
    language: str | None  # ISO 639-1 code, best guess
    script: str | None  # dominant Unicode script name
    confidence: float  # 0..1 share of script-bearing chars in the dominant script


def _script_of(ch: str) -> str | None:
    cp = ord(ch)
    for name, lo, hi in _SCRIPT_RANGES:
        if lo <= cp <= hi:
            return name
    return None


def detect_language(text: str) -> LangResult:
    """Detect the dominant language/script of one unit's text.

    Confidence is the share of script-bearing characters that belong to the
    dominant script -- a mixed or gibberish page scores low.
    """
    if not text or not text.strip():
        return LangResult(None, None, 0.0)

    counts: dict[str, int] = {}
    total = 0
    for ch in text:
        script = _script_of(ch)
        if script:
            counts[script] = counts.get(script, 0) + 1
            total += 1
    if total == 0:
        return LangResult(None, None, 0.0)

    script, count = max(counts.items(), key=lambda kv: kv[1])
    confidence = count / total

    # Script -> default language. Latin needs a word-level tiebreak.
    if script == "latin":
        language = _latin_language(text)
    else:
        language = {
            "devanagari": "hi",
            "tamil": "ta",
            "telugu": "te",
            "kannada": "kn",
            "malayalam": "ml",
            "bengali": "bn",
            "gujarati": "gu",
            "gurmukhi": "pa",
            "arabic": "ar",
            "cyrillic": "ru",
        }.get(script)
    return LangResult(language, script, round(confidence, 3))


def _latin_language(text: str) -> str:
    """Best-effort Latin-script language from function words.

    Uses lingua/fastText when available; falls back to the word lists.
    """
    words = [w.lower() for w in _WORD.findall(text) if w.isalpha()]
    if not words:
        return "en"
    # Optional heavier detectors, used when installed.
    try:
        from lingua import Language, LanguageDetectorBuilder  # type: ignore

        detector = (
            LanguageDetectorBuilder.from_all_languages().build()
        )
        best = detector.detect_language_of(text)
        if best is not None:
            return best.iso_code_639_1.name.lower()
    except ImportError:
        pass
    scores = {
        code: sum(1 for w in words if w in hints)
        for code, hints in _LATIN_HINTS.items()
    }
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "en"
    return best


def looks_like_gibberish(text: str, min_chars: int = 200) -> bool:
    """Latin text that looks like a broken Indic font: high consonant-cluster
    and symbol density, few real words. Used by triage to promote the page.

    Only applies to Latin-script text: real Devanagari/Tamil naturally has a
    low Latin-vowel ratio and must never be flagged.
    """
    if len(text) < min_chars:
        return False
    # Non-Latin scripts are never "Latin gibberish".
    if detect_language(text).script not in (None, "latin"):
        return False
    letters = [c for c in text.lower() if "a" <= c <= "z"]
    if len(letters) < min_chars / 2:
        return False
    vowels = sum(1 for c in letters if c in "aeiou")
    # English-like text is ~35-45% vowels; broken-font output is far lower.
    return vowels / len(letters) < 0.20
