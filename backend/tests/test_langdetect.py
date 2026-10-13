"""Phase 4 tests: language/script detection, triage promotion, result spans."""

from app.core.config import Settings
from app.processing.langdetect import detect_language, looks_like_gibberish


def test_devanagari_detected():
    r = detect_language("हिन्दी भाषा भारत की एक प्रमुख भाषा है। यह देवनागरी लिपि में लिखी जाती है।")
    assert r.script == "devanagari"
    assert r.language == "hi"
    assert r.confidence > 0.9


def test_tamil_detected():
    r = detect_language("தமிழ் மொழி தமிழ்நாட்டின் அதிகாரப்பூர்வ மொழியாகும்। இது ஒரு செம்மொழி ஆகும்।")
    assert r.script == "tamil"
    assert r.language == "ta"
    assert r.confidence > 0.9


def test_english_detected():
    r = detect_language("The invoice was issued to the buyer with the terms agreed in the contract.")
    assert r.script == "latin"
    assert r.language == "en"


def test_mixed_language_low_confidence():
    # Half Tamil, half Latin: confidence must drop below a pure page's.
    tamil = "தமிழ் மொழி தமிழ்நாட்டின் அதிகாரப்பூர்வ மொழியாகும் இது ஒரு செம்மொழி ஆகும் என்று"
    english = "The invoice was issued to the buyer with the terms agreed in the contract."
    mixed = detect_language(tamil + " " + english)
    pure = detect_language(tamil)
    assert mixed.confidence < pure.confidence


def test_empty_text():
    r = detect_language("")
    assert r.language is None and r.script is None and r.confidence == 0.0


def test_gibberish_flagged():
    # Broken-Indic-font output: dense consonant clusters, almost no vowels.
    gibberish = " ".join(["xkqzvwjhn" * 3 for _ in range(40)])
    assert looks_like_gibberish(gibberish)
    normal = "The invoice was issued to the buyer with the terms agreed in the contract. " * 5
    assert not looks_like_gibberish(normal)


def test_triage_promotion_config_defaults():
    s = Settings()
    assert s.triage_lang_promotion_enabled
    assert 0 < s.triage_lang_min_confidence < 1


def test_result_languages_spans():
    from app.agents.extraction_agent import NemotronExtractionAgent
    from app.processing.document import ProcessedDocument, ReadingUnit
    from app.schemas.enums import ExtractionRoute, PageDifficulty
    from tests.extraction_fakes import DEFAULT_EXTRACTION, FakeLLMClient
    import uuid

    units = [
        ReadingUnit(index=1, kind="page", text="English page.", language="en", script="latin"),
        ReadingUnit(index=2, kind="page", text="Tamil page.", language="ta", script="tamil"),
    ]
    agent = NemotronExtractionAgent(
        FakeLLMClient(extraction=DEFAULT_EXTRACTION), Settings(reading_cache_enabled=False)
    )
    output = agent.extract(ProcessedDocument("pdf", units), source_id=uuid.uuid4())
    spans = {s.language: s.pages for s in output.result.languages}
    assert spans == {"en": [1], "ta": [2]}
    assert output.result.language == "en"  # dominant, existing field preserved
    # The prompt received the detected languages.
    chat = [c for c in agent.client.calls if c["kind"] == "chat_json"][0]
    assert "en" in chat["user"] and "ta" in chat["user"]
    assert "never translate" in chat["user"]