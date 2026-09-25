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

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol

from app.agents import prompts
from app.core.config import Settings, get_settings
from app.llm.client import LLMClient, LLMError, LLMUsage, get_llm_client
from app.processing.document import DocumentUnit, ProcessedDocument
from app.schemas.enums import ExtractionRoute, ExtractionStatus, ModelRole
from app.schemas.extraction import (
    ExtractedField,
    ExtractedTable,
    ExtractionResult,
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

    @classmethod
    def of(cls, role, model, page, usage: LLMUsage | None, error: str | None = None):
        usage = usage or LLMUsage()
        return cls(
            role=role,
            model=model,
            page=page,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            latency_ms=usage.latency_ms,
            status="failed" if error else "ok",
            error=error,
        )


@dataclass
class AgentOutput:
    result: ExtractionResult
    usage: list[UsageRecord] = field(default_factory=list)
    # The merged Markdown the extract model was given, before truncation: the
    # document as read, which the graph and vector stages build on.
    content: str | None = None


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

    unit: DocumentUnit
    text: str
    route: ExtractionRoute
    model: str | None
    status: str  # ok | fallback | failed
    note: str | None = None
    usage: list[UsageRecord] = field(default_factory=list)


def _short(exc: Exception) -> str:
    return (getattr(exc, "message", None) or str(exc))[:500]


class NemotronExtractionAgent:
    """Routes units to NVIDIA layout / vision models, then extracts once."""

    def __init__(self, client: LLMClient, settings: Settings | None = None):
        self.client = client
        self.settings = settings or get_settings()

    # -- reading ---------------------------------------------------------------

    def _read_layout(self, unit: DocumentUnit) -> _Reading:
        usage: list[UsageRecord] = []
        s = self.settings
        # Layout model first, then the vision model with the same instruction,
        # then whatever local text the page had. Each step is recorded.
        for role, model in (
            (ModelRole.LAYOUT, s.llm_layout_model),
            (ModelRole.VISION, s.llm_vision_model),
        ):
            try:
                text, u = self.client.read_image(
                    model, unit.page_image.data, unit.page_image.mime, prompts.LAYOUT_PROMPT
                )
                usage.append(UsageRecord.of(role, model, unit.index, u))
                status = "ok" if role == ModelRole.LAYOUT else "fallback"
                return _Reading(unit, text, ExtractionRoute.LAYOUT, model, status, usage=usage)
            except LLMError as exc:
                usage.append(UsageRecord.of(role, model, unit.index, exc.usage, _short(exc)))
        if unit.text.strip():
            return _Reading(
                unit, unit.text, ExtractionRoute.TEXT, None, "fallback",
                note="layout and vision models failed; used local text", usage=usage,
            )
        return _Reading(
            unit, "", ExtractionRoute.LAYOUT, None, "failed",
            note="page could not be read", usage=usage,
        )

    def _read_vision(self, unit: DocumentUnit) -> _Reading:
        model = self.settings.llm_vision_model
        usage: list[UsageRecord] = []
        described: list[str] = []
        failures = 0
        for image in unit.images:
            try:
                text, u = self.client.read_image(
                    model, image.data, image.mime, prompts.VISION_PROMPT
                )
                usage.append(UsageRecord.of(ModelRole.VISION, model, unit.index, u))
                described.append(f"[Image — {image.label}: {text}]")
            except LLMError as exc:
                failures += 1
                usage.append(
                    UsageRecord.of(ModelRole.VISION, model, unit.index, exc.usage, _short(exc))
                )
        text = "\n\n".join(p for p in [unit.text.strip(), *described] if p)
        if not text:
            return _Reading(unit, "", ExtractionRoute.VISION, model, "failed",
                            note="image could not be read", usage=usage)
        status, note = "ok", None
        if failures:
            status, note = "fallback", f"{failures} image(s) could not be described"
        return _Reading(unit, text, ExtractionRoute.VISION, model, status, note, usage)

    def _read(self, unit: DocumentUnit) -> _Reading:
        if unit.route == ExtractionRoute.LAYOUT and unit.page_image is not None:
            return self._read_layout(unit)
        if unit.route == ExtractionRoute.VISION and unit.images:
            return self._read_vision(unit)
        return _Reading(unit, unit.text, ExtractionRoute.TEXT, None, "ok")

    # -- extracting ------------------------------------------------------------

    def extract(
        self,
        document: ProcessedDocument,
        *,
        source_id: uuid.UUID,
        instructions: str | None = None,
    ) -> AgentOutput:
        workers = max(1, self.settings.extraction_concurrency)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            readings = list(pool.map(self._read, document.units))

        usage = [u for r in readings for u in r.usage]
        warnings = list(document.warnings)
        sections = []
        for r in readings:
            if r.note:
                warnings.append(f"{r.unit.kind} {r.unit.index}: {r.note}")
            if r.text.strip():
                marker = f"<!-- {r.unit.kind} {r.unit.index} · {r.unit.difficulty.value} · {r.route.value} -->"
                sections.append(f"{marker}\n{r.text.strip()}")

        if not sections:
            raise ExtractionFailed("Nothing readable was found in the document", usage)

        content = "\n\n".join(sections)
        merged = content
        limit = self.settings.extraction_max_input_chars
        if len(merged) > limit:
            merged = merged[:limit]
            warnings.append(
                f"Document text truncated to {limit} characters before extraction"
            )

        model = self.settings.llm_extract_model
        try:
            raw, u = self.client.chat_json(
                model,
                prompts.EXTRACT_SYSTEM,
                prompts.extract_user_message(merged, instructions),
            )
        except LLMError as exc:
            usage.append(UsageRecord.of(ModelRole.EXTRACT, model, None, exc.usage, _short(exc)))
            raise ExtractionFailed(f"Extraction model failed: {_short(exc)}", usage) from exc
        usage.append(UsageRecord.of(ModelRole.EXTRACT, model, None, u))

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
        incomplete = document.skipped_units > 0 or any(r.status == "failed" for r in readings)
        result = _build_result(raw, source_id=source_id, pages=pages, warnings=warnings)
        result.status = ExtractionStatus.PARTIAL if incomplete else ExtractionStatus.COMPLETED
        return AgentOutput(result=result, usage=usage, content=content)


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
