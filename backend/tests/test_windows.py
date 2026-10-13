"""Phase 6 tests: window building, reduce logic, coverage reporting."""

import uuid

from app.agents.windows import build_windows, merge_fields, merge_tables, reduce_windows
from app.core.config import Settings


def test_windows_pack_whole_units():
    sections = [(f"Unit {i} text. " * 50, i) for i in range(1, 6)]  # ~350 chars each
    windows = build_windows(sections, max_chars=1000, overlap_units=1)
    assert len(windows) >= 2
    # No unit is ever split across windows.
    for w in windows:
        for i in w.unit_indices:
            assert f"Unit {i} text." in w.text
    # Overlap: consecutive windows share at least one unit.
    for a, b in zip(windows, windows[1:]):
        assert set(a.unit_indices) & set(b.unit_indices)


def test_oversized_unit_becomes_own_window():
    big = ("x" * 5000, 1)
    small = ("y" * 100, 2)
    windows = build_windows([big, small], max_chars=1000)
    assert len(windows) == 2
    assert windows[0].text == "x" * 5000


def test_all_units_covered():
    sections = [(f"Unit {i}", i) for i in range(1, 11)]
    windows = build_windows(sections, max_chars=20, overlap_units=1)
    covered = {u for w in windows for u in w.unit_indices}
    assert covered == set(range(1, 11))


def test_merge_fields_dedupes_same_value():
    fields = [
        {"key": "invoice_number", "value": "INV-2041", "page": 1, "confidence": 0.9},
        {"key": "invoice_number", "value": "INV-2041", "page": 2, "confidence": 0.95},
    ]
    kept, conflicts, duplicates = merge_fields(fields)
    assert len(kept) == 1
    assert duplicates == 1
    assert kept[0]["confidence"] == 0.95  # better citation won
    assert conflicts == []


def test_merge_fields_conflict_keeps_higher_confidence():
    fields = [
        {"key": "total_amount", "value": "12,400 INR", "page": 1, "confidence": 0.7},
        {"key": "total_amount", "value": "12400", "page": 3, "confidence": 0.95},
    ]
    kept, conflicts, _ = merge_fields(fields)
    assert kept[0]["value"] == "12400"
    assert len(conflicts) == 1
    assert conflicts[0].dropped == "12,400 INR"


def test_merge_tables_continuation_same_header():
    tables = [
        {"title": "Items", "page": 1, "columns": ["Item", "Qty"],
         "rows": [["Widget", "4"]]},
        {"title": "Items", "page": 2, "columns": ["Item", "Qty"],
         "rows": [["Gadget", "2"]]},
    ]
    merged, count = merge_tables(tables)
    assert len(merged) == 1
    assert merged[0]["rows"] == [["Widget", "4"], ["Gadget", "2"]]
    assert count == 1


def test_merge_tables_headerless_continuation():
    tables = [
        {"title": "Items", "page": 1, "columns": ["Item", "Qty"],
         "rows": [["Widget", "4"]]},
        {"title": None, "page": 2, "columns": [],
         "rows": [["Gadget", "2"]]},
    ]
    merged, count = merge_tables(tables)
    assert len(merged) == 1
    assert merged[0]["rows"] == [["Widget", "4"], ["Gadget", "2"]]


def test_merge_tables_distinct_tables_kept():
    tables = [
        {"title": "Items", "columns": ["Item", "Qty"], "rows": [["Widget", "4"]]},
        {"title": "Totals", "columns": ["Label", "Amount"], "rows": [["Sum", "9"]]},
    ]
    merged, _ = merge_tables(tables)
    assert len(merged) == 2


def test_merge_tables_overlap_rows_not_repeated():
    row = ["Widget", "4"]
    tables = [
        {"title": "Items", "columns": ["Item", "Qty"], "rows": [row]},
        {"title": "Items", "columns": ["Item", "Qty"], "rows": [row, ["Gadget", "2"]]},
    ]
    merged, _ = merge_tables(tables)
    assert merged[0]["rows"] == [row, ["Gadget", "2"]]


def test_reduce_windows_end_to_end():
    w1 = {
        "fields": [{"key": "invoice_number", "value": "INV-2041", "page": 1, "confidence": 0.9}],
        "tables": [{"title": "Items", "columns": ["Item"], "rows": [["A"]]}],
    }
    w2 = {  # overlap window: same field, table continuation
        "fields": [{"key": "invoice_number", "value": "INV-2041", "page": 2, "confidence": 0.95}],
        "tables": [{"title": "Items", "columns": ["Item"], "rows": [["A"], ["B"]]}],
    }
    reduced = reduce_windows([w1, w2])
    assert len(reduced.fields) == 1
    assert reduced.duplicate_fields == 1
    assert len(reduced.tables) == 1
    assert reduced.tables[0]["rows"] == [["A"], ["B"]]
    assert reduced.merged_tables == 1


def test_agent_reports_coverage():
    from app.agents.extraction_agent import NemotronExtractionAgent
    from app.processing.document import ProcessedDocument, ReadingUnit
    from tests.extraction_fakes import DEFAULT_EXTRACTION, FakeLLMClient

    units = [
        ReadingUnit(index=1, kind="page", text="First page of real content here."),
        ReadingUnit(index=2, kind="page", text="Second page with more content."),
    ]
    agent = NemotronExtractionAgent(
        FakeLLMClient(extraction=DEFAULT_EXTRACTION), Settings(reading_cache_enabled=False)
    )
    output = agent.extract(ProcessedDocument("pdf", units), source_id=uuid.uuid4())
    assert output.result.pages_read == 2
    assert output.result.pages_skipped == 0
    assert output.result.units_failed == 0
    assert any("coverage:" in w for w in output.result.warnings)
    # No truncation warning anymore.
    assert not any("truncated" in w for w in output.result.warnings)