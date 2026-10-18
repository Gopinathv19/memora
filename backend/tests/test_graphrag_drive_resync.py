"""Drive change detection: pure comparison of imported and current modification times."""

from app.graphrag.drive_resync import DriveChange, _parse, detect_changes


def imp(file_id, modified="2026-10-01T10:00:00.000Z", source="s-1", name="a.pdf"):
    return {"drive_file_id": file_id, "source_id": source, "file_name": name, "modified_time": modified}


def drive(file_id, modified):
    return {"id": file_id, "modified_time": modified}


def test_a_file_modified_after_it_was_imported_is_reported():
    got = detect_changes([imp("f1")], [drive("f1", "2026-10-05T08:00:00.000Z")])
    assert got == [DriveChange("f1", "a.pdf", "s-1", "modified", "2026-10-01T10:00:00.000Z", "2026-10-05T08:00:00.000Z")]


def test_an_unchanged_or_older_file_is_not():
    assert detect_changes([imp("f1")], [drive("f1", "2026-10-01T10:00:00.000Z")]) == []
    assert detect_changes([imp("f1")], [drive("f1", "2026-09-01T10:00:00.000Z")]) == []


def test_a_file_drive_no_longer_lists_is_reported_as_removed():
    got = detect_changes([imp("f1")], [drive("other", "2026-10-05T08:00:00.000Z")])
    assert [(c.drive_file_id, c.reason, c.drive_modified_time) for c in got] == [("f1", "removed", None)]


def test_files_never_imported_are_ignored_and_the_result_is_ordered_by_file_id():
    got = detect_changes([imp("f2"), imp("f1")], [drive("f1", "2026-10-09T00:00:00Z"), drive("f2", "2026-10-09T00:00:00Z"), drive("new", "2026-10-09T00:00:00Z")])
    assert [c.drive_file_id for c in got] == ["f1", "f2"]


def test_a_time_that_cannot_be_read_is_never_a_change():
    assert detect_changes([imp("f1", modified="garbage")], [drive("f1", "2026-10-05T08:00:00Z")]) == []
    assert detect_changes([imp("f1")], [drive("f1", None)]) == []
    assert detect_changes([imp("f1")], [drive("f1", "not a date")]) == []


def test_times_with_and_without_a_zone_compare_correctly():
    assert _parse("2026-10-01T10:00:00Z") == _parse("2026-10-01T10:00:00+00:00") == _parse("2026-10-01T10:00:00")
    assert detect_changes([imp("f1", modified="2026-10-01T10:00:00")], [drive("f1", "2026-10-01T10:00:01Z")])
