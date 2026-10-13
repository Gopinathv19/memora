"""The Extraction Agent: Document -> Extracted Information.

Its single responsibility is reading. It receives a `ProcessedDocument` (the
Document Processor's routed units), has NVIDIA models read the parts local
parsing could not, and returns one validated `ExtractionResult` plus a record
of every model call it made, plus the merged document text it read. It never
touches the database, and it creates no graph entities, chunks or embeddings --
the graph stage (app/graph) builds on that text afterwards.

The rest of Memora depends on the `ExtractionAgent` protocol, not on this
implementation or on any provider SDK.
"""

import io
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol

from app.agents import prompts
from app.agents.reading_cache import DbReadingStore, ReadingCache
from app.agents.windows import build_windows, reduce_windows
from app.core.config import Settings, get_settings
from app.llm.cache import cached_chat_json
from app.llm.client import LLMClient, LLMError, LLMUsage, get_llm_client
from app.processing.cleaning import (
    CleanReport,
    clean_reading,
    strip_headers_footers,
)
from app.processing.document import ProcessedDocument, ReadingUnit
from app.schemas.enums import ExtractionRoute, ExtractionStatus, ModelRole
from app.schemas.extraction import (
    ExtractedField,
    ExtractedTable,
    ExtractionResult,
    LanguageSpan,
    PageProvenance,
)


@dataclass
class UsageRecord:
    """One model call, successful or not -- what the cost ledger is built from."""

    role: ModelRole
    model: str
    page: int | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    status: str = "ok"
    error: str | None = None
    # e.g. optional parameters the provider rejected and the client shed.
    notes: str | None = None

    @classmethod
    def of(cls, role, model, page, usage: LLMUsage | None, error: str | None = None):
        usage = usage or LLMUsage()
        shed = getattr(usage, "shed_params", None) or []
        return cls(
            role=role,
            model=model,
            page=page,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            latency_ms=usage.latency_ms,
            status="failed" if error else "ok",
            error=error,
            notes=(
                f"shed optional parameters: {', '.join(shed)}" if shed else None
            ),
        )


@dataclass
class UnitReading:
    """One unit's transcribed text, preserved for chunking.

    This is the document representation: the per-page Markdown the layout /
    vision models produced (or the local text for EASY units). It is persisted
    on `source_extractions.readings` so chunks can be regenerated without
    re-extracting. See docs/chunking.md.
    """

    page: int
    kind: str
    difficulty: str
    route: str
    model: str | None
    status: str
    text: str
    note: str | None = None
    # Optional, added later: the pre-cleaning text and the cleaning report.
    # All default so previously persisted readings stay loadable.
    raw_text: str | None = None
    damage_ratio: float = 0.0
    loop_detected: bool = False
    language: str | None = None
    script: str | None = None
    lang_confidence: float = 0.0


@dataclass
class AgentOutput:
    result: ExtractionResult
    usage: list[UsageRecord] = field(default_factory=list)
    # The merged Markdown the extract model was given, before truncation: the
    # document as read, which the graph and vector stages build on.
    content: str | None = None
    readings: list[UnitReading] = field(default_factory=list)
    # Per-phase wall-clock timings in milliseconds: where the run spent its
    # time, so a slow run is explainable from the stored row alone.
    timings: dict[str, int] = field(default_factory=dict)


class ExtractionFailed(Exception):
    """Nothing usable could be extracted. Carries the calls already made."""

    def __init__(self, message: str, usage: list[UsageRecord] | None = None):
        super().__init__(message)
        self.message = message
        self.usage = usage or []


class ExtractionAgent(Protocol):
    def extract(
        self,
        document: ProcessedDocument,
        *,
        source_id: uuid.UUID,
        instructions: str | None = None,
    ) -> AgentOutput: ...


@dataclass
class _Reading:
    """What reading one unit produced."""

    unit: ReadingUnit
    text: str
    route: ExtractionRoute
    model: str | None
    status: str  # ok | fallback | failed
    note: str | None = None
    usage: list[UsageRecord] = field(default_factory=list)
    raw_text: str | None = None
    report: CleanReport | None = None


def _short(exc: Exception) -> str:
    return (getattr(exc, "message", None) or str(exc))[:500]


def _tesseract_ocr(image_png: bytes, langs: str = "hin+tam+eng") -> str | None:
    """Best-effort OCR via tesseract; None when unavailable or empty."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(image_png))
        return pytesseract.image_to_string(img, lang=langs) or None
    except Exception:
        return None  # tesseract binary missing, language pack absent, ...


def _norm(text: str) -> str:
    """Lowercase, whitespace-collapsed identity for near-duplicate checks."""
    return " ".join((text or "").lower().split())


def _duplicates(local: str, description: str, threshold: float = 0.9) -> bool:
    """True when a vision description adds nothing the local text lacks."""
    if not local or not description:
        return False
    d, l = _norm(description), _norm(local)
    if d in l or l in d:
        return True
    # Cheap containment check: every word of the description already local.
    words = [w for w in d.split() if len(w) > 3]
    if not words:
        return False
    hits = sum(1 for w in words if w in l)
    return hits / len(words) >= threshold


class NemotronExtractionAgent:
    """Routes units to NVIDIA layout / vision models, then extracts once."""

    def __init__(self, client: LLMClient, settings: Settings | None = None):
        self.client = client
        self.settings = settings or get_settings()
        # Two layers: the in-memory LRU in front of the database-backed
        # store, so a validated reading survives restarts and eviction.
        store = (
            DbReadingStore() if self.settings.reading_cache_persistent else None
        )
        self._cache = (
            ReadingCache(self.settings.reading_cache_max_entries, store=store)
            if self.settings.reading_cache_enabled
            else None
        )

    def _cache_key(self, image: bytes, model: str, prompt: str) -> str | None:
        if self._cache is None:
            return None
        return ReadingCache.key(
            image,
            model,
            prompts.PROMPT_VERSION,
            self.settings.extraction_render_long_side,
            prompt,
        )

    # -- reading ---------------------------------------------------------------

    def _page_max_tokens(self, unit: ReadingUnit) -> int:
        """Scale the output budget to the page: local text length is a good
        proxy for how much transcription the page needs."""
        s = self.settings
        # ~4 chars per token, with headroom for Markdown structure.
        scaled = max(1024, len(unit.text or "") * 2)
        return min(scaled, s.vision_max_tokens_cap)

    def _clean(self, unit: ReadingUnit, text: str) -> tuple[str, CleanReport]:
        """Sanitize one model/local reading. Raw text is kept on the reading."""
        s = self.settings
        cleaned, report = clean_reading(
            text,
            loop_compression_min=s.cleaning_loop_compression_min,
            loop_min_chars=s.cleaning_loop_min_chars,
        )
        return cleaned, report

    def _damaged(self, report: CleanReport) -> bool:
        s = self.settings
        return (
            report.damage_ratio > s.cleaning_damage_ratio_threshold
            or report.loop_detected
        )

    def _read_layout(self, unit: ReadingUnit, deadline: float) -> _Reading:
        usage: list[UsageRecord] = []
        s = self.settings
        # Layout model first, then the vision model with a transcription
        # instruction, then whatever local text the page had. Each step is
        # recorded. A reading that fails the cleaning gate (too much junk
        # removed, or a detected loop) is retried once with stricter decoding
        # before falling through.
        for role, model, prompt in (
            (ModelRole.LAYOUT, s.llm_layout_model, prompts.LAYOUT_PROMPT),
            (ModelRole.VISION, s.llm_vision_model, prompts.TRANSCRIPTION_PROMPT),
        ):
            # Reading cache: a page already read well is never re-paid for.
            cache_key = self._cache_key(unit.page_image.data, model, prompt)
            if cache_key and self._cache is not None:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    status = "ok" if role == ModelRole.LAYOUT else "fallback"
                    return _Reading(
                        unit, cached, ExtractionRoute.LAYOUT, model, status,
                        note="served from reading cache", usage=usage,
                    )
            for attempt in range(1, 2 + s.llm_layout_retries + 1):
                # Per-page deadline across the whole fallback chain: each
                # call gets at most the time the page (and the run budget)
                # has left, so one slow page cannot eat the run.
                left = deadline - time.monotonic()
                if left <= 0:
                    usage.append(
                        UsageRecord.of(
                            ModelRole.LAYOUT, model, unit.index, None,
                            "page deadline exhausted before this call",
                        )
                    )
                    break  # fall through to local text
                try:
                    text, u = self.client.read_image(
                        model,
                        unit.page_image.data,
                        unit.page_image.mime,
                        prompt,
                        strict=attempt > 1,
                        max_tokens=self._page_max_tokens(unit),
                        timeout=min(
                            (
                                self.settings.llm_layout_timeout_seconds
                                if role == ModelRole.LAYOUT
                                else self.settings.llm_timeout_seconds
                            ),
                            left,
                        ),
                    )
                except LLMError as exc:
                    usage.append(
                        UsageRecord.of(role, model, unit.index, exc.usage, _short(exc))
                    )
                    if getattr(exc, "category", None) == "timeout":
                        # A timeout is not retried on the same model: paying
                        # for the same slow call twice is how runs stacked to
                        # 10+ minutes. Fall through to the next model now.
                        break
                    if attempt <= s.llm_layout_retries:
                        continue  # one retry before falling through
                    break  # fall through to the next model in the chain
                cleaned, report = self._clean(unit, text)
                usage.append(UsageRecord.of(role, model, unit.index, u))
                if self._damaged(report):
                    if attempt == 1:
                        continue  # retry once with stricter decoding
                    # Still damaged: record it and fall through the chain.
                    usage.append(
                        UsageRecord.of(
                            ModelRole.LAYOUT, model, unit.index, None,
                            f"reading failed cleaning gate (damage "
                            f"{report.damage_ratio:.3f}, loop={report.loop_detected})",
                        )
                    )
                    break
                status = "ok" if role == ModelRole.LAYOUT else "fallback"
                if cache_key and self._cache is not None:
                    self._cache.put(cache_key, cleaned)  # only validated readings
                return _Reading(
                    unit, cleaned, ExtractionRoute.LAYOUT, model, status,
                    raw_text=text, report=report, usage=usage,
                )
        if unit.text.strip():
            cleaned, report = self._clean(unit, unit.text)
            return _Reading(
                unit, cleaned, ExtractionRoute.TEXT, None, "fallback",
                note="layout and vision models failed; used local text",
                raw_text=unit.text, report=report, usage=usage,
            )
        # Optional tesseract OCR fallback (hin/tam/eng). Skipped gracefully
        # when tesseract is not installed or the flag is off.
        if s.tesseract_fallback_enabled and unit.page_image is not None:
            ocr = _tesseract_ocr(unit.page_image.data)
            if ocr:
                cleaned, report = self._clean(unit, ocr)
                if cleaned:
                    return _Reading(
                        unit, cleaned, ExtractionRoute.LAYOUT, "tesseract",
                        "fallback", note="read by tesseract OCR fallback",
                        raw_text=ocr, report=report, usage=usage,
                    )
        return _Reading(
            unit, "", ExtractionRoute.LAYOUT, None, "failed",
            note="page could not be read", usage=usage,
        )

    def _read_vision(self, unit: ReadingUnit, deadline: float) -> _Reading:
        model = self.settings.llm_vision_model
        usage: list[UsageRecord] = []
        described: list[str] = []
        failures = 0
        local = unit.text.strip()
        for image in unit.images:
            left = deadline - time.monotonic()
            if left <= 0:
                failures += 1
                usage.append(
                    UsageRecord.of(
                        ModelRole.VISION, model, unit.index, None,
                        "page deadline exhausted before this call",
                    )
                )
                continue
            try:
                text, u = self.client.read_image(
                    model,
                    image.data,
                    image.mime,
                    prompts.VISION_PROMPT,
                    max_tokens=self._page_max_tokens(unit),
                    timeout=left,
                )
                usage.append(UsageRecord.of(ModelRole.VISION, model, unit.index, u))
                cleaned, _ = self._clean(unit, text)
                # One source per fact: drop a description that just repeats
                # what the local text already says (a caption under a picture).
                if cleaned and not _duplicates(local, cleaned):
                    described.append(f"[Image — {image.label}: {cleaned}]")
            except LLMError as exc:
                failures += 1
                usage.append(
                    UsageRecord.of(ModelRole.VISION, model, unit.index, exc.usage, _short(exc))
                )
        text = "\n\n".join(p for p in [local, *described] if p)
        cleaned, report = self._clean(unit, text)
        if not cleaned:
            return _Reading(unit, "", ExtractionRoute.VISION, model, "failed",
                            note="image could not be read", usage=usage)
        status, note = "ok", None
        if failures:
            status, note = "fallback", f"{failures} image(s) could not be described"
        return _Reading(
            unit, cleaned, ExtractionRoute.VISION, model, status, note,
            raw_text=text, report=report, usage=usage,
        )

    def _read_local(self, unit: ReadingUnit, note: str | None = None) -> _Reading:
        """The local-text reading a unit falls back to when its deadline
        (per-page or the run budget) is exhausted -- never a silent skip."""
        cleaned, report = self._clean(unit, unit.text)
        return _Reading(
            unit, cleaned, ExtractionRoute.TEXT, None, "fallback", note=note,
            raw_text=unit.text or None, report=report,
        )

    def _read(self, unit: ReadingUnit, deadline: float | None = None) -> _Reading:
        if deadline is None:
            deadline = (
                time.monotonic()
                + self.settings.extraction_page_deadline_seconds
            )
        # Deferred rendering (Phase 3): render inside this worker so
        # pypdfium2 work overlaps with other units' model calls.
        if unit.render_fn is not None:
            try:
                unit.render_fn()
            except Exception as exc:  # rendering failure should not kill the run
                return _Reading(
                    unit, unit.text, ExtractionRoute.TEXT, None, "fallback",
                    note=f"page rendering failed: {_short(exc)}",
                )
            unit.render_fn = None
        # Run budget exhausted: remaining units use their local text and the
        # run completes as PARTIAL with a warning (see extract()).
        if time.monotonic() >= deadline:
            return self._read_local(
                unit, note="run budget exhausted; used local text"
            )
        if unit.route == ExtractionRoute.LAYOUT and unit.page_image is not None:
            return self._read_layout(unit, deadline)
        if unit.route == ExtractionRoute.VISION and unit.images:
            return self._read_vision(unit, deadline)
        cleaned, report = self._clean(unit, unit.text)
        return _Reading(
            unit, cleaned, ExtractionRoute.TEXT, None, "ok",
            raw_text=unit.text or None, report=report,
        )

    # -- extracting ------------------------------------------------------------

    def extract(
        self,
        document: ProcessedDocument,
        *,
        source_id: uuid.UUID,
        instructions: str | None = None,
    ) -> AgentOutput:
        timings: dict[str, int] = {}
        agent_started = time.monotonic()
        workers = max(
            1, min(self.settings.extraction_concurrency, self.settings.extraction_concurrency_max)
        )
        read_started = time.monotonic()
        # The run's wall-clock budget: every unit's deadline is the earlier
        # of its own page deadline and this budget.
        run_deadline = agent_started + self.settings.extraction_run_budget_seconds
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                readings = list(
                    pool.map(
                        lambda u: self._read(
                            u,
                            min(
                                time.monotonic()
                                + self.settings.extraction_page_deadline_seconds,
                                run_deadline,
                            ),
                        ),
                        document.units,
                    )
                )
        finally:
            # Deferred renders may hold the pdfium document open; release it.
            if document.finalize is not None:
                document.finalize()
                document.finalize = None
        timings["read_ms"] = int((time.monotonic() - read_started) * 1000)

        usage = [u for r in readings for u in r.usage]
        warnings = list(document.warnings)
        s = self.settings

        # Running headers/footers: strip them from every page, keep one copy
        # as document-level metadata.
        page_units = [r for r in readings if r.unit.kind in ("page", "slide")]
        if len(page_units) >= s.cleaning_header_footer_min_pages:
            stripped, meta = strip_headers_footers(
                [r.text for r in page_units],
                repeat_ratio=s.cleaning_header_footer_repeat_ratio,
                min_pages=s.cleaning_header_footer_min_pages,
            )
            if meta:
                warnings.append(f"Running header/footer removed: {' | '.join(meta.values())}")
                for r, text in zip(page_units, stripped):
                    r.text = text

        sections = []
        for r in readings:
            if r.note:
                warnings.append(f"{r.unit.kind} {r.unit.index}: {r.note}")
            if r.report and r.report.notes:
                warnings.append(
                    f"{r.unit.kind} {r.unit.index}: {'; '.join(r.report.notes)}"
                )
            if r.text.strip():
                marker = f"<!-- {r.unit.kind} {r.unit.index} · {r.unit.difficulty.value} · {r.route.value} -->"
                sections.append(f"{marker}\n{r.text.strip()}")

        if not sections:
            raise ExtractionFailed("Nothing readable was found in the document", usage)

        content = "\n\n".join(sections)

        # Locally detected languages (Phase 4): passed to the prompt so the
        # model transcribes faithfully instead of guessing/translating.
        detected = [
            (r.unit.language, r.unit.index)
            for r in readings
            if r.unit.language
        ]
        lang_order: list[str] = []
        for code, _ in detected:
            if code not in lang_order:
                lang_order.append(code)

        # Phase 6: windowed map-reduce. No silent truncation: every unit is
        # in some window, and coverage is reported.
        s = self.settings
        model = s.llm_extract_model
        unit_sections = [
            (f"{marker}\n{r.text.strip()}", r.unit.index)
            for r, marker in zip(
                readings,
                (
                    f"<!-- {r.unit.kind} {r.unit.index} · {r.unit.difficulty.value} · {r.route.value} -->"
                    for r in readings
                ),
            )
            if r.text.strip()
        ]
        windows = build_windows(
            unit_sections,
            max_chars=s.extraction_window_chars,
            overlap_units=s.extraction_window_overlap_units,
        )
        window_results: list[dict] = []
        window_summaries: list[str] = []
        budget_skipped: list[int] = []

        def run_window(window):
            """One window's extract call, with a repair retry on bad JSON."""
            w_usage: list[UsageRecord] = []
            for attempt in range(1 + s.extraction_window_repair_retries):
                if time.monotonic() >= run_deadline:
                    budget_skipped.append(window.index)
                    warnings.append(
                        f"window {window.index + 1} of {len(windows)} skipped: "
                        "run budget exhausted"
                    )
                    return None, w_usage
                try:
                    raw, u, _ = cached_chat_json(
                        self.client,
                        model,
                        prompts.EXTRACT_SYSTEM,
                        prompts.extract_user_message(
                            window.text, instructions, lang_order or None
                        ),
                    )
                except LLMError as exc:
                    w_usage.append(
                        UsageRecord.of(ModelRole.EXTRACT, model, None, exc.usage, _short(exc))
                    )
                    if attempt < s.extraction_window_repair_retries:
                        continue
                    warnings.append(
                        f"window {window.index + 1} of {len(windows)} failed: {_short(exc)}"
                    )
                    return None, w_usage
                w_usage.append(UsageRecord.of(ModelRole.EXTRACT, model, None, u))
                if isinstance(raw, dict):
                    summary = (raw.get("summary") or "").strip()
                    if summary:
                        window_summaries.append(f"Window {window.index + 1}: {summary}")
                    return raw, w_usage
                if attempt < s.extraction_window_repair_retries:
                    continue
                warnings.append(
                    f"window {window.index + 1} of {len(windows)} returned malformed JSON"
                )
            return None, w_usage

        workers = max(
            1, min(s.extraction_concurrency, s.extraction_concurrency_max)
        )
        windows_started = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            window_outputs = list(pool.map(run_window, windows))
        timings["windows_ms"] = int((time.monotonic() - windows_started) * 1000)
        for _, w_usage in window_outputs:
            usage.extend(w_usage)
        window_results = [raw for raw, _ in window_outputs if raw]

        if not window_results:
            if budget_skipped:
                # Every window was skipped because the run budget ran out:
                # complete as PARTIAL with an empty result and a warning,
                # never a silent failure (the units were read as local text).
                warnings.append(
                    f"{len(budget_skipped)} window(s) skipped: run budget exhausted"
                )
                raw = {
                    "fields": [],
                    "tables": [],
                    "document_type": None,
                    "title": None,
                    "summary": None,
                    "language": None,
                }
            else:
                raise ExtractionFailed(
                    "Extraction model failed for every window", usage
                )

        # Reduce in code: merge tables and fields across windows.
        reduced = reduce_windows(window_results)
        for conflict in reduced.conflicts:
            warnings.append(
                f"field conflict on '{conflict.key}': kept "
                f"'{conflict.kept}' (confidence {conflict.kept_confidence}) "
                f"over '{conflict.dropped}' (confidence {conflict.dropped_confidence})"
            )
        raw = {
            "fields": reduced.fields,
            "tables": reduced.tables,
            # Document-level metadata comes from the first window plus the
            # per-window summaries, in one small call below.
            "document_type": None,
            "title": None,
            "summary": None,
            "language": None,
        }
        try:
            metadata_started = time.monotonic()
            meta_raw, mu, _ = cached_chat_json(
                self.client,
                model,
                prompts.METADATA_SYSTEM,
                prompts.metadata_user_message(
                    windows[0].text if windows else "",
                    window_summaries,
                    lang_order or None,
                ),
            )
            timings["metadata_ms"] = int((time.monotonic() - metadata_started) * 1000)
            usage.append(UsageRecord.of(ModelRole.EXTRACT, model, None, mu))
            raw.update(
                {
                    k: meta_raw.get(k)
                    for k in ("document_type", "title", "summary")
                }
            )
        except LLMError as exc:
            usage.append(
                UsageRecord.of(ModelRole.EXTRACT, model, None, exc.usage, _short(exc))
            )
            warnings.append(f"document metadata call failed: {_short(exc)}")
        coverage = {
            "windows": len(windows),
            "windows_ok": len(window_results),
        }
        warnings.append(
            f"coverage: {len(window_results)}/{len(windows)} windows extracted "
            f"from {len(unit_sections)} readable unit(s)"
        )

        pages = [
            PageProvenance(
                page=r.unit.index,
                kind=r.unit.kind,
                difficulty=r.unit.difficulty,
                route=r.route,
                model=r.model,
                status=r.status,
                note=r.note,
            )
            for r in readings
        ]
        unit_readings = [
            UnitReading(
                page=r.unit.index,
                kind=r.unit.kind,
                difficulty=r.unit.difficulty.value,
                route=r.route.value,
                model=r.model,
                status=r.status,
                text=r.text.strip(),
                note=r.note,
                raw_text=r.raw_text,
                damage_ratio=round(r.report.damage_ratio, 4) if r.report else 0.0,
                loop_detected=bool(r.report and r.report.loop_detected),
                language=r.unit.language,
                script=r.unit.script,
                lang_confidence=r.unit.lang_confidence,
            )
            for r in readings
        ]
        incomplete = (
            document.skipped_units > 0
            or any(r.status == "failed" for r in readings)
            # A unit that fell back to local text because its deadline or
            # the run budget ran out makes the run PARTIAL, not COMPLETED.
            or any("budget" in (r.note or "") for r in readings)
        )
        result = _build_result(raw, source_id=source_id, pages=pages, warnings=warnings)
        _regex_identifiers(readings, result.fields)
        # Language spans from local detection (Phase 4), not the model's guess.
        spans: dict[str, list[int]] = {}
        for code, index in detected:
            spans.setdefault(code, []).append(index)
        result.languages = [
            LanguageSpan(language=code, pages=pages_)
            for code, pages_ in spans.items()
        ]
        # Coverage (Phase 6): never silent about what was read or skipped.
        result.pages_read = len([r for r in readings if r.text.strip()])
        result.pages_skipped = document.skipped_units
        result.units_failed = len([r for r in readings if r.status == "failed"])
        # The dominant language stays in the existing single field.
        if lang_order and not result.language:
            result.language = lang_order[0]
        result.status = ExtractionStatus.PARTIAL if incomplete else ExtractionStatus.COMPLETED
        timings["agent_ms"] = int((time.monotonic() - agent_started) * 1000)
        return AgentOutput(
            result=result, usage=usage, content=content, readings=unit_readings,
            timings=timings,
        )


# -- turning the model's JSON into a validated result -------------------------------


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        import json

        return json.dumps(value, ensure_ascii=False)
    value = str(value).strip()
    return value or None


def _page(value) -> int | None:
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None


def _confidence(value) -> float | None:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return None
    if c > 1:  # a model answering in percent
        c = c / 100
    return min(max(c, 0.0), 1.0)


# Deterministic backstop for identifiers the model may omit. (key, pattern); the
# first group is the number as printed.
_ID_PATTERNS = (
    ("enrolment_number", re.compile(
        r"Enrol{1,2}ment\s*(?:No|Number|ID)\.?\s*[:\-]?\s*\**\s*(\d[\d/ .\-]{6,}\d)",
        re.IGNORECASE)),
    ("vid", re.compile(r"\bVID\s*[:\-]?\s*(\d{4}\s\d{4}\s\d{4}\s\d{4})", re.IGNORECASE)),
    # Not the tail of a 16-digit VID: no digit group before or after it.
    ("aadhaar_number", re.compile(r"(?<![\d/.\-])(?<!\d{4}\s)(\d{4}\s\d{4}\s\d{4})(?![\d/.\-]|\s\d{4})")),
)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _regex_identifiers(readings: list[_Reading], fields: list[ExtractedField]) -> None:
    """Append identifiers found in the readings that the model did not return."""
    known = {_digits(f.value) for f in fields if _digits(f.value)}
    for r in readings:
        for key, pattern in _ID_PATTERNS:
            for match in pattern.finditer(r.text):
                value = match.group(1).strip(" .-/")
                digits = _digits(value)
                if digits in known:
                    continue
                known.add(digits)
                fields.append(
                    ExtractedField(
                        key=key, value=value, page=r.unit.index, confidence=0.9
                    )
                )


def _build_result(raw: dict, *, source_id, pages, warnings) -> ExtractionResult:
    """Validate leniently: keep every well-formed item, drop malformed ones.

    Models occasionally return a number for a string, 95 for 0.95, or a table
    row that is not a list. None of that should fail an otherwise good run.
    """
    fields = []
    for item in raw.get("fields") or []:
        if not isinstance(item, dict):
            continue
        key, value = _text(item.get("key")), _text(item.get("value"))
        if key and value:
            fields.append(
                ExtractedField(
                    key=key.lower().replace(" ", "_"),
                    value=value,
                    page=_page(item.get("page")),
                    confidence=_confidence(item.get("confidence")),
                )
            )
    tables = []
    for item in raw.get("tables") or []:
        if not isinstance(item, dict):
            continue
        rows = [
            [_text(c) or "" for c in row]
            for row in item.get("rows") or []
            if isinstance(row, list)
        ]
        columns = [_text(c) or "" for c in item.get("columns") or []]
        if rows or columns:
            tables.append(
                ExtractedTable(
                    title=_text(item.get("title")),
                    page=_page(item.get("page")),
                    columns=columns,
                    rows=rows,
                )
            )
    return ExtractionResult(
        source_id=source_id,
        document_type=(_text(raw.get("document_type")) or "unknown").lower().replace(" ", "_"),
        title=_text(raw.get("title")),
        language=_text(raw.get("language")),
        summary=_text(raw.get("summary")) or "",
        fields=fields,
        tables=tables,
        pages=pages,
        warnings=warnings,
    )


@lru_cache
def get_extraction_agent() -> ExtractionAgent:
    """FastAPI dependency; tests override it with a fake agent."""
    return NemotronExtractionAgent(get_llm_client(), get_settings())
