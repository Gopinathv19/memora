"""Chunking pipeline tests: readings → units → blocks → chunks.

Pure tests for the chunking package (no DB, no network), plus the strategy's
§32 example document as the primary fixture. These verify the core rules:
semantic boundaries before token boundaries, tables split by row groups with
repeated headers, forms/key-value kept coherent, section context in the
embedding representation, and provenance preserved.
"""

import pytest

from app.chunking.blocks import build_semantic_blocks
from app.chunking.chunker import chunk_semantic_block
from app.chunking.representation import build_embedding_text, count_tokens
from app.chunking.structure import assign_section_paths, detect_numbered_headings
from app.chunking.units import parse_readings
from app.core.config import Settings

SETTINGS = Settings(database_url="postgresql+psycopg://unused/unused")


# --- The strategy's §32 example document ----------------------------------------

EXAMPLE_READINGS = [
    {
        "page": 1,
        "kind": "page",
        "difficulty": "easy",
        "route": "text",
        "model": None,
        "status": "ok",
        "text": (
            "# Employee Information\n\n"
            "## Personal Details\n"
            "Name: John Smith\n"
            "DOB: 10 Jan 1995\n"
            "Address: Chennai\n\n"
            "## Employment Details\n"
            "Company: ABC Technologies\n"
            "Role: Software Engineer\n\n"
            "## Employment History\n\n"
            "| Company | Role | From | To |\n"
            "|---------|------|------|----|\n"
            "| XYZ | Developer | 2021 | 2023 |\n"
            "| ABC | Engineer | 2024 | Now |"
        ),
        "note": None,
    },
]


def _pipeline(readings):
    units = parse_readings(readings)
    units = detect_numbered_headings(units)
    units = assign_section_paths(units)
    blocks = build_semantic_blocks(units)
    return units, blocks


# --- Unit parsing ---------------------------------------------------------------


def test_readings_are_parsed_into_elements():
    units = parse_readings(EXAMPLE_READINGS)
    kinds = [u.kind for u in units]
    assert "heading" in kinds
    assert "key_value" in kinds
    assert "table" in kinds


def test_headings_get_correct_levels():
    units = parse_readings(EXAMPLE_READINGS)
    headings = [u for u in units if u.kind == "heading"]
    assert headings[0].content == "Employee Information"
    assert headings[0].heading_level == 1
    assert headings[1].content == "Personal Details"
    assert headings[1].heading_level == 2


def test_key_value_fields_are_grouped():
    units = parse_readings(EXAMPLE_READINGS)
    kv = [u for u in units if u.kind == "key_value"]
    # Two key/value groups: Personal Details and Employment Details
    assert len(kv) == 2
    assert "Name: John Smith" in kv[0].content
    assert "Company: ABC Technologies" in kv[1].content


def test_table_is_detected_as_one_element():
    units = parse_readings(EXAMPLE_READINGS)
    tables = [u for u in units if u.kind == "table"]
    assert len(tables) == 1
    assert "| Company | Role | From | To |" in tables[0].content


# --- Structure detection --------------------------------------------------------


def test_section_paths_form_hierarchy():
    units, _ = _pipeline(EXAMPLE_READINGS)
    # Personal Details units should have path [Employee Information, Personal Details]
    personal = [u for u in units if u.kind == "key_value" and "Name:" in u.content][0]
    assert personal.section_path == ["Employee Information", "Personal Details"]

    employment = [u for u in units if u.kind == "key_value" and "Company:" in u.content][0]
    assert employment.section_path == ["Employee Information", "Employment Details"]


def test_numbered_headings_are_promoted():
    readings = [
        {"page": 1, "kind": "page", "difficulty": "easy", "route": "text",
         "model": None, "status": "ok", "text": "1. Employee Information\n1.1 Personal Details\nName: John", "note": None},
    ]
    units = parse_readings(readings)
    units = detect_numbered_headings(units)
    headings = [u for u in units if u.kind == "heading"]
    assert len(headings) == 2
    assert headings[0].heading_level == 1
    assert headings[0].content == "Employee Information"
    assert headings[1].heading_level == 2
    assert headings[1].content == "Personal Details"


# --- Semantic blocks ------------------------------------------------------------


def test_blocks_match_sections():
    _, blocks = _pipeline(EXAMPLE_READINGS)
    # 4 blocks: Employee Information (heading-only), Personal Details,
    # Employment Details, Employment History
    assert len(blocks) == 4
    paths = [b.section_path for b in blocks]
    assert ["Employee Information"] in paths
    assert ["Employee Information", "Personal Details"] in paths
    assert ["Employee Information", "Employment Details"] in paths
    assert ["Employee Information", "Employment History"] in paths


def test_block_has_page_range():
    _, blocks = _pipeline(EXAMPLE_READINGS)
    for b in blocks:
        assert b.page_start == 1
        assert b.page_end == 1


def test_block_content_includes_all_units():
    _, blocks = _pipeline(EXAMPLE_READINGS)
    history = [b for b in blocks if "Employment History" in b.section_path][0]
    assert "| Company | Role | From | To |" in history.content
    assert "| XYZ | Developer | 2021 | 2023 |" in history.content


# --- Chunking -------------------------------------------------------------------


def test_small_blocks_become_single_chunks():
    _, blocks = _pipeline(EXAMPLE_READINGS)
    for block in blocks:
        chunks = chunk_semantic_block(block, SETTINGS)
        assert len(chunks) == 1


def test_chunk_has_section_context_in_embedding_text():
    _, blocks = _pipeline(EXAMPLE_READINGS)
    personal = [b for b in blocks if "Personal Details" in b.section_path][0]
    chunk = chunk_semantic_block(personal, SETTINGS)[0]
    assert "Section: Employee Information > Personal Details" in chunk.embedding_text


def test_chunk_has_token_count():
    _, blocks = _pipeline(EXAMPLE_READINGS)
    for block in blocks:
        chunk = chunk_semantic_block(block, SETTINGS)[0]
        assert chunk.token_count > 0


def test_chunk_content_type_is_classified():
    _, blocks = _pipeline(EXAMPLE_READINGS)
    block_types = {b.section_path[-1]: chunk_semantic_block(b, SETTINGS)[0].content_type for b in blocks}
    assert block_types["Personal Details"] == "key_value"
    assert block_types["Employment Details"] == "key_value"
    assert block_types["Employment History"] == "table"


def test_short_coherent_section_stays_small():
    """RULE 6: a short coherent section can be smaller than 300 tokens."""
    _, blocks = _pipeline(EXAMPLE_READINGS)
    for block in blocks:
        chunk = chunk_semantic_block(block, SETTINGS)[0]
        # All blocks in the example are small; they should stay as-is.
        assert chunk.token_count < 300


# --- Table splitting ------------------------------------------------------------


def test_large_table_splits_by_row_groups_with_repeated_headers():
    # Build a table large enough to exceed the soft max.
    rows = "\n".join(f"| Company {i} | Role {i} | 2021 | 2023 |" for i in range(100))
    table_text = (
        "| Company | Role | From | To |\n"
        "|---------|------|------|----|\n"
        + rows
    )
    readings = [
        {"page": 1, "kind": "page", "difficulty": "easy", "route": "text",
         "model": None, "status": "ok", "text": f"## Big Table\n\n{table_text}", "note": None},
    ]
    units, blocks = _pipeline(readings)
    block = [b for b in blocks if "Big Table" in b.section_path][0]
    chunks = chunk_semantic_block(block, SETTINGS)
    assert len(chunks) > 1
    # Every chunk should repeat the table header.
    for chunk in chunks:
        assert "| Company | Role | From | To |" in chunk.content
        assert "|---------|------|------|----|" in chunk.content


# --- Embedding representation ----------------------------------------------------


def test_embedding_text_includes_section_prefix():
    text = build_embedding_text(["A", "B", "C"], "content here")
    assert text == "Section: A > B > C\n\ncontent here"


def test_embedding_text_without_section_path():
    text = build_embedding_text([], "just content")
    assert text == "just content"


def test_token_count_is_approximate():
    assert count_tokens("") == 0
    assert count_tokens("a" * 400) == 100  # 400 chars / 4 = 100 tokens
    assert count_tokens("short") >= 1


# --- Empty / edge cases ----------------------------------------------------------


def test_empty_readings_produce_no_units():
    units = parse_readings([])
    assert units == []


def test_empty_readings_produce_no_blocks():
    units = parse_readings([])
    units = assign_section_paths(units)
    blocks = build_semantic_blocks(units)
    assert blocks == []


def test_heading_only_document():
    readings = [
        {"page": 1, "kind": "page", "difficulty": "easy", "route": "text",
         "model": None, "status": "ok", "text": "# Title\n## Subtitle", "note": None},
    ]
    units, blocks = _pipeline(readings)
    assert len(units) == 2
    assert all(u.kind == "heading" for u in units)
    assert len(blocks) == 2


def test_multi_page_section_stays_together():
    """RULE 3: page boundaries are not chunk boundaries."""
    readings = [
        {"page": 1, "kind": "page", "difficulty": "easy", "route": "text",
         "model": None, "status": "ok", "text": "# Section\nParagraph one on page one.", "note": None},
        {"page": 2, "kind": "page", "difficulty": "easy", "route": "text",
         "model": None, "status": "ok", "text": "Paragraph two on page two.", "note": None},
    ]
    units, blocks = _pipeline(readings)
    # Both paragraphs should be in the same block (same section).
    assert len(blocks) == 1
    assert blocks[0].page_start == 1
    assert blocks[0].page_end == 2
    assert "Paragraph one" in blocks[0].content
    assert "Paragraph two" in blocks[0].content
