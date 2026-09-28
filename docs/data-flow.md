# Data flow — what each table stores, from upload to chunks

A walk through the database in the order rows are actually created. For the
chunking design see [chunking.md](chunking.md); for the extraction stage see
[extraction-agent.md](extraction-agent.md).

---

## 1. Setup tables (exist before any upload)

The ownership chain. Nothing can be uploaded until it has a place to live.

```text
tenants → applications → subjects → (sources)
```

| Table | What it stores |
|---|---|
| `tenants` | The organization. Top of the ownership chain. |
| `applications` | A project inside a tenant. API credentials are scoped to one. |
| `subjects` | A folder / topic inside an application. Nestable (parent_id). |
| `users` | Console login accounts (email, password hash, avatar). |
| `authenticated_user` | Console auth sessions / refresh tokens. |
| `api_credentials` | API keys (`memora_…`) a consuming application authenticates with. |
| `actors` | The consuming application's own end users, when it identifies them. Audit only — never ownership. |
| `alembic_version` | Which migration the schema is on. Currently `b2c3d4e5f6a7`. |

---

## 2. Upload

```text
You upload a PDF  →  sources
```

**`sources`** — the document itself. One row per file.

| Column | Meaning |
|---|---|
| `filename`, `mime_type`, `size_bytes` | What was uploaded |
| `storage_uri` | Where the bytes live on disk (`backend/var/storage/{tenant}/{app}/…`) |
| `type` | file \| url \| chat |
| `status` | pending → processing → completed |
| `tenant_id`, `application_id`, `subject_id` | Denormalized ownership |
| `created_by_actor_id` | Audit: who registered it |

The file's bytes are **not** in the database — only the pointer. `Source` *is*
the document; there is no separate `documents` table.

---

## 3. Extraction

```text
You click Extract  →  source_extractions  (+ extraction_usage per LLM call)
```

**`source_extractions`** — one row per extraction run. Versioned and
append-only: re-extracting adds v2 next to v1, it never overwrites.

| Column | Meaning |
|---|---|
| `version` | 1, 2, 3 … |
| `status` | processing \| completed \| partial \| failed |
| `mode`, `instructions` | How the run was configured |
| `provider`, `models` | Which provider and role→model map was used |
| `result` | JSONB — the extracted fields / tables / summary (a distillation) |
| `readings` | JSONB — one entry per page: `page`, `kind`, `route`, `model`, `status`, `text`. **The raw Markdown transcription chunking reads.** |
| `prompt_tokens`, `completion_tokens`, `cost_usd` | Run totals |
| `error` | Why it failed, if it did |
| `finished_at` | When it ended |

`result` is what an API consumer asks for. `readings` is what the chunker
consumes — keeping it means chunks can be regenerated without re-extracting
(and without spending credits).

**`extraction_usage`** — the cost ledger, written alongside. One row per model
call, including failed calls (a failed call can still be billed).

| Column | Meaning |
|---|---|
| `model`, `role`, `provider` | Which model, in which role |
| `page` | Which page the call was for; NULL for the final extract call |
| `prompt_tokens`, `completion_tokens`, `cost_usd`, `latency_ms` | The bill |
| `status`, `error` | ok / failed |

---

## 4. Chunking

Runs automatically after a successful extraction (`chunk_on_extract=true`),
or on demand via `POST /sources/{id}/rechunk`. Pure and local — no LLM, no cost.

```text
readings → document_units → semantic_blocks → retrieval_chunks
```

**`document_units`** — the document parsed into fine-grained elements.

| Column | Meaning |
|---|---|
| `position` | Order within the extraction |
| `kind` | heading \| text \| list \| table \| figure \| key_value |
| `content` | The element's text (Markdown for tables, `[Figure: …]` for figures) |
| `page` | Which page it came from |
| `heading_level` | 1–6 for headings, NULL otherwise |
| `section_path` | JSONB, e.g. `["Test Suite Reference", "01 — Security Headers"]` |

**`semantic_blocks`** — units grouped into logical sections. One per leaf
section. Consecutive heading-only blocks (a table of contents) are merged into
one so the TOC doesn't become a pile of 3-token chunks.

| Column | Meaning |
|---|---|
| `position`, `section_path`, `title` | Where the section sits in the document |
| `unit_ids` | JSONB array of the document_units it contains |
| `page_start`, `page_end` | Provenance range — a section spanning pages stays one block |
| `content` | The block's full text (so parent-context expansion later needs no re-assembly) |

**`retrieval_chunks`** — the final retrieval-sized pieces. This is the unit that
will be embedded and searched in the next phase.

| Column | Meaning |
|---|---|
| `chunk_index` | Order across the whole extraction (0, 1, 2 …) |
| `content` | The chunk's raw text |
| `embedding_text` | `"Section: A > B\n\n" + content` — what actually gets embedded |
| `token_count` | Counted on `embedding_text` (~4 chars/token approximation) |
| `content_type` | text \| table \| figure \| key_value \| mixed |
| `page_start`, `page_end`, `document_unit_ids`, `section_path` | Provenance |
| `semantic_block_id`, `extraction_id`, `source_version` | Parent links |
| `is_active` | Only the newest extraction version's chunks are true |

Targets: 300–500 tokens, 600 soft max, 1200 hard max. A coherent section
smaller than 300 tokens **stays small** — no padding (strategy RULE 6).

---

## 5. Cross-cutting conventions

- **Denormalized scope.** Every table from `sources` down carries
  `tenant_id`, `application_id`, `subject_id`, `source_id`. Every read is one
  indexed predicate instead of a multi-table join, and the later vector search
  needs those predicates inline.
- **Cascade.** Deleting a source removes its extractions, usage rows, units,
  blocks and chunks with it.
- **Versioning.** Units, blocks and chunks hang off `extraction_id`, so
  versioning comes for free. Chunking vN flips `is_active` on in one
  transaction and off for every earlier version.
- **Not yet stored.** No embedding vectors, no HNSW or tsvector indexes —
  those arrive with the retrieval phase.

---

## 6. Inspecting it

```bash
# Full quality report for the latest extraction (read-only)
cd backend && .venv/bin/python scripts/verify_chunking.py [source_id]
```

| To see… | Query |
|---|---|
| Uploaded files | `SELECT filename, status FROM sources` |
| Extraction runs | `SELECT version, status, cost_usd FROM source_extractions` |
| What the model extracted | `SELECT result FROM source_extractions WHERE version = 1` |
| Per-page transcription | `SELECT readings FROM source_extractions WHERE version = 1` |
| Document structure | `SELECT kind, content, page FROM document_units ORDER BY position` |
| Section groupings | `SELECT title, section_path, page_start, page_end FROM semantic_blocks ORDER BY position` |
| Final chunks | `SELECT chunk_index, token_count, content_type FROM retrieval_chunks WHERE is_active ORDER BY chunk_index` |
| Model costs | `SELECT model, page, cost_usd FROM extraction_usage` |
