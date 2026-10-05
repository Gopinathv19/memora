# API reference

Every endpoint of the Memora service, grouped by resource. Written for a
developer wiring up calls: method, path, purpose, and the fields that matter.
For the concepts behind the embedding and retrieval endpoints see
[embedding-retrieval.md](embedding-retrieval.md); for the graph design see
[graph-rag.md](graph-rag.md). Interactive docs (OpenAPI/Swagger) are served
at `/docs` when the backend runs.

---

## 1. Conventions

**Base URL.** Everything is under `/api/v1`. Health check: `GET /health`
(no prefix).

**Authentication — two kinds of caller:**

| Caller | How | Scope |
|---|---|---|
| Client application | `Authorization: Bearer memora_…` (API credential) | Its own application only |
| Console user | `memora_session` HttpOnly cookie (Google sign-in) | Every tenant the user owns |

**Scope rule.** A row outside the caller's scope does not exist: reads and
writes return **404, not 403** (a 403 would confirm the id exists in someone
else's tenant). Ownership columns (`tenant_id`, `application_id`, …) are
derived from the path, never accepted from the body.

**Synchronous vs. background.** Endpoints that start slow, model-backed work
(extraction, graph build, embedding) return **202 Accepted** immediately with
the run in `processing`; poll the matching status endpoint. Everything else
is synchronous.

**Errors.** Domain errors are consistent JSON:

```json
{ "detail": "human-readable message", "code": "machine_code" }
```

Common statuses: `200` ok · `201` created · `202` accepted (background work
started) · `204` no content (delete/logout) · `404` not found or outside scope
· `409` a run is already processing · `413` upload too large · `422`
validation · `503` a subsystem (e.g. the graph) is not configured.

---

## 2. Auth (console)

| Method & path | Purpose |
|---|---|
| `POST /auth/google` | Sign in with a Google ID token; first sign-in creates the account. Sets the session cookie. |
| `GET /auth/me` | Who the current session belongs to (the console's login check). |
| `POST /auth/logout` | Clears the session cookie. `204`. |

`POST /auth/google` body: `{ "id_token": "<Google ID token>" }` → response:
`{ "user": { "id", "email", "name", "avatar_url" } }`.

---

## 3. Tenants, applications, subjects, actors, credentials

The ownership chain. Nested paths create inside a parent; flat paths list
across it (scope-filtered either way).

### Tenants
| Method & path | Purpose |
|---|---|
| `POST /tenants` | Create a tenant. Body: `{ "name" }`. `201`. |
| `GET /tenants` | List tenants in scope (with counts). |
| `GET /tenants/{tenant_id}` | One tenant. |
| `PATCH /tenants/{tenant_id}` | Update. Body: `{ "name"? }`. |

### Applications
| Method & path | Purpose |
|---|---|
| `POST /tenants/{tenant_id}/applications` | Create an application inside a tenant. Body: `{ "name", "slug"? }`. `201`. |
| `GET /tenants/{tenant_id}/applications` | List a tenant's applications. |
| `GET /applications?tenant_id=` | Flat list across tenants (console). |
| `GET /applications/{application_id}` | One application, with counts. |
| `PATCH /applications/{application_id}` | Update. Body: `{ "name"? }`. |

### Subjects (workspaces / folders)
| Method & path | Purpose |
|---|---|
| `POST /applications/{application_id}/subjects` | Create a subject (optionally `parent_subject_id` to nest). `201`. |
| `GET /applications/{application_id}/subjects` | List an application's subjects. |
| `GET /subjects?application_id=&parent_id=` | Flat list, filterable. |
| `GET /subjects/{subject_id}` | One subject, with counts. |
| `PATCH /subjects/{subject_id}` | Update (name, parent). |
| `DELETE /subjects/{subject_id}` | Delete a subject and its subtree. `204`. |

### Actors (audit only — never ownership)
| Method & path | Purpose |
|---|---|
| `POST /applications/{application_id}/actors` | Register the consuming app's end user. Body: `{ "external_id", "type", "display_name"? }`. `201`. |
| `GET /actors?application_id=` | Flat list. |
| `GET /actors/{actor_id}` | One actor, with counts. |
| `PATCH /actors/{actor_id}` | Update display name/type. |

### Credentials (API keys)
| Method & path | Purpose |
|---|---|
| `POST /applications/{application_id}/credentials` | Issue a credential. Body: `{ "name" }`. **The only response that carries the raw token** — store it now. `201`. |
| `GET /credentials?application_id=` | List credentials (token preview only, never the secret). |
| `PATCH /credentials/{credential_id}` | Update name/status. |
| `DELETE /credentials/{credential_id}` | Revoke. `204`. |

---

## 4. Sources

| Method & path | Purpose |
|---|---|
| `POST /subjects/{subject_id}/sources` | Register a source from metadata (URL, chat, external storage) — no bytes move. Body: `{ "type": "file"\|"url"\|"chat", "filename", "storage_uri"? }`. `201`. |
| `POST /subjects/{subject_id}/sources/upload` | Register by posting the file itself (multipart). Optional form fields: `created_by_actor_id`, `extract` (bool), `extract_mode`, `extract_instructions`, `build_graph` (bool). `201`; with `extract=true` the row returns in `processing`. |
| `GET /subjects/{subject_id}/sources?status=` | List a subject's sources. |
| `GET /sources?tenant_id=&application_id=&subject_id=&status=` | Flat list, filterable. |
| `GET /sources/{source_id}` | One source, with detail. |
| `GET /sources/{source_id}/content` | Stream the stored bytes back (if Memora stored them). |
| `PATCH /sources/{source_id}` | Update metadata. |
| `POST /sources/{source_id}/move` | Move to another subject in the same application. Body: `{ "target_subject_id" }`. |
| `DELETE /sources/{source_id}` | Delete the row and its stored bytes. `204`. |

The client never sends `tenant_id`/`application_id` — they are copied from the
subject in the path.

---

## 5. Extractions

All under `/sources/{source_id}/extractions`. Every run is a new version;
re-extracting adds v2 next to v1, never overwrites.

| Method & path | Purpose |
|---|---|
| `POST /sources/{source_id}/extractions` | Start the next extraction version. `202`; poll `/latest`. `409` if a run is already processing. |
| `GET /sources/{source_id}/extractions` | Every version, newest first, without results. |
| `GET /sources/{source_id}/extractions/latest` | The newest version, with result and usage. |
| `GET /sources/{source_id}/extractions/{version}` | One version, with result and usage. |

**`POST` body** (`ExtractionRequest`):

| Field | Type | Default | Meaning |
|---|---|---|---|
| `mode` | `standard` \| `deep` | `standard` | Run configuration. |
| `instructions` | string ≤ 2000 | — | Optional hint ("capture the premium table on page 3"). Guidance only. |
| `actor_id` | uuid | — | The end user this run is for. Cost attribution only; grants nothing. |
| `build_graph` | bool | `false` | Build the knowledge graph after a successful run. |

**Response** (`ExtractionRead`): `id`, `source_id`, `version`, `status`
(`processing` → `completed` \| `partial` \| `failed`), `mode`, `provider`,
`models` (role→model map), `result` (the extracted `document_type`, `title`,
`summary`, `fields[]`, `tables[]`, `pages[]` provenance), `usage[]` (one row
per model call: model, role, tokens, `cost_usd`, latency), `error`,
`prompt_tokens`, `completion_tokens`, `cost_usd`, `finished_at`.

---

## 6. Chunks

| Method & path | Purpose |
|---|---|
| `POST /sources/{source_id}/rechunk?version=` | Regenerate chunks from an extraction's stored readings. Free — no model calls. Optional `version` (latest if omitted). |
| `GET /sources/{source_id}/chunks?active_only=true` | List chunks; `active_only=false` includes older versions' chunks. |

**`RechunkResponse`**: `extraction_id`, `source_id`, `version`, `chunk_count`.

**`ChunkRead`** (per chunk): `chunk_index`, `content`, `embedding_text` (what
gets embedded — content prefixed with its section path), `token_count`,
`content_type` (`text` \| `table` \| `figure` \| `key_value` \| `mixed`),
`section_path`, `page_start`, `page_end`, `is_active`.

---

## 7. Embeddings

All under `/embedding`. Embedding is background work: enqueue endpoints
return `202` with real queued counts, then a worker drains the queue.

### Models & strategies

| Method & path | Purpose |
|---|---|
| `GET /embedding/models` | The registered model catalog (provider, identifier, dimension, normalization, metric). Global data. |
| `GET /embedding/strategies` | Every strategy, active and inactive. |
| `POST /embedding/strategies` | Create a new strategy over an existing model — the entry point for introducing a new embedding configuration. `201`. |
| `GET /embedding/strategies/{strategy_id}` | One strategy. |
| `PATCH /embedding/strategies/{strategy_id}` | Update **only** `description` / `is_active`. This is how the active strategy is switched. |

**`POST /embedding/strategies` body** (`EmbeddingStrategyCreate`):

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | — | Required. |
| `description` | string | — | Optional. |
| `model_id` | uuid | — | Required — a row from `GET /embedding/models`. |
| `input_type` | string | `embedding_text` | Which chunk field is embedded. |
| `document_template` | string | `{input}` | Template wrapping document text; must contain `{input}`. |
| `query_template` | string | `{input}` | Template wrapping the query; must contain `{input}`. |
| `configuration_json` | object | `{}` | Free-form extras. |

Dimension, normalization and similarity metric are **inherited from the
model row**, never supplied — the vector column and HNSW index are typed for
them. A materially different configuration is a new strategy row, not an edit.

**Strategy response** (`EmbeddingStrategyRead`): `id`, `name`, `description`,
`model_id`, `input_type`, `document_template`, `query_template`,
`normalization`, `similarity_metric`, `dimension`, `configuration_json`,
`is_active`, `created_at`, `updated_at`, plus joined model fields
(`model_provider`, `model_name`, `model_identifier`).

### Embedding work

| Method & path | Purpose |
|---|---|
| `POST /embedding/sources/{source_id}/embed` | Enqueue the source's active chunks. Body: `{ "strategy_id"?, "force"? }`. `202`. |
| `POST /embedding/subjects/{subject_id}/embed` | Enqueue every active chunk under a subject (all its sources). `202`. |
| `POST /embedding/strategies/{strategy_id}/rebuild` | Force re-embed everything in scope under a strategy. `202`. |
| `POST /embedding/retry-failed` | Reset failed embeddings to pending and drain. Body: `{ "strategy_id"? }`. `202`. |

All four return `{ "enqueued": <n>, "strategy_id", "strategy_name" }` — work
queued, not work done. Embedding is idempotent: unchanged chunks (same input
hash) are skipped unless `force` is true.

### Health & debug

| Method & path | Purpose |
|---|---|
| `GET /embedding/stats` | Scope-wide health: `total_chunks`, `embedded`, `pending`, `processing`, `failed`, `stale`, `coverage_percent`. |
| `GET /embedding/sources/{source_id}/status` | The same counts for one source. |
| `GET /embedding/chunks/{chunk_id}/embeddings` | Every embedding row for one chunk: status, input hash, attempt count, vector preview. Debug view. |
| `POST /embedding/query` | Embed a query string under a strategy. Body: `{ "query", "strategy_id"? }`. Returns the vector + context — retrieval preparation only, no search. |

---

## 8. Knowledge graph

All under `/sources/{source_id}/graph` and `/subjects/{subject_id}/graph`.
Design and concepts: [graph-rag.md](graph-rag.md). Without `FALKORDB_URL`
configured these answer `503`.

| Method & path | Purpose |
|---|---|
| `POST /sources/{source_id}/graph` | Build the source's graph from its latest extraction. Body: `{ "retry_failed"?, "actor_id"? }`. `202`; poll `/builds/latest`. `409` while a build runs. |
| `GET /sources/{source_id}/graph/builds` | Every build, newest first. |
| `GET /sources/{source_id}/graph/builds/latest` | The latest build: status, counts, failed chunks, usage. |
| `GET /subjects/{subject_id}/graph?include_subfolders=&max_entities=&max_relationships=` | The subject's graph for plotting (entities + relationships, from its sources only). |
| `POST /subjects/{subject_id}/graph/query` | Graph evidence for a question: the entities it names, their neighbourhood, and the source chunks behind every fact. |

**`POST /subjects/{id}/graph/query` body** (`GraphQueryRequest`): `query`
(≤ 1000 chars), `max_hops` (1–3, default 2), `max_entities` (1–100, default
20), `max_relationships` (1–200, default 50), `include_subfolders` (default
true).

**Response** (`GraphRetrievalResult`): `seed_entity_ids`, `entities[]`
(id, name, type, description, aliases, mention count, provenance),
`relationships[]` (source → relation → target, with confidence and
provenance), `chunks[]` (the source passages behind the facts). Structured
evidence — not an LLM answer.

---

## 9. Retrieval — asking questions

The centerpiece: a synchronous, read-only question over the caller's embedded
knowledge. Concepts and the four-stage pipeline:
[embedding-retrieval.md](embedding-retrieval.md).

### `POST /query`

**Request** (`QueryRequest`):

| Field | Type | Default | Meaning |
|---|---|---|---|
| `query` | string ≤ 4000 | — | Required. The question. |
| `subject_id` | uuid | — | Narrow the search to one subject. |
| `source_id` | uuid | — | Narrow the search to one source. |
| `strategy_id` | uuid | — | Query under a non-active strategy (A/B evaluation). |

**Response** (`QueryResponse`):

| Field | Type | Meaning |
|---|---|---|
| `query` | string | The question, echoed. |
| `answer` | string | The grounded answer, written from the evidence only. |
| `strategy_id`, `strategy_name` | string | Which strategy's vector space was searched. |
| `chunks[]` | list | The evidence: each with `chunk_id`, `source_id`, `subject_id`, `content`, `content_type`, `section_path`, `page_start`, `page_end`, `token_count`, `similarity` (cosine, stage 1), `rerank_score` (cross-encoder, stage 3 — null when the fallback was used). |
| `retrieval` | object | The pipeline trace: `hnsw_candidates`, `mmr_candidates`, `final_chunks`, `reranker_used`. |
| `usage` | object | The answering model's `prompt_tokens`, `completion_tokens`, `latency_ms`. |

**Example:**

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Authorization: Bearer memora_..." -H "Content-Type: application/json" \
  -d '{"query": "What is the notice period for early termination?",
       "subject_id": "…"}'
```

```json
{
  "query": "What is the notice period for early termination?",
  "answer": "Either party may terminate early with 30 days written notice…",
  "strategy_id": "…", "strategy_name": "Memora Dense v1",
  "chunks": [
    {
      "chunk_id": "…", "source_id": "…", "subject_id": "…",
      "content": "…", "content_type": "text",
      "section_path": ["Agreement", "Termination"],
      "page_start": 4, "page_end": 4, "token_count": 312,
      "similarity": 0.8341, "rerank_score": 6.42
    }
  ],
  "retrieval": { "hnsw_candidates": 50, "mmr_candidates": 10,
                 "final_chunks": 5, "reranker_used": true },
  "usage": { "prompt_tokens": 2140, "completion_tokens": 96, "latency_ms": 1830 }
}
```

When nothing in scope is embedded, the answer says so plainly and
`retrieval` reports zeros — no guessing.

---

## 10. Dashboard & usage

| Method & path | Purpose |
|---|---|
| `GET /stats` | Live counts for the console dashboard. |
| `GET /stats/metrics?days=30` | Daily activity, storage and type mix (window 7–90 days). |
| `GET /whoami` | What the presented API credential resolves to (credential → application → tenant). |
| `GET /usage/extractions` | Extraction usage and cost in scope: totals, by application, by model, by trigger. |
| `GET /health` | Liveness. `{"status": "ok"}`. |
