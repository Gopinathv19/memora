# Extraction Agent — design

Status: **built** (2026-09-24). This document is both the design and the
description of what shipped. This is the first
processing stage below `Source`. For what exists today see
[current-state.md](current-state.md); for earlier foundation decisions see
[decisions.md](decisions.md).

---

## 1. Goal and scope

Turn an uploaded document into structured, traceable information:

> **Document → Extracted Information**

```text
Source(status=pending)
        │
        ▼
Document Processor      local parsing + per-page triage (no LLM cost)
        │
        ▼
Extraction Agent        NVIDIA models: layout / vision / extract
        │
        ▼
source_extractions      versioned result (v1, v2, …) + usage/cost ledger
```

The agent runs **inside the Memora backend**. There is no separate service, no
separate deployment and no queue.

**In scope**
- Parsing PDF (the main case, including embedded tables and images), images,
  DOCX, XLSX, PPTX and plain text.
- Routing each part to the right NVIDIA model.
- A structured result that points back to its `source_id` and page.
- Versioned re-runs.
- Cost tracking.

**Out of scope for this stage:** graph entities and relationships, chunking,
embeddings, vector DB, RAG, query/search API, Memory Agent.

**Unchanged**
- The upload endpoint's default behavior.
- The ownership and security model (`Scope`, the 404-not-403 rule).
- The original file, which remains the source of truth.

---

## 2. Model providers — NVIDIA models only

Memora is being built for the **Nebius × NVIDIA Global AI Hackathon**. That
means NVIDIA open models only, running on Nebius Token Factory. Nebius
credits are limited (about $25), so development and testing use
build.nvidia.com.

| `LLM_PROVIDER` | Endpoint (OpenAI-compatible) | Key | Used for |
|---|---|---|---|
| `build-nvidia` (default) | `https://integrate.api.nvidia.com/v1` | `NVIDIA_API_KEY` | Development and testing (free) |
| `nebius` | `https://api.tokenfactory.nebius.com/v1/` | `NEBIUS_API_KEY` | Demo and submission |

- Both endpoints speak the OpenAI API, so there is **one client**: the `openai`
  SDK with a configurable `base_url`. Switching provider changes only `.env`.
- **NVIDIA-only guard:** every configured model id must start with `nvidia/`.
  Settings validation fails at startup otherwise.
- Only `app/llm/` imports the SDK. The rest of Memora depends on the
  `ExtractionAgent` interface.
- Automated tests use fakes and never call a provider, so they spend no credits.

### Model roles

| Role | Job | build-nvidia | nebius |
|---|---|---|---|
| `layout` | Page image → Markdown, including tables | `nvidia/nemotron-parse` | the NVIDIA vision model, with a "transcribe as Markdown, tables as Markdown tables" prompt |
| `vision` | Picture → description plus any text in it | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | same, or the id Nebius lists |
| `extract` | Merged document → structured JSON | `nvidia/nemotron-3-super-120b-a12b` | same |

Each role has its own setting (`LLM_LAYOUT_MODEL`, `LLM_VISION_MODEL`,
`LLM_EXTRACT_MODEL`). Exact model ids can differ between the two platforms, so
check them against each provider's `GET /v1/models` before a live run.
Nemotron-Parse is confirmed on build.nvidia.com only, which is why `layout`
falls back to the VL model on Nebius.

---

## 3. Routing — per-page triage

Local parsers read plain digital text well. They read **tables, images and
scans poorly**. So local parsing is used mainly to *decide* which pages are
hard, and every hard page goes to an NVIDIA model that can read it.

### How a PDF page is labelled

Every page gets four free measurements from `pdfplumber`:

| Signal | How it is measured | What it tells us |
|---|---|---|
| Text amount | `len(page.extract_text())` | Whether there is a real text layer (a scan has almost none) |
| Image coverage | Sum of `page.images` bounding boxes ÷ page area | How much of the page is pictures |
| Table signal | `page.find_tables()` count, or many ruled lines in `page.lines` / `page.rects` | Whether there is probably a table or grid |
| Text quality | Share of junk or unreadable characters | Whether a broken font or encoding makes the text unreliable |

The parser only has to *notice* a table or image, which it does reliably. It
doesn't have to *read* one. When a check is borderline, the page moves up a
level. A wasted model call costs little; a missed hard page loses data.

```text
                 page measurements
                        │
   ┌────────────────────┼─────────────────────┐
   ▼                    ▼                     ▼
 EASY                 MEDIUM                 HARD
 plenty of clean      good text + 1-2        scan / garbled text, OR
 text, no table,      small images           a table, OR
 images < 15%         (logo, photo)          images/charts > 15%
   │                    │                     │
   ▼                    ▼                     ▼
 local text          local text             render the whole page
 (free)              + images → vision      → layout model
```

Thresholds (`TRIAGE_MIN_TEXT_CHARS=200`, `TRIAGE_IMAGE_RATIO=0.15`) are
settings. They will be tuned on real documents.

### Other file types

| Type | Parser | Route |
|---|---|---|
| DOCX | `python-docx` | Paragraphs and tables → `text` (the XML holds exact cells); embedded pictures → `vision` |
| PPTX | `python-pptx`, one unit per slide | Text and tables → `text`; pictures → `vision` |
| XLSX | `openpyxl`, one unit per sheet | Cells → Markdown tables → `text` (no model) |
| PNG / JPG / … | none | `vision` |
| TXT / MD / CSV / JSON | decode | `text` |
| Anything else | none | The run fails with "unsupported document type" |

### Merge and extract

All unit outputs are joined **in reading order** into one Markdown document.
Each unit carries a provenance marker such as
`<!-- page 3 · hard · layout · nvidia/… -->`. That document goes **once** to
the `extract` model, which returns the structured result.

*Example:* a 10-page PDF with 6 text pages, 1 page with a logo, and 3 pages
with tables or scans needs 1 vision call, 3 layout calls and 1 extract call.
That's 5 model calls instead of 11.

### Guardrails

- Pages are rendered with `pypdfium2` at the model's resolution
  (≥ 1024×1280 for Nemotron-Parse, about 150–200 DPI).
- There is a cap of `EXTRACTION_MAX_PAGES` (default 30) per document. Pages
  beyond it are recorded as skipped.
- Calls use `temperature=0` and JSON-schema output. The result is validated
  against the schema, with one repair retry.
- At most `EXTRACTION_CONCURRENCY` (default 4) calls run in parallel. 429s and
  5xx errors are retried with backoff, and every call has a timeout.
- Fallback chain for a unit: `layout` → `vision` → local text. The run is only
  `failed` if nothing could be read. If some units failed, it is `partial`.
- The route, model, tokens and cost are recorded for every unit.

---

## 4. Output schema — one generic shape

Every document uses one schema. The model chooses the field keys, and there
is no type-specific schema to maintain. It contains no graph entities and no
relationships.

```json
{
  "source_id": "…uuid…",
  "document_type": "aadhaar_card",
  "title": "Aadhaar — Arun Kumar",
  "language": "en",
  "summary": "Aadhaar card of Arun Kumar, resident of Chennai.",
  "fields": [
    {"key": "name",    "value": "Arun Kumar",  "page": 1, "confidence": 0.98},
    {"key": "dob",     "value": "1995-01-01",  "page": 1, "confidence": 0.95},
    {"key": "address", "value": "Chennai …",   "page": 1, "confidence": 0.90}
  ],
  "tables": [
    {"title": "Line items", "page": 2,
     "columns": ["Item", "Qty", "Price"], "rows": [["Widget", "4", "3100"]]}
  ],
  "pages": [
    {"page": 1, "difficulty": "easy", "route": "text",   "model": null},
    {"page": 2, "difficulty": "hard", "route": "layout", "model": "nvidia/…"}
  ],
  "status": "completed",
  "warnings": []
}
```

`page` and `confidence` on every value let you trace it back to the original
file. `status` is `completed` or `partial`.

---

## 5. Triggering, versions and re-extraction

Extraction is **explicit**. It never runs unless someone asks for it, which
protects the credit budget.

| Caller | How |
|---|---|
| API, at upload | `POST /subjects/{id}/sources/upload` with optional form fields `extract=true`, `extract_mode`, `extract_instructions`. Without `extract`, the upload behaves exactly as today. |
| API, later | `POST /sources/{id}/extractions` with `{ "mode": "standard" \| "deep", "instructions": "…" }` → **202** |
| Console | An **Extract** button on the source page and on file rows in the FileExplorer. It becomes **Re-extract** once a version exists. |

- **Versions:** every run is a new row: v1, v2, …, unique per
  `(source_id, version)`. Old versions are never overwritten. The same call
  covers a first run, a retry after `failed`, and a re-run when information
  was missed.
- **Deep mode:** `mode=deep` treats every page as HARD, so every page goes to
  the `layout` model. It is more accurate and costs more credits.
- **Instructions:** optional, up to 2,000 characters, for example "capture the
  premium table on page 3". They are passed to the `extract` prompt as guidance,
  treated as data, and can't change the output schema. Mode and instructions
  are stored on the version, so the reason v2 differs from v1 is visible.
- **One run at a time:** starting a run while one is `processing` returns **409**.
- **Source status** follows the latest run: `processing` → `completed` | `failed`.
- **Execution:** the run is a FastAPI `BackgroundTask` in the same process,
  with its own DB session. Any exception leaves the run and the source
  `failed` with an error, never stuck in `processing`.
- A source without stored bytes (a URL or chat) fails with "no stored content".

---

## 6. Cost tracking

Every model call is recorded and attributed to whoever triggered the run.

- **`extraction_usage`**, one row per model call:
  - `extraction_id`, `source_id`, `tenant_id`, `application_id`
  - `triggered_by_kind` (`user` | `credential`), `triggered_by_user_id`,
    `triggered_by_credential_id`, and optional `actor_id` (the client
    application's own end user)
  - `provider`, `model`, `role` (`layout` | `vision` | `extract`), `page`
  - `prompt_tokens`, `completion_tokens`, `cost_usd`, `latency_ms`, `status`
- **Totals** (tokens and `cost_usd`) are also stored on each extraction version.
- **Prices** come from the `LLM_PRICES` setting (JSON: model → USD per 1M
  input and output tokens). build.nvidia.com is $0; Nebius prices come from
  its pricing page.
- **Attribution:** `Scope` gains an optional `credential_id`, set by
  `get_scope` for credential callers. This is purely additive and doesn't
  change any access rule.
- **Report:** `GET /usage/extractions?tenant_id=&application_id=&from=&to=`
  returns scope-checked totals, grouped by application, model and trigger.

---

## 7. API surface (new)

| Method and path | Purpose |
|---|---|
| `POST /subjects/{id}/sources/upload` (+ `extract`, `extract_mode`, `extract_instructions`) | Upload, optionally starting v1 |
| `POST /sources/{id}/extractions` | Start a new version (first run, retry or re-extract) |
| `GET /sources/{id}/extractions` | List versions and their status |
| `GET /sources/{id}/extractions/latest` | The newest version |
| `GET /sources/{id}/extractions/{version}` | One specific version |
| `GET /usage/extractions` | Usage and cost totals |

All routes are scope-checked through `source_service.get_source`, so a source
outside the caller's scope returns 404.

## 8. Console

- **ExtractionPanel** on the source page:
  - an Extract / Re-extract button, with a Standard/Deep choice and an
    instructions box
  - a status badge, polling about every 3 s while the run is processing
  - a version picker (v1, v2, …)
  - the result: summary, a fields table with page and confidence, the
    extracted tables, and the route, model and cost of each page
- **FileExplorer:** an Extract action and a status badge on each file.
- **Application page:** a usage and cost card.

---

## 9. Data model (new tables)

```text
sources (unchanged columns)
   │ 1
   │
   │ n
source_extractions                          extraction_usage
┌──────────────────────────────┐            ┌──────────────────────────────┐
│ id                           │ 1        n │ id                           │
│ source_id  FK cascade        │◄───────────┤ extraction_id  FK cascade    │
│ tenant_id, application_id    │            │ source_id, tenant_id,        │
│ version    (unique/source)   │            │   application_id             │
│ status                       │            │ triggered_by_kind / user_id /│
│ mode, instructions           │            │   credential_id, actor_id    │
│ result     JSONB             │            │ provider, model, role, page  │
│ provider, models JSONB       │            │ prompt/completion tokens     │
│ prompt/completion tokens,    │            │ cost_usd, latency_ms, status │
│   cost_usd (totals)          │            │ created_at                   │
│ triggered_by_* , actor_id    │            └──────────────────────────────┘
│ error                        │
│ created_at, finished_at      │
└──────────────────────────────┘
```

`tenant_id` and `application_id` are copied from the source, following the
same denormalization pattern `sources` uses, so every read is a single indexed
scope predicate. Deleting a source (or its subject) cascades to its
extractions and their usage rows.

---

## 10. Code layout

```text
backend/app/
├── llm/client.py                  NvidiaLLMClient (openai SDK + base_url); the only SDK import
├── processing/
│   ├── document.py                DocumentUnit, ProcessedDocument
│   ├── triage.py                  page signals → EASY / MEDIUM / HARD
│   ├── parsers.py                 pdf, docx, pptx, xlsx, image, text
│   └── processor.py               DocumentProcessor.process(...)   (no LLM calls)
├── agents/
│   ├── extraction_agent.py        ExtractionAgent protocol + NemotronExtractionAgent
│   └── prompts.py
├── services/extraction_service.py start / run / list / get / usage_report
├── db/models/extraction.py        SourceExtraction, ExtractionUsage
├── schemas/extraction.py          ExtractionResult, ExtractionRequest, …
└── api/routes/extractions.py, usage.py
```

The **interface** the rest of Memora depends on:

```python
class ExtractionAgent(Protocol):
    def extract(
        self,
        document: ProcessedDocument,
        *,
        source_id: uuid.UUID,
        instructions: str | None = None,
    ) -> AgentOutput:  # ExtractionResult + list[UsageRecord]
        ...
```

## 11. Libraries and licenses

The repository is Apache 2.0, and the hackathon requires an open-source license.

| Library | License | Used for |
|---|---|---|
| `openai` | Apache 2.0 | Client for both OpenAI-compatible endpoints |
| `pdfplumber` | MIT | PDF text and triage signals (tables, images, lines) |
| `pypdfium2` | Apache 2.0 / BSD | Rendering PDF pages to images |
| `python-docx`, `python-pptx` | MIT | DOCX and PPTX |
| `openpyxl` | MIT | XLSX |
| `Pillow` | MIT-CMU | Image handling |

**PyMuPDF is not used.** It is AGPL-3.0, which conflicts with shipping under Apache 2.0.

---

## 12. Build order

| Step | Work |
|---|---|
| 0 | This document |
| 1 | Configuration: provider, models with the `nvidia/` guard, limits, prices, thresholds, `.env.example` |
| 2 | Models `SourceExtraction` and `ExtractionUsage`, plus the Alembic migration |
| 3 | Schemas and enums |
| 4 | LLM client (`app/llm/client.py`) |
| 5 | Document Processor (`app/processing/`) |
| 6 | Extraction Agent (`app/agents/`) |
| 7 | Extraction service |
| 8 | Routes (upload flags, extractions, usage), `Scope.credential_id` |
| 9 | Tests (processor, agent, API) using fakes, with no network calls |
| 10 | Console: ExtractionPanel, FileExplorer action, usage card |
| 11 | Docs: current-state, README, public API reference page |

## 13. Verification

1. **Tests:** `pytest` in `backend/`. The existing and new suites pass, and no
   network calls are made.
2. **Migration:** `alembic upgrade head`, then `downgrade -1`, then
   `upgrade head` again, on a scratch database.
3. **Live run on build-nvidia (free):** upload a text PDF, a PDF with tables
   and images, a scanned page, a DOCX, a PPTX, an XLSX and a PNG, each with
   `extract=true`. Check the routes per page, the fields, the tables and the
   usage rows.
4. **Re-extract:** run one file with `mode=deep` and instructions. v2 exists,
   v1 is unchanged, and v2 records its mode and instructions.
5. **Console:** upload → Extract → watch the status → view the result →
   Re-extract → switch versions.
6. **Nebius check:** switch to `LLM_PROVIDER=nebius`, check the model ids via
   `GET /v1/models`, then run **one** document to confirm the models work and
   the cost is recorded.
