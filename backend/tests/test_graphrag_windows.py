"""Extraction windows: packing blocks, splitting big ones, linking entities to chunks (pure)."""

from app.graphrag.windows import ChunkRef, chunks_mentioning, pack_windows


def chunk(cid, index, tokens, text="text", **kw):
    return ChunkRef(id=cid, index=index, text=text, tokens=tokens, **kw)


def block(bid, path, chunks, title=None):
    return {"id": bid, "section_path": path, "title": title, "chunks": chunks}


def pack(blocks, **kw):
    return pack_windows(blocks, source_id="s1", extraction_id="e1", source_version=1, **kw)


def test_tiny_blocks_are_packed_into_one_window_in_order():
    blocks = [block(f"b{i}", ["A"], [chunk(f"c{i}", i, 60)]) for i in range(10)]
    windows = pack(blocks, budget=1000)
    assert len(windows) == 1
    assert windows[0].chunk_ids == [f"c{i}" for i in range(10)]
    assert windows[0].tokens == 600


def test_window_closes_at_the_budget():
    blocks = [block(f"b{i}", ["A"], [chunk(f"c{i}", i, 400)]) for i in range(5)]
    windows = pack(blocks, budget=1000)
    assert [w.tokens for w in windows] == [800, 800, 400]
    assert [w.id for w in windows] == ["e1:0", "e1:1", "e1:2"]


def test_window_closes_early_at_top_level_section_change_once_big_enough():
    blocks = [
        block("b1", ["Intro"], [chunk("c1", 0, 500)]),
        block("b2", ["Terms"], [chunk("c2", 1, 100)]),
    ]
    assert len(pack(blocks, budget=1000, min_tokens=400)) == 2      # 500 >= min: split at the section change
    assert len(pack(blocks, budget=1000, min_tokens=600)) == 1      # 500 < min: keep packing


def test_oversized_block_is_split_at_chunk_boundaries_and_never_cut_mid_chunk():
    chunks = [chunk(f"c{i}", i, 700) for i in range(4)]            # 2800 tokens > max 1800
    windows = pack([block("big", ["A"], chunks)], budget=1000, max_tokens=1800)
    assert [w.chunk_ids for w in windows] == [["c0"], ["c1"], ["c2"], ["c3"]]
    assert all(w.segments[0].block_id == "big" for w in windows)


def test_block_without_active_chunks_is_skipped_and_empty_input_gives_no_windows():
    assert pack([block("b", ["A"], [])]) == []
    assert pack([]) == []


def test_chunks_inside_a_block_are_ordered_by_index():
    chunks = [chunk("c2", 2, 10), chunk("c0", 0, 10), chunk("c1", 1, 10)]
    assert pack([block("b", ["A"], chunks)])[0].chunk_ids == ["c0", "c1", "c2"]


def test_render_labels_each_segment_with_its_section_path():
    w = pack([
        block("b1", ["Contract", "Termination"], [chunk("c1", 0, 10, "Either party may terminate.")]),
        block("b2", [], [chunk("c2", 1, 10, "Loose paragraph.")], title=None),
    ])[0]
    text = w.render()
    assert "[Section: Contract > Termination]\nEither party may terminate." in text
    assert text.endswith("Loose paragraph.")


def test_pages_label():
    w = pack([block("b", ["A"], [chunk("c1", 0, 10, page_start=3, page_end=4), chunk("c2", 1, 10, page_start=4)])])[0]
    assert w.pages == "pages 3-4"
    assert pack([block("b", ["A"], [chunk("c", 0, 10)])])[0].pages is None


def test_chunks_mentioning_uses_word_boundaries_and_case_folding():
    w = pack([block("b", ["A"], [
        chunk("c1", 0, 10, "Acme Corp shall refund the fees."),
        chunk("c2", 1, 10, "The  ACME   Corp\nhas offices abroad."),
        chunk("c3", 1, 10, "A ribbmo is not an IBM."),
    ])])[0]
    assert chunks_mentioning(w, ["Acme Corp"]) == ["c1", "c2"]          # case and whitespace folded
    assert chunks_mentioning(w, ["IBM"]) == ["c3"]
    assert chunks_mentioning(w, ["bmo"]) == []                          # no mid-word match
    assert chunks_mentioning(w, ["Acme Corp", "IBM"]) == ["c1", "c2", "c3"]
    assert chunks_mentioning(w, []) == [] and chunks_mentioning(w, ["  "]) == []
