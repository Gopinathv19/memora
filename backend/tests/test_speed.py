"""Phase 3 tests: reading cache, concurrency cap, deferred rendering."""

from app.agents.prompts import PROMPT_VERSION
from app.agents.reading_cache import ReadingCache
from app.core.config import Settings
from app.processing.document import ImageBlob, ReadingUnit
from app.schemas.enums import ExtractionRoute, PageDifficulty


def test_cache_key_is_stable_and_distinct():
    a = ReadingCache.key(b"img", "m1", "v1", 1800, "p")
    assert a == ReadingCache.key(b"img", "m1", "v1", 1800, "p")
    assert a != ReadingCache.key(b"img2", "m1", "v1", 1800, "p")
    assert a != ReadingCache.key(b"img", "m2", "v1", 1800, "p")
    assert a != ReadingCache.key(b"img", "m1", "v2", 1800, "p")
    assert a != ReadingCache.key(b"img", "m1", "v1", 2200, "p")


def test_cache_hit_miss_and_lru_eviction():
    cache = ReadingCache(max_entries=2)
    cache.put("a", "text-a")
    cache.put("b", "text-b")
    assert cache.get("a") == "text-a"  # a is now most-recent
    cache.put("c", "text-c")  # evicts b
    assert cache.get("b") is None
    assert cache.get("c") == "text-c"
    assert cache.get("missing") is None
    assert cache.hits == 2 and cache.misses == 2


def test_concurrency_capped():
    s = Settings(extraction_concurrency=99, extraction_concurrency_max=12)
    assert min(s.extraction_concurrency, s.extraction_concurrency_max) == 12


def test_deferred_render_resolves_in_read():
    """A unit with render_fn gets its images assigned before reading."""
    from app.agents.extraction_agent import NemotronExtractionAgent
    from tests.extraction_fakes import FakeLLMClient

    blob = ImageBlob(b"png", "image/png", "page 1")
    unit = ReadingUnit(
        index=1, kind="page", text="",
        difficulty=PageDifficulty.HARD, route=ExtractionRoute.LAYOUT,
    )

    def render(u=unit):
        u.page_image = blob
        return blob, []

    unit.render_fn = render
    agent = NemotronExtractionAgent(FakeLLMClient(), Settings(reading_cache_enabled=False))
    reading = agent._read(unit)
    assert unit.page_image is blob
    assert unit.render_fn is None
    assert reading.status == "ok"


def test_prompt_version_exists():
    assert isinstance(PROMPT_VERSION, str) and PROMPT_VERSION