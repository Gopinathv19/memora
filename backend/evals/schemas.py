"""The evaluation-example schema and its validation.

One JSONL line = one eval example. The minimum contract (docs in
``evals/README.md``):

    {
      "query": "What is the contract renewal date?",
      "tenant_id": "tenant-id",
      "expected_answer": "The contract renews on 15 March 2026.",
      "supporting_chunk_ids": ["chunk-id-1"],
      "supporting_pages": [3, 4],
      "query_type": "factoid",
      "difficulty": "easy"
    }

Rules:

* ``query`` and ``tenant_id`` are required and non-empty.
* ``expected_answer`` may be absent or empty ONLY for unanswerable
  examples, which must say so explicitly via ``is_unanswerable: true``
  (or the legacy ``expected_behavior: "insufficient"``).
* ``supporting_chunk_ids`` / ``supporting_pages`` support empty lists
  (unanswerable examples have none).
* ``query_type`` and ``difficulty`` are validated against the allowed
  values below.
* Optional metadata: ``source_ids``, ``application_id``, ``subject_id``,
  ``expected_citations``, ``as_of_date``, ``notes``.

Nothing is ever invented: a record that fails validation is reported with
its JSONL line number, never silently repaired.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# --- Allowed values (documented; extend deliberately) ------------------------

QUERY_TYPES = (
    "factoid",            # a single fact stated in one chunk
    "exact_lookup",       # an identifier, code, name, reference number
    "table",              # a value inside a table row/cell
    "comparison",         # compares two or more entities/values
    "multi_document",     # needs facts from more than one source
    "multi_hop",          # needs chaining two facts (entity A -> B -> C)
    "unanswerable",       # deliberately not answerable from the corpus
    "conversational",     # a follow-up that depends on prior turns
    "image",              # answer lives in an image/chart/scanned page
    "temporal",           # answer depends on a date / document version
)

DIFFICULTIES = ("easy", "medium", "hard")

QueryType = Literal[
    "factoid", "exact_lookup", "table", "comparison", "multi_document",
    "multi_hop", "unanswerable", "conversational", "image", "temporal",
]
Difficulty = Literal["easy", "medium", "hard"]


# --- The schema -----------------------------------------------------------------


class EvalExample(BaseModel):
    """One evaluation example: a query plus its verified ground truth."""

    model_config = ConfigDict(extra="ignore")

    query: str
    tenant_id: str
    expected_answer: str | None = None
    # Explicit unanswerable flag. Legacy datasets use
    # expected_behavior == "insufficient" instead; from_legacy() maps it.
    is_unanswerable: bool = False
    supporting_chunk_ids: list[str] = []
    supporting_pages: list[int] = []
    query_type: QueryType
    difficulty: Difficulty
    # Optional metadata.
    source_ids: list[str] = []
    application_id: str | None = None
    subject_id: str | None = None
    expected_citations: list[str] = []
    as_of_date: str | None = None
    notes: str = ""
    # Provenance of the record itself (not part of the minimum contract).
    id: str | None = None
    review_status: str | None = None

    @field_validator("query")
    @classmethod
    def _query_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must be a non-empty string")
        return v.strip()

    @field_validator("tenant_id")
    @classmethod
    def _tenant_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("tenant_id must be a non-empty string")
        return v.strip()

    @model_validator(mode="after")
    def _cross_field(self) -> "EvalExample":
        if self.is_unanswerable:
            if self.expected_answer:
                raise ValueError(
                    "is_unanswerable=true but expected_answer is set; "
                    "unanswerable examples must have no expected answer"
                )
            if self.supporting_chunk_ids:
                raise ValueError(
                    "is_unanswerable=true but supporting_chunk_ids is set; "
                    "unanswerable examples must have no supporting chunks"
                )
        else:
            if not (self.expected_answer or "").strip():
                raise ValueError(
                    "expected_answer is required for answerable examples "
                    "(or set is_unanswerable=true)"
                )
        return self


# --- Loading / validation ------------------------------------------------------


class RecordError(Exception):
    """One invalid JSONL record. Carries the line number and the reason."""

    def __init__(self, line_no: int, reason: str):
        super().__init__(f"line {line_no}: {reason}")
        self.line_no = line_no
        self.reason = reason


def validate_record(raw: dict, line_no: int = 0) -> EvalExample:
    """Validate one raw record. Raises RecordError with the line number."""
    try:
        return EvalExample(**raw)
    except Exception as exc:  # pydantic ValidationError and friends
        # Collapse pydantic's error list into one readable reason.
        details = getattr(exc, "errors", None)
        if callable(details):  # pydantic v2: errors() is a method
            try:
                details = details()
            except Exception:
                details = None
        if details:
            parts = []
            for err in details:
                loc = ".".join(str(x) for x in err.get("loc", ()))
                parts.append(f"{loc or 'record'}: {err.get('msg', exc)}")
            reason = "; ".join(parts)
        else:
            reason = str(exc)
        raise RecordError(line_no, reason) from exc


def from_legacy(raw: dict) -> dict:
    """Map the legacy evals/data schema onto the minimum contract.

    Legacy fields (generate_eval_set.py output):
        question -> query
        type     -> query_type (semantic -> factoid, multi_part -> multi_document)
        expected_chunks[].chunk_id -> supporting_chunk_ids
        expected_chunks[].page_start/page_end -> supporting_pages
        expected_behavior "insufficient" -> is_unanswerable=true
    """
    out = dict(raw)
    if "question" in out:
        out["query"] = out.pop("question")
    if "reference_answer" in out:
        out["expected_answer"] = out.pop("reference_answer") or None
    if "type" in out:
        t = out.pop("type")
        mapping = {
            "semantic": "factoid",
            "exact_lookup": "exact_lookup",
            "table": "table",
            "multi_part": "multi_document",
            "unanswerable": "unanswerable",
        }
        out["query_type"] = mapping.get(t, t)
    if "expected_behavior" in out:
        out["is_unanswerable"] = out.pop("expected_behavior") == "insufficient"
    if "expected_chunks" in out:
        chunks = out.pop("expected_chunks") or []
        out["supporting_chunk_ids"] = [c["chunk_id"] for c in chunks if c.get("chunk_id")]
        pages: list[int] = []
        for c in chunks:
            for p in (c.get("page_start"), c.get("page_end")):
                if p is not None and p not in pages:
                    pages.append(p)
        out["supporting_pages"] = sorted(pages)
        out["source_ids"] = sorted({c["source_id"] for c in chunks if c.get("source_id")})
    # Legacy unanswerable rows carry an empty reference_answer, and their
    # expected_chunks are provenance (the chunk the question was generated
    # from), not relevance ground truth: clear them so the record passes
    # the unanswerable rules.
    if out.get("is_unanswerable"):
        out["expected_answer"] = None
        out["supporting_chunk_ids"] = []
        out["supporting_pages"] = []
    # Legacy rows have no difficulty; default rather than reject.
    out.setdefault("difficulty", "medium")
    return out
