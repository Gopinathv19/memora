"""Tests for evals.dataset: JSONL loading, line-numbered errors, coverage."""

import json

import pytest

from evals.dataset import checksum, coverage, load, load_lenient
from evals.schemas import RecordError


def _write(tmp_path, records, name="eval.jsonl"):
    path = tmp_path / name
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


def _valid(**overrides):
    base = {
        "query": "What is the renewal date?",
        "tenant_id": "t-1",
        "expected_answer": "15 March 2026.",
        "supporting_chunk_ids": ["c1"],
        "query_type": "factoid",
        "difficulty": "easy",
    }
    base.update(overrides)
    return base


def test_load_valid(tmp_path):
    path = _write(tmp_path, [_valid(), _valid(query="Another query?")])
    examples = load(path)
    assert len(examples) == 2


def test_load_reports_line_number(tmp_path):
    records = [_valid(), _valid(query=""), _valid(query="Third?")]
    path = _write(tmp_path, records)
    with pytest.raises(RecordError) as err:
        load(path)
    assert err.value.line_no == 2


def test_load_invalid_json_line_number(tmp_path):
    path = tmp_path / "eval.jsonl"
    path.write_text(
        json.dumps(_valid()) + "\nnot json at all\n",
        encoding="utf-8",
    )
    with pytest.raises(RecordError) as err:
        load(path)
    assert err.value.line_no == 2
    assert "invalid JSON" in err.value.reason


def test_load_lenient_collects_errors(tmp_path):
    records = [_valid(), _valid(query=""), _valid(query="Third?")]
    path = _write(tmp_path, records)
    examples, errors = load_lenient(path)
    assert len(examples) == 2
    assert len(errors) == 1
    assert errors[0].line_no == 2


def test_load_skips_blank_lines(tmp_path):
    path = tmp_path / "eval.jsonl"
    path.write_text("\n\n" + json.dumps(_valid()) + "\n\n", encoding="utf-8")
    assert len(load(path)) == 1


def test_load_legacy(tmp_path):
    legacy = {
        "id": "q001",
        "question": "What is the enrolment number?",
        "type": "exact_lookup",
        "reference_answer": "ENR-5590",
        "expected_behavior": "answer",
        "expected_chunks": [{"chunk_id": "c1", "source_id": "s1",
                              "page_start": 2, "page_end": 2}],
        "tenant_id": "t-1",
    }
    path = _write(tmp_path, [legacy])
    examples = load(path, legacy=True)
    assert examples[0].supporting_chunk_ids == ["c1"]
    assert examples[0].query_type == "exact_lookup"


def test_coverage(tmp_path):
    records = [
        _valid(),
        _valid(query="Second?", difficulty="hard"),
        _valid(query="Third?", difficulty="medium", is_unanswerable=True,
               expected_answer=None, supporting_chunk_ids=[],
               query_type="unanswerable"),
    ]
    path = _write(tmp_path, records)
    cov = coverage(load(path))
    assert cov["total"] == 3
    assert cov["answerable"] == 2
    assert cov["unanswerable"] == 1
    assert cov["by_difficulty"]["easy"] == 1
    assert cov["by_difficulty"]["hard"] == 1
    assert cov["by_difficulty"]["medium"] == 1
    assert cov["by_tenant"]["t-1"] == 3
    assert cov["annotation_coverage"]["expected_answer"] == 2


def test_checksum_stable(tmp_path):
    path = _write(tmp_path, [_valid()])
    c1 = checksum(path)
    assert c1 == checksum(path)
    assert len(c1) == 64
    _write(tmp_path, [_valid(), _valid(query="x?")])
    assert checksum(path) != c1
