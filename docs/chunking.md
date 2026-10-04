# Chunking — design

Status: **in progress** (2026-09-25). This is the second processing stage below
`Source`, built on top of the Extraction Agent. For what exists today see
[current-state.md](current-state.md); for the extraction stage see
[extraction-agent.md](extraction-agent.md).

This stage implements the Memora chunking strategy: turn an extracted document
into semantic blocks and retrieval-sized chunks, with full provenance back to
the source. **This phase is chunking only** — embeddings, vector indexes,
lexical search and retrieval are a later phase.

---

## 1. Goal and scope

Turn the document representation an extraction produced into traceable,
retrieval-sized chunks:

> **Extraction readings → DocumentUnits → Semantic Blocks → Retrieval Chunks**

```text
SourceExtraction (versioned, with readings)
        │
        ▼
Chunking stage      parse readings → elements → sections → chunks (no LLM cost)
        │
        ▼
document_units · semantic_blocks · retrieval_chunks
```

The chunking stage runs **inside the Memora backend**, like the Extraction
Agent. It is pure and local: no model calls, no credits.

**In scope**
- Persisting the per-page document representation (the readings) so chunks can
  be regenerated without re-extracting.
- Parsing readings into fine-grained elements (DocumentUnits).
- Detecting structure and grouping elements into Semantic Blocks.
- Splitting large blocks into 300–500-token Retrieval Chunks.
- Building the embedding representation (section context + content) and
  counting tokens on it.
- Versioning and provenance: every chunk traces to its extraction version and
  source DocumentUnits.

**Out of scope for this stage:** embeddings, pgvector/HNSW, tsvector lexical
search, hybrid retrieval, reranking, context expansion, the search/ask
endpoint, the Memory Agent. The `embedding_text` and `token_count` columns
are produced now (they are part of chunking); the vector itself is not.

**Unchanged**
- The extraction pipeline's behavior and output contract.
- The ownership and security model (`Scope`, the 404-not-403 rule).
- The original file and the extraction result, which remain sources of truth.

---

## 2. Vocabulary — resolving the DocumentUnit name

The Extraction Agent already has a class called `DocumentUnit` (in
`app/processing/document.py`): a page/slide/sheet that triage routes to a
model. The chunking strategy uses "DocumentUnit" for a different concept: a
heading/paragraph/table/figure element. To avoid two meanings of one name in
one codebase, the extraction-layer class is renamed.

| Term | Layer | Meaning |
|---|---|---|
| `ReadingUnit` | extraction | A page/slide/sheet the processor routes to text/vision/layout. **Renamed from `DocumentUnit`.** Transient, in-memory. |
| Reading | extraction | What reading one `ReadingUnit` produced (page, kind, route, model, status, text). Persisted as `source_extractions.readings` JSONB. |
| `DocumentUnit` | chunking | A heading/paragraph/list/table/figure/key-value element parsed from the readings. Persisted in the `document_units` table. |
| `SemanticBlock` | chunking | A grouping of DocumentUnits that belong to the same logical section. Persisted in `semantic_blocks`. |
| `RetrievalChunk` | chunking | A retrieval-sized piece of a block, with section context and provenance. Persisted in `retrieval_chunks`. |

The pipeline therefore reads:

```text
ReadingUnit → reading → DocumentUnit → SemanticBlock → RetrievalChunk
```

`ProcessedDocument.units` keeps its field name — "units of the document being
read" is still accurate; only the element type is renamed to `ReadingUnit`.

---

## 3. What the extraction stage stores today

Today `run_extraction` persists two artifacts: the original bytes on disk, and
`source_extractions.result` (the extracted fields/tables/summary — a lossy
distillation). The per-page Markdown transcriptions the layout/vision models
produced exist only as local variables inside `extract()` and are discarded
after the single extract call. Chunking needs that text, so this stage adds:

- `AgentOutput.readings` — the agent returns its per-unit readings.
- `source_extractions.readings` JSONB — one entry per unit: `page`, `kind`,
  `difficulty`, `route`, `model`, `status`, `text`.

From then on the document representation is intact per version, and
DocumentUnits → SemanticBlocks → RetrievalChunks are all derived from it —
regeneratable without re-extracting (strategy RULE 19 / §28).

---

## 4. Data model (new tables)

`Source` is the document (its bytes); `SourceExtraction` is a versioned reading
of it. There is no separate `documents` table — that would duplicate `sources`.
Units, blocks and chunks hang off `extraction_id`, so versioning comes for free,
and "source version" in the chunk metadata is `source_extractions.version`.

```text
sources (unchanged)
   │
   └── source_extractions            (+ readings JSONB)
          │
          ├── document_units
          │
          └── semantic_blocks
                 │
                 └── retrieval_chunks
```

Ownership columns (`tenant_id`, `application_id`, `subject_id`, `source_id`)
are denormalized onto every new table, following the repo's established
convention: every read is one indexed scope predicate, and the later vector
search will need those predicates inline. `subject_id` is included because
retrieval is subject-scoped.

### document_units

| Column | Notes |
|---|---|
| id | UUID PK |
| extraction_id | FK → source_extractions, cascade |
| tenant_id, application_id, subject_id, source_id | denormalized scope |
| position | ordered index within the extraction |
| kind | heading \| text \| list \| table \| figure \| key_value |
| content | the element's text (Markdown for tables, `[Figure: …]` for figures) |
| page | 1-based page/slide/sheet the element came from |
| heading_level | int, for headings; null otherwise |
| section_path | JSONB array of strings, e.g. `["Employee Information", "Employment Details"]` |
| created_at | timestamptz |

### semantic_blocks

| Column | Notes |
|---|---|
| id | UUID PK |
| extraction_id | FK → source_extractions, cascade |
| tenant_id, application_id, subject_id, source_id | denormalized scope |
| position | ordered index |
| section_path | JSONB array |
| title | the leaf heading, or null |
| unit_ids | JSONB array of document_unit ids |
| page_start, page_end | provenance range |
| content | the block's full text (so parent-expansion later needs no re-assembly) |
| created_at | timestamptz |

### retrieval_chunks

| Column | Notes |
|---|---|
| id | UUID PK |
| semantic_block_id | FK → semantic_blocks, cascade |
| extraction_id | FK → source_extractions, cascade |
| source_version | int, copied from the extraction |
| tenant_id, application_id, subject_id, source_id | denormalized scope |
| chunk_index | ordered within the block |
| content | the chunk's raw content |
| embedding_text | section-context prefix + content (what will be embedded) |
| token_count | counted on `embedding_text` |
| content_type | text \| table \| figure \| mixed \| key_value |
| page_start, page_end | provenance range |
| document_unit_ids | JSONB array |
| section_path | JSONB array |
| is_active | bool; only the active version's chunks are searchable |
| created_at | timestamptz |

No `chunk_document_units` join table — `document_unit_ids` is a JSONB array
(strategy §16 already shows it that way; a join table is over-engineering for
MVP). No `embedding` vector column and no HNSW/tsvector indexes yet — those
arrive with the retrieval phase in a later migration.

Indexes: `(tenant_id, application_id, is_active)`, `(extraction_id)`,
`(semantic_block_id, chunk_index)`.

---

## 5. Chunking algorithm

Structural-first, semantic-fallback (strategy §7, §8):

```text
readings (per-unit Markdown, with <!-- page N · … --> markers)
        │
        ▼
parse into ordered DocumentUnits (consume page markers as provenance,
        strip them from content)
        │
        ▼
detect structure: heading hierarchy → section_path
        (heading-less fallback: numbered headings, short title/caps lines,
         blank-line clustering, key/value clustering)
        │
        ▼
group into SemanticBlocks (one per leaf section; carry page range + full text)
        │
        ▼
for each block: chunk only if it exceeds the target
        subsection → paragraph → sentence → token   (strategy §11, §12)
        tables: split by logical row groups, repeat headers
        forms / key-value: keep coherent
        │
        ▼
build embedding_text: "Section: A > B\n\n" + content
        count tokens on the final representation
```

Page boundaries are **not** chunk boundaries (RULE 3): the parser consumes the
merged document, not per-page, so a section spanning pages stays together.

### Token counting

There is no local Nemotron tokenizer, so token counts are approximate: ~4
characters per token with a safety margin, calibrated later against
provider-reported usage. The count is taken on the final `embedding_text`
(strategy §14), after the section-context prefix is added.

### Defaults (settings)

| Parameter | Initial value |
|---|---|
| `chunk_target_min` | 300 tokens |
| `chunk_target_max` | 500 tokens |
| `chunk_soft_max` | 600 tokens |
| `chunk_hard_max` | 1200 tokens (implementation safety limit) |
| `chunk_overlap` | 0 (selective; prefer context expansion later) |
| `chunk_on_extract` | true (auto-chunk after a successful extraction) |

A coherent section smaller than 300 tokens stays small (RULE 6); no padding.

---

## 6. Versioning and re-chunking

When chunking for extraction vN commits, it flips `is_active` on vN's chunks
and off on all earlier versions **in the same transaction** (strategy §27,
transactional replacement). Old chunks remain for inspection/audit but are not
searchable.

Because chunks are derived from the persisted readings, they can be
regenerated without re-extracting: `POST /sources/{id}/rechunk` re-runs the
chunking stage from the latest (or a specified) extraction's readings. This
proves RULE 19 and lets chunking logic improve without spending credits.

---

## 7. API surface (new)

| Method and path | Purpose |
|---|---|
| `POST /sources/{id}/rechunk` | Regenerate chunks from stored readings (202) |
| `GET /sources/{id}/chunks` | Inspect chunks (section path, pages, tokens, active version) |

All routes are scope-checked through `source_service.get_source`, so a source
outside the caller's scope returns 404.

---

## 8. Code layout

```text
backend/app/
├── chunking/
│   ├── __init__.py
│   ├── units.py            readings → DocumentUnits
│   ├── structure.py        heading hierarchy → section_path; heading-less fallback
│   ├── blocks.py           DocumentUnits → SemanticBlocks
│   ├── chunker.py          SemanticBlocks → RetrievalChunks (recursive split)
│   └── representation.py   embedding_text builder + token counter
├── db/models/chunking.py   DocumentUnit, SemanticBlock, RetrievalChunk tables
├── schemas/chunking.py     Pydantic models for chunk read responses
├── services/chunking_service.py  chunk_extraction(): readings → rows; is_active flip
└── api/routes/chunks.py    rechunk + chunks endpoints
```

The chunking package is pure: no LLM, no DB. The service layer is the only
thing that writes to the database, mirroring the extraction stage's shape.

---

## 9. Build order

| Step | Work |
|---|---|
| 0 | This document |
| 1 | Rename `DocumentUnit` → `ReadingUnit` (extraction layer); persist `readings` on `source_extractions` + migration |
| 2 | Chunking schema (`document_units`, `semantic_blocks`, `retrieval_chunks`) + migration, no pgvector |
| 3 | Pure chunking package (readings → units → structure → blocks → chunks → embedding_text) |
| 4 | Chunking service, auto-chunk hook, rechunk + chunks endpoints |
| 5 | Tests, console chunks viewer, docs updates |

Deferred to the next phase: embedding client + `input_type` handling, pgvector
+ HNSW, tsvector lexical search, hybrid retrieval + search endpoint, context
expansion, reranking, evaluation.

---

## 10. Verification

1. **Tests:** `pytest` in `backend/`. Existing extraction tests pass after the
   rename; new chunking tests use the strategy's §32 example document as a
   fixture, with no network calls.
2. **Migration:** `alembic upgrade head`, then `downgrade -1`, then
   `upgrade head` again, on a scratch database.
3. **Re-chunk:** extract a document, then `POST /sources/{id}/rechunk` — chunks
   regenerate from the stored readings without a new extraction run.
4. **Console:** source page shows chunks (section path, pages, tokens, active
   version).
