"""Phase 5 tests: chunk quality gate, dedupe, sentence dedupe."""

from app.chunking.chunker import ChunkData
from app.chunking.quality import (
    apply_quality,
    dedupe_chunks,
    dedupe_sentences,
    gate_chunk,
)
from app.core.config import Settings


def _chunk(content: str, ctype: str = "text") -> ChunkData:
    return ChunkData(
        content=content,
        embedding_text=content,
        token_count=len(content.split()),
        content_type=ctype,
        page_start=1,
        page_end=1,
        unit_indices=[0],
        section_path=["Section"],
    )


S = Settings()


def test_gate_rejects_short_chunk():
    result = gate_chunk(_chunk("tiny"), S)
    assert not result.ok
    assert any("too short" in r for r in result.reasons)


def test_gate_rejects_junk_tokens():
    result = gate_chunk(_chunk("A perfectly long chunk of text <unk> with junk inside it."), S)
    assert not result.ok
    assert any("junk" in r for r in result.reasons)


def test_gate_rejects_low_unique_ratio():
    result = gate_chunk(_chunk(" ".join(["word"] * 100)), S)
    assert not result.ok
    assert any("unique-token" in r for r in result.reasons)


def test_gate_rejects_loop_compression():
    result = gate_chunk(_chunk("The same phrase. " * 60), S)
    assert not result.ok
    assert any("compression" in r for r in result.reasons)


def test_gate_accepts_normal_chunk():
    text = (
        "The invoice covers consulting delivered in September. Payment terms "
        "are net thirty days. The buyer operates from Chennai and ships to "
        "two depots. Shipping charges of 450 INR apply to this order."
    )
    assert gate_chunk(_chunk(text), S).ok


def test_dedupe_exact_normalized_match():
    a = _chunk("The invoice was issued on 2026-09-01 to Globex.")
    b = _chunk("the   invoice was ISSUED on 2026-09-01 to globex.")
    kept = dedupe_chunks([a, b], S)
    assert len(kept) == 1


def test_dedupe_near_duplicate():
    a = _chunk("The invoice was issued on 2026-09-01 to Globex Corporation for services.")
    b = _chunk("The invoice was issued on 2026-09-01 to Globex Corporation for service.")
    kept = dedupe_chunks([a, b], S)
    assert len(kept) == 1


def test_dedupe_keeps_distinct_chunks():
    a = _chunk("The invoice was issued on 2026-09-01 to Globex Corporation.")
    b = _chunk("Payment terms are net thirty days from the invoice date stated.")
    kept = dedupe_chunks([a, b], S)
    assert len(kept) == 2


def test_sentence_dedupe_within_chunk():
    text = (
        "The buyer operates from Chennai. The buyer operates from Chennai. "
        "Shipping charges apply. "
    )
    out = dedupe_sentences(text)
    assert out.count("The buyer operates from Chennai.") == 1
    assert "Shipping charges apply." in out


def test_sentence_dedupe_tamil_danda():
    # Tamil sentence ender "।"-style (danda) must also split.
    text = "விலை பட்டியல் இதோ. மொத்தம் ரூ.450. விலை பட்டியல் இதோ."
    out = dedupe_sentences(text)
    assert out.count("விலை பட்டியல் இதோ.") == 1


def test_apply_quality_end_to_end():
    good = _chunk(
        "The invoice covers consulting delivered in September. Payment terms "
        "are net thirty days from the invoice date."
    )
    dup = _chunk(
        "the invoice covers consulting delivered in september. payment terms "
        "are net thirty days from the invoice date."
    )
    junk = _chunk("A chunk with <unk> tokens that is long enough otherwise.")
    notes: list[str] = []
    kept = apply_quality([good, dup, junk], S, notes)
    assert len(kept) == 1
    assert any("duplicate" in n for n in notes)
    assert any("junk" in n for n in notes)

def test_regex_identifiers_backstop():
    from types import SimpleNamespace as NS

    from app.agents.extraction_agent import _regex_identifiers
    from app.schemas.extraction import ExtractedField

    reading = NS(
        text="**Enrolment No.: 2726/10022/47657**\nAadhaar 1234 5678 9012\nMobile: 6755589076",
        unit=NS(index=1),
    )
    fields = [ExtractedField(key="aadhaar_number", value="1234 5678 9012")]
    _regex_identifiers([reading], fields)
    got = {f.key: f.value for f in fields}
    assert got["enrolment_number"] == "2726/10022/47657"
    assert [f.key for f in fields].count("aadhaar_number") == 1


def test_regex_identifiers_do_not_take_a_vid_tail_for_an_aadhaar_number():
    from types import SimpleNamespace as NS

    from app.agents.extraction_agent import _regex_identifiers

    reading = NS(
        text="Aadhaar 4036 4248 6500\nVID: 9147 9825 3240 4171", unit=NS(index=1)
    )
    fields: list = []
    _regex_identifiers([reading], fields)
    got = {(f.key, f.value) for f in fields}
    assert ("aadhaar_number", "4036 4248 6500") in got
    assert ("vid", "9147 9825 3240 4171") in got
    assert ("aadhaar_number", "9825 3240 4171") not in got
