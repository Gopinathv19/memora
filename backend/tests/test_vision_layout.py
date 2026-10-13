"""Phase 2 tests: decorative-image filtering, prompt separation, token scaling."""

from app.core.config import Settings
from app.processing.document import ImageBlob
from app.processing.parsers import filter_decorative_images
from app.agents import prompts


def _png_bytes(size=(300, 200), color=(10, 10, 200)):
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def test_tiny_images_are_decorative():
    s = Settings()
    blobs = [ImageBlob(_png_bytes((20, 20)), "image/png")]
    kept, skipped = filter_decorative_images(blobs, s)
    assert kept == [] and skipped == 1


def test_extreme_aspect_ratio_is_decorative():
    s = Settings()
    blobs = [ImageBlob(_png_bytes((1600, 20)), "image/png")]  # 80:1
    kept, skipped = filter_decorative_images(blobs, s)
    assert kept == [] and skipped == 1


def test_duplicate_content_hash_is_decorative():
    s = Settings()
    data = _png_bytes((300, 200))
    blobs = [ImageBlob(data, "image/png"), ImageBlob(data, "image/png")]
    kept, skipped = filter_decorative_images(blobs, s)
    assert len(kept) == 1 and skipped == 1


def test_normal_images_are_kept():
    s = Settings()
    blobs = [
        ImageBlob(_png_bytes((300, 200), (1, 2, 3)), "image/png"),
        ImageBlob(_png_bytes((400, 300), (4, 5, 6)), "image/png"),
    ]
    kept, skipped = filter_decorative_images(blobs, s)
    assert len(kept) == 2 and skipped == 0


def test_transcription_prompt_differs_from_layout_and_vision():
    assert prompts.TRANSCRIPTION_PROMPT != prompts.LAYOUT_PROMPT
    assert prompts.TRANSCRIPTION_PROMPT != prompts.VISION_PROMPT
    assert "never translate" in prompts.TRANSCRIPTION_PROMPT


def test_page_max_tokens_scales_with_text():
    from app.agents.extraction_agent import NemotronExtractionAgent
    from app.processing.document import ReadingUnit

    agent = NemotronExtractionAgent(client=None, settings=Settings())
    empty = ReadingUnit(index=1, kind="page", text="")
    dense = ReadingUnit(index=2, kind="page", text="x" * 5000)
    assert agent._page_max_tokens(empty) == 1024
    assert agent._page_max_tokens(dense) == 8192  # 5000*2 capped at 8192
    huge = ReadingUnit(index=3, kind="page", text="x" * 100000)
    assert agent._page_max_tokens(huge) == Settings().vision_max_tokens_cap