"""Tests for evals.schemas: validation rules and legacy mapping."""

import pytest

from evals.schemas import EvalExample, RecordError, from_legacy, validate_record


def _valid(**overrides) -> dict:
    base = {
        "query": "What is the contract renewal date?",
        "tenant_id": "t-1",
        "expected_answer": "15 March 2026.",
        "supporting_chunk_ids": ["c1"],
        "supporting_pages": [3],
        "query_type": "factoid",
        "difficulty": "easy",
    }
    base.update(overrides)
    return base


def test_valid_record():
    ex = validate_record(_valid(), line_no=1)
    assert ex.query.startswith("What is")
    assert ex.supporting_chunk_ids == ["c1"]
    assert not ex.is_unanswerable


def test_missing_query_rejected():
    raw = _valid()
    del raw["query"]
    with pytest.raises(RecordError) as err:
        validate_record(raw, line_no=5)
    assert err.value.line_no == 5
    assert "query" in err.value.reason


def test_empty_query_rejected():
    with pytest.raises(RecordError):
        validate_record(_valid(query="   "), line_no=1)


def test_missing_tenant_rejected():
    raw = _valid()
    del raw["tenant_id"]
    with pytest.raises(RecordError):
        validate_record(raw, line_no=2)


def test_bad_query_type_rejected():
    with pytest.raises(RecordError):
        validate_record(_valid(query_type="nonsense"), line_no=3)


def test_bad_difficulty_rejected():
    with pytest.raises(RecordError):
        validate_record(_valid(difficulty="extreme"), line_no=3)


def test_answerable_without_answer_rejected():
    raw = _valid()
    raw["expected_answer"] = ""
    with pytest.raises(RecordError) as err:
        validate_record(raw, line_no=7)
    assert "expected_answer" in err.value.reason


def test_unanswerable_with_answer_rejected():
    raw = _valid(is_unanswerable=True)
    with pytest.raises(RecordError):
        validate_record(raw, line_no=8)


def test_unanswerable_with_chunks_rejected():
    raw = _valid(is_unanswerable=True, expected_answer=None, supporting_chunk_ids=["c1"])
    with pytest.raises(RecordError):
        validate_record(raw, line_no=9)


def test_unanswerable_valid():
    raw = _valid(is_unanswerable=True, expected_answer=None,
                 supporting_chunk_ids=[], supporting_pages=[])
    ex = validate_record(raw, line_no=10)
    assert ex.is_unanswerable
    assert ex.expected_answer is None


def test_optional_metadata_defaults():
    ex = validate_record(_valid(), line_no=1)
    assert ex.source_ids == []
    assert ex.application_id is None
    assert ex.expected_citations == []
    assert ex.notes == ""


def test_extra_fields_ignored():
    ex = validate_record(_valid(unknown_field="whatever"), line_no=1)
    assert ex.query


# --- legacy mapping ---------------------------------------------------------------


def test_from_legacy_maps_fields():
    legacy = {
        "id": "q001",
        "question": "What is the enrolment number?",
        "type": "exact_lookup",
        "reference_answer": "ENR-5590",
        "expected_behavior": "answer",
        "expected_chunks": [{
            "chunk_id": "c1", "source_id": "s1",
            "page_start": 2, "page_end": 2,
            "section_path": ["Enrolment"], "text_snippet": "…",
        }],
        "tenant_id": "t-1",
        "subject_id": "sub-1",
    }
    mapped = from_legacy(legacy)
    ex = validate_record(mapped, line_no=1)
    assert ex.query == "What is the enrolment number?"
    assert ex.query_type == "exact_lookup"
    assert ex.expected_answer == "ENR-5590"
    assert ex.supporting_chunk_ids == ["c1"]
    assert ex.supporting_pages == [2]
    assert ex.source_ids == ["s1"]
    assert not ex.is_unanswerable


def test_from_legacy_unanswerable():
    legacy = {
        "question": "What is the CEO's home address?",
        "type": "unanswerable",
        "reference_answer": "",
        "expected_behavior": "insufficient",
        "expected_chunks": [],
        "tenant_id": "t-1",
    }
    ex = validate_record(from_legacy(legacy), line_no=1)
    assert ex.is_unanswerable
    assert ex.expected_answer is None
    assert ex.query_type == "unanswerable"


def test_from_legacy_type_mapping():
    assert from_legacy({"type": "semantic"})["query_type"] == "factoid"
    assert from_legacy({"type": "multi_part"})["query_type"] == "multi_document"
