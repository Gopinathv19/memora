# Knowledge graph (GraphRAG) — design

Status: **built** (2026-09-25), backend and console. This
document is both the design and a description of what shipped. It sits below
the [Extraction Agent](extraction-agent.md). The vector/embedding pipeline is
a separate piece of work and is not part of this.

---

## 1. Goal and scope

Turn what the Extraction Agent read into a knowledge graph, and answer
questions from it with evidence:

> **Extraction content → entities + relationships (FalkorDB) → graph evidence**

```text
source_extractions.content      the merged Markdown the agent read (new column)
        │
        ▼
chunking            page-aware text units            app/graph/chunking.py
        ▼
LLM extraction      one NVIDIA call per unit          app/graph/extraction.py
        ▼
validation          lenient, item by item             app/graph/extraction.py
        ▼
normalization       display name + canonical key      app/graph/normalization.py
        ▼
resolution          one node per real-world thing     app/graph/resolution.py
        ▼
FalkorDB            one graph per tenant               app/graph/store.py
        ▼
retrieval           seeds → 1..3 hops → chunks         app/graph/retrieval.py
        ▼
GraphRetrievalResult  entities, relationships, source chunks
```

**In scope:** building and rebuilding a source's graph, retrying failed chunks,
cost tracking, a subject's graph view, question-based graph retrieval, and
keeping the graph in step with deletes and moves.

**Out of scope for this stage:** embeddings and vector search (another
developer), the final answering layer that merges vector and graph evidence,
LLM-generated Cypher, and community detection and summaries.

**Console:**
- **Source page — `GraphPanel`:** a Build / Rebuild graph button, and a
  "Retry failed chunks" button when the latest build is `partial`. It shows
  status, counts, failed chunks and model calls, and polls while a build runs.
- **Extract / Re-extract dialog:** "Also build the knowledge graph" is ticked
  by default.
- **Subject page — `SubjectGraph`:** an SVG drawing of the subject's graph,
  laid out with a small built-in force-directed layout (no new dependency).
  - It has an "Include subfolders" toggle.
  - Clicking an entity shows its connections.
  - An "Ask the graph" box highlights the matching subgraph and lists the
    facts and source passages.

---

## 2. Decisions

These were settled one by one before the build.

| # | Decision |
|---|---|
| 1 | The agent's merged Markdown is stored on the extraction version (`source_extractions.content`). The graph reads it, and the vector pipeline can read it too. |
| 2 | Building is **explicit**, like extraction: `POST /sources/{id}/graph`, or `build_graph=true` on an extract/upload so it chains after a successful run. |
| 3 | **One extraction version is live per source.** A full build replaces the facts that source contributed before. |
| 4 | Every build is a `source_graph_builds` row in Postgres. |
| 5 | Graph model calls go in the shared `extraction_usage` ledger (role `graph`, `graph_build_id` set). |
| 6 | `LLM_GRAPH_MODEL` defaults to `nvidia/nemotron-3-super-120b-a12b`. The `nvidia/` guard applies. |
| 7 | **One FalkorDB graph per tenant.** Applications are not a boundary inside it; **the subject (workspace) is the unit that matters**. |
| 8 | Entity nodes are **tenant-wide**. A subject's view is filtered by provenance: only facts from that subject's sources. |
| 9 | A subject's view covers its **subtree** by default. The folder tree stays in Postgres; a Document node only carries its `subject_id`. |
| 10 | The graph is updated **after** the Postgres commit, with a startup sweep for anything missed. |
| 11 | The ontology is a versioned file, `backend/ontology.json`. Unknown types are mapped to a fallback and the original label is kept. |
| 12 | Facts are **typed edges** (`[:WORKS_FOR]`), whitelisted against the ontology before they reach Cypher. |
| 13 | There is **one edge per (fact, source)**, carrying that source's chunk ids. |
| 14 | Chunk text is stored on the Chunk node, with offsets into `content`. |
| 15 | Chunking is page-aware paragraph packing: ~3,000 characters with ~300 characters of overlap, and a table is not cut if it fits. |
| 16 | Resolution is normalization, plus model-given aliases, plus strict fuzzy matching. It makes no extra LLM calls. |
| 17 | Seed entities are found by exact key/alias match plus a full-text index. There is no LLM at query time. |
| 18 | The API covers build, build status, subject view and subject query. |
| 19 | The backend comes first; the console is a later step. |
| 20 | The graph runs on **FalkorDB Cloud**. Tests use a throwaway graph prefix on any FalkorDB. |
| 21 | If some chunks fail, the build is `partial` and a retry re-reads only those chunks. |

---

## 3. Isolation and scope

```text
FalkorDB server
├── memora_tenant_3f9a…     tenant A: every application, subject and source of A
└── memora_tenant_d07e…     tenant B
```

- The graph name is built from Memora's own tenant UUID and
  `FALKORDB_GRAPH_PREFIX`. Nothing a client sends goes into it. A query can
  only ever touch the graph it runs against, so **tenants are separated
  physically**.
- **Inside a tenant, the subject decides what you see.** Every graph request
  starts in Postgres:
  1. `get_subject(db, subject_id, scope)` returns 404 outside the caller's
     scope. An API credential only reaches its own application.
  2. The subtree is found with the existing recursive CTE.
  3. The source ids in that subtree are passed to the graph.
- Every read method in `store.py` takes the tenant **and** the in-scope source
  ids, so there is no way to ask the graph layer an unscoped question.
- Entities are shared across the tenant (decision 8), but a subject view only
  returns facts, mentions, descriptions and chunks from its own sources.
  "Arun Kumar" in Case-1 and "Arun Kumar" in Case-2 may share a node, but
  neither workspace sees the other's facts about him.

---

## 4. Graph model (FalkorDB)

```text
(:Document {id = source_id, subject_id, application_id, tenant_id, filename,
            extraction_id, extraction_version, build_id})
    │ CONTAINS
    ▼
(:Chunk {id = "<source_id>:<version>:<index>", source_id, index,
         page_start, page_end, char_start, char_end, text})
    │ MENTIONS {source_id, description}
    ▼
(:Entity {id, tenant_id, entity_type, canonical_key, display_name,
          aliases[], alias_keys[], raw_types[], search_text})

(:Entity)-[:PROVIDES_TO {source_id, chunk_ids[], description, confidence,
                         raw_relation, build_id}]->(:Entity)
```

- **Entity ids** are `uuid5(tenant_id, "<TYPE>:<canonical_key>")` for a new
  entity. Rebuilding the same document therefore produces the same ids.
  Writes use `MERGE`, so re-ingesting a document is idempotent.
- **Every fact edge carries `source_id`.** There is one edge per (source
  entity, type, target entity, source). Its `chunk_ids` accumulate every chunk
  of that source stating the fact, and its confidence is the highest seen.
- **Every relationship endpoint is also MENTIONED** by the chunk that states
  the fact. That invariant is what makes deletion simple (below).
- **Indexes**, created on first use of each tenant graph: range indexes on
  `Entity.id`, `Entity.canonical_key`, `Entity.entity_type`, `Chunk.id`,
  `Chunk.source_id` and `Document.id`, plus a full-text index on
  `Entity.search_text` (the display name and aliases).

### Deleting or rebuilding one source

`FalkorGraphStore.delete_source` runs these steps in order:

1. Collect the entities that source's chunks mention.
2. Delete fact edges with `source_id = S` touching those entities.
3. Delete the source's chunks (which removes their MENTIONS) and its Document.
4. Delete those entities only if **no other chunk still mentions them**.

A shared entity survives as long as any other source supports it.

---

## 5. The pipeline

### Chunking (`chunking.py`)

1. Split `content` on its `<!-- page N · … -->` markers, so every block knows
   its page.
2. Split each page on blank lines. A Markdown table has no blank lines, so it
   stays one block. A block longer than a unit is cut at line breaks, then at
   whitespace.
3. Pack blocks in order up to `GRAPH_CHUNK_CHARS`. Each unit after the first
   starts with up to `GRAPH_CHUNK_OVERLAP` characters from the end of the
   previous one.

Units may span pages (`page_start`/`page_end`). Nothing is dropped. The
chunking settings are stored on the build, so a retry chunks identically even
if `.env` changed in between.

### Extraction (`extraction.py`, `prompts.py`)

- Each unit is sent to `LLM_GRAPH_MODEL` through the existing `LLMClient`:
  temperature 0, JSON mode, and one repair retry.
- The prompt lists the ontology's entity types and relationship types with
  their meanings, and asks for aliases.
- The chunk is wrapped in `<chunk>` tags as data, not instructions.
- The reply is validated item by item with Pydantic. Malformed items are
  dropped and the rest kept, with at most 200 items per list per chunk.
- At most `GRAPH_CONCURRENCY` calls run in parallel.

### Ontology (`ontology.py`, `backend/ontology.json`)

- The file holds `entity_types` and `relation_types`, each with a description
  shown to the model, plus these maps:
  - `entity_type_synonyms`: e.g. `CORPORATION → COMPANY`.
  - `relation_synonyms`: e.g. `WORKS_AT → WORKS_FOR`.
  - `inverse_relations`: e.g. `OWNED_BY → OWNS`, stored with source and
    target swapped.
- A label the file doesn't know becomes `CONCEPT` or `RELATED_TO`, and the
  model's own label is kept as `raw_types` or `raw_relation`. That makes it
  visible which types to add next.
- The file is validated at startup. A relation must match
  `^[A-Z][A-Z0-9_]*$` and may not reuse `CONTAINS` or `MENTIONS`. That check is
  what makes putting relation names into Cypher text safe.

### Normalization and resolution (`normalization.py`, `resolution.py`)

- `display_name` keeps the document's spelling. `canonical_key` is what
  identity is decided on. It is built by:
  - casefolding and applying NFKC;
  - turning `&` into `and` and joining dotted acronyms (`I.B.M.` → `ibm`);
  - for organizations, dropping a leading "the" and trailing legal forms
    (Inc, Corp, Ltd, Pvt, LLC, GmbH, …);
  - for people, dropping leading honorifics (Mr, Dr, Shri, Smt, …).
- Resolution works within one entity type, against the tenant's existing
  entities and the ones created earlier in the same build. The first rule that
  matches wins:
  1. **Exact:** the key or any alias key matches.
  2. **Acronym:** for organizations only, with at least 3 letters. "IBM" and
     "International Business Machines" match.
  3. **Fuzzy:** `rapidfuzz` ratio of at least `GRAPH_FUZZY_THRESHOLD` (default
     92), for keys of 5 or more characters. This catches OCR typos.
  4. Otherwise a new entity is created.
- A false merge is worse than a duplicate, so every step is conservative.

### Failure handling

- **One chunk fails:** it is recorded in `failed_chunks` (index, id, pages,
  error), the rest are written, and the build ends **`partial`**.
- **Every chunk fails:** **nothing is written**, so a failed rebuild never
  wipes the graph a source already had. The build ends `failed`.
- **More units than `GRAPH_MAX_CHUNKS`:** the extras are recorded as failed
  (not silently dropped), so a retry reads them.
- **Writing to FalkorDB fails:** the build ends `failed` with the error.
  Rebuild to recover; a full build always starts by clearing the source.
- **Interrupted builds:** a build still `processing` at startup is marked
  `failed`, like extraction runs.

---

## 6. Retrieval (`retrieval.py`)

Retrieval is deterministic: no LLM and no generated Cypher.

1. **Seeds**, at most 5:
   - First, every 1–4 word phrase of the question is turned into a canonical
     key and matched exactly against keys and aliases.
   - Then the full-text index is searched with the question's non-stopword
     tokens OR-ed together.
   - A seed must be mentioned by a chunk in scope.
2. **Expansion:** breadth-first, one hop at a time, up to `max_hops` (default
   2, maximum 3), in either direction. It only follows fact edges whose
   `source_id` is in scope, and stops at `max_entities` and
   `max_relationships`.
3. **Evidence:**
   - Per-source edges are merged into one relationship with all its supporting
     chunk ids.
   - Entities come with their in-scope descriptions and chunk ids.
   - The text of up to 20 supporting chunks is returned.

The result is structured data, not a prompt:

```json
{
  "seed_entity_ids": ["…dell…"],
  "entities": [
    {"entity_id": "…", "name": "Dell", "entity_type": "COMPANY", "description": "…",
     "aliases": [], "mention_count": 1, "source_ids": ["…"], "source_chunk_ids": ["…:1:0"]}
  ],
  "relationships": [
    {"source_entity_id": "…", "source_name": "Dell", "relation": "PROVIDES_TO",
     "target_entity_id": "…", "target_name": "Microsoft Corporation",
     "description": "Dell provides servers to Microsoft", "confidence": 0.8,
     "raw_relation": null, "source_ids": ["…"], "source_chunk_ids": ["…:1:0"]}
  ],
  "source_chunk_ids": ["…:1:0"],
  "chunks": [{"chunk_id": "…:1:0", "source_id": "…", "index": 0, "page_start": 1,
              "page_end": 1, "char_start": 34, "char_end": 211, "text": "…"}]
}
```

The **subject view** (`GET /subjects/{id}/graph`) returns the subject's
most-mentioned entities (up to `max_entities`, default 200) and the facts
between them, without chunk text. It's meant for the console to plot.

---

## 7. API

| Method and path | Purpose |
|---|---|
| `POST /sources/{id}/graph` `{retry_failed?, actor_id?}` | Build (or retry) → **202** with the build in `processing` |
| `GET /sources/{id}/graph/builds` | Every build, newest first |
| `GET /sources/{id}/graph/builds/latest` | The newest build, with its model calls |
| `GET /subjects/{id}/graph?include_subfolders=&max_entities=&max_relationships=` | The subject's graph |
| `POST /subjects/{id}/graph/query` `{query, max_hops, max_entities, max_relationships, include_subfolders}` | Graph evidence for a question |
| `POST /sources/{id}/extractions` `{…, build_graph}` / upload form `build_graph` | Chain a build after extraction |

Error responses:

| Code | When |
|---|---|
| **404** | Outside the caller's scope. |
| **409** | A build is already running for this source. |
| **422** | There's no completed extraction; the extraction predates `content`, so re-extract; or `retry_failed` was sent without a `partial` latest build. |
| **503** | `FALKORDB_URL` isn't set. Uploads and extraction keep working, and `build_graph=true` is skipped with a log line. |

---

## 8. Data model (Postgres)

- `source_extractions.content` (`TEXT`, nullable).
- `source_graph_builds`:
  - `source_id`, `tenant_id`, `application_id`, `extraction_id`,
    `extraction_version`, and `retry_of_id`;
  - `status`, `provider`, `model`, `chunk_chars`, `chunk_overlap`;
  - counts: `chunk_count`, `failed_chunk_count`, `entity_count`,
    `relationship_count`;
  - `failed_chunks` (JSONB), `stats` (JSONB), `error`;
  - token and cost totals, `triggered_by_*`, `actor_id`, `created_at` and
    `finished_at`.
  - It cascades from sources and extractions.
- `extraction_usage.graph_build_id` is nullable, with `ON DELETE CASCADE`.
  `SourceExtraction.usage` now excludes rows that have it, so an extraction's
  usage list stays its own. `GET /usage/extractions` includes graph calls
  (role `graph`).
- Migration: `b6d4e8f1a2c3_graph_builds`.

Pricing: `price_call` now counts an image only for `layout` and `vision`. The
old rule, "every role except `extract`", would have charged graph calls as
images.

---

## 9. Configuration

| Setting | Default | |
|---|---|---|
| `FALKORDB_URL` | empty (graph off) | `falkors://user:pass@host:port` for FalkorDB Cloud (TLS) |
| `FALKORDB_GRAPH_PREFIX` | `memora` | Graph names are `<prefix>_tenant_<uuid>` |
| `FALKORDB_TIMEOUT_MS` | 30000 | Per query |
| `LLM_GRAPH_MODEL` | `nvidia/nemotron-3-super-120b-a12b` | Must be `nvidia/…` |
| `GRAPH_CHUNK_CHARS` / `GRAPH_CHUNK_OVERLAP` | 3000 / 300 | |
| `GRAPH_CONCURRENCY` | 4 | Parallel chunk calls |
| `GRAPH_MAX_CHUNKS` | 200 | Per build; extras are recorded for retry |
| `GRAPH_FUZZY_THRESHOLD` | 92 | 0–100 |
| `GRAPH_ONTOLOGY_FILE` | `backend/ontology.json` | |

---

## 10. Code layout

```text
backend/
├── ontology.json                   entity / relation types (operator config)
└── app/
    ├── graph/
    │   ├── models.py               GraphTextUnit, extraction + resolved objects
    │   ├── ontology.py             load/validate/map types; Cypher whitelist
    │   ├── chunking.py             content → GraphTextUnits
    │   ├── prompts.py              the extraction prompt
    │   ├── extraction.py           GraphExtractor: one LLM call per unit
    │   ├── normalization.py        display_name, canonical_key, acronym
    │   ├── resolution.py           EntityResolver, resolve_graph
    │   ├── store.py                FalkorGraphStore — the only FalkorDB import
    │   ├── ingestion.py            GraphIngestionService.ingest_document
    │   ├── retrieval.py            GraphRetrievalService.retrieve / .view
    │   └── sync.py                 post-commit delete/move hooks
    ├── services/graph_service.py   builds, usage, scope → source ids
    ├── db/models/graph.py          SourceGraphBuild
    ├── schemas/graph.py            request/response models
    └── api/routes/graph.py
```

For the vector developer, the entry points that don't depend on HTTP are:

```python
GraphIngestionService.ingest_document(tenant_id=…, application_id=…, subject_id=…,
    source_id=…, extraction_id=…, extraction_version=…, build_id=…, content=…)
GraphRetrievalService.retrieve(tenant_id=…, source_ids=[…], query=…, max_hops=2)
graph_service.subject_source_ids(db, subject_id, scope)   # scope → source ids
```

---

## 11. Tests

- `tests/test_graph_units.py` covers the ontology, normalization, chunking,
  resolution, lenient validation and pricing. It needs no graph database.
- `tests/test_graph_pipeline.py` runs ingestion and retrieval on a real
  FalkorDB. It covers:
  - typed facts and provenance;
  - deduplicating company variants;
  - 1- and 2-hop retrieval, and finding seeds by alias and full-text;
  - tenant and subject isolation;
  - idempotent rebuilds and version replacement;
  - deletion that keeps shared entities;
  - partial builds and retry, all-fail keeping the old graph, and the chunk
    limit.
- `tests/test_graph_api.py` runs through the API. It covers:
  - `build_graph` chaining, usage split and the stored content;
  - the 422, 409 and 503 errors;
  - subfolder views and queries;
  - credential scope returning 404;
  - delete and move sync;
  - retry via the API, interrupted builds and the orphan sweep.

The graph-database tests need `TEST_FALKORDB_URL`, or `falkordblite`
installed for an embedded FalkorDB; otherwise they are skipped. Each test uses
a random graph prefix and drops its graphs afterwards.

---

## 12. Known limits and next steps

- **Resolution load:** each build loads the tenant's entities of the types it
  saw, so memory use grows with the tenant. Past tens of thousands of
  entities, switch to per-name candidate lookups.
- **Aliases outlive their source:** aliases a deleted source contributed stay
  on a surviving entity. That's harmless for scoping, but it can widen
  full-text matching.
- **Cross-type duplicates:** the same name typed differently (`COMPANY` vs
  `ORGANIZATION`) makes two nodes, by design. Merge them in the ontology if
  that becomes common.
- **Connection:** the FalkorDB client connects inside its own constructor, so
  `FalkorGraphStore` connects on first use. An unreachable graph never stalls
  uploads or extraction. Graph reads then answer **503** "not reachable", and a
  build ends `failed` with the connection error.
- **Next:**
  - Optional LLM-assisted seed detection behind `seed_entities`.
  - Community summaries, once this layer is proven on real documents.
