"""Tests for evals.reports: payload building and both report formats."""

import json

from evals.reports import build_payload, write_json, write_markdown, write_reports


def _payload(**overrides):
    base = {
        "label": "test-run",
        "summary": {
            "overall": {"n": 2, "recall_at_5": 0.5, "mrr": 0.75},
            "by_query_type": {"factoid": {"n": 2, "recall_at_5": 0.5}},
            "by_difficulty": {},
            "by_tenant": {"t-1": {"n": 2}},
            "abstention": {"refusal_rate": 1.0, "false_answer_rate": 0.0},
            "operational": {"latency_p50_ms": 100.0, "latency_p95_ms": 200.0},
            "annotation_coverage": {"expected_answer": 2},
            "warnings": ["TTFT: N/A (not exposed)"],
        },
        "per_example": [
            {"id": "q1", "query": "What?", "error": None},
            {"id": "q2", "query": "Where?", "error": "LLMError: timeout"},
        ],
        "run_info": {
            "mode": "full",
            "dataset": "eval.jsonl",
            "dataset_sha256": "abc123",
            "git_commit": "deadbeef",
            "models": {"answer": "nvidia/x"},
        },
    }
    base.update(overrides)
    return build_payload(**base)


def test_build_payload_structure():
    payload = _payload()
    assert payload["run_info"]["label"] == "test-run"
    assert "timestamp" in payload["run_info"]
    assert payload["summary"]["overall"]["n"] == 2
    assert len(payload["examples"]) == 2


def test_write_json_roundtrip(tmp_path):
    payload = _payload()
    path = write_json(tmp_path / "r.json", payload)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["summary"]["overall"]["n"] == 2
    assert loaded["examples"][1]["error"] == "LLMError: timeout"


def test_write_markdown_sections(tmp_path):
    payload = _payload()
    path = write_markdown(tmp_path / "r.md", payload)
    text = path.read_text(encoding="utf-8")
    assert "# Eval report: test-run" in text
    assert "## Overall" in text
    assert "## By query type" in text
    assert "## Abstention" in text
    assert "## Operational" in text
    assert "## Warnings" in text
    assert "TTFT: N/A" in text
    assert "## Failures (1)" in text
    assert "LLMError: timeout" in text


def test_write_markdown_handles_missing_sections(tmp_path):
    payload = _payload(
        summary={"overall": {"n": 0}, "warnings": []},
        per_example=[{"id": "q1", "query": "What?", "error": None}],
    )
    text = write_markdown(tmp_path / "r.md", payload).read_text(encoding="utf-8")
    assert "## Overall" in text
    assert "## Failures" not in text
    assert "## By query type" not in text


def test_write_reports_pair(tmp_path):
    payload = _payload()
    json_path, md_path = write_reports(tmp_path, "label", payload)
    assert json_path.exists() and md_path.exists()
    assert json_path.suffix == ".json" and md_path.suffix == ".md"
    assert "label" in json_path.name
