# Memora — Project Context & Handoff

> **Purpose of this file:** a complete, self-contained briefing of everything
> built in this repository so far, written to be fed to another coding agent
> (or a new developer) so work can continue without re-deriving history.
> Generated 2026-10-09 from `main` @ `629f371` plus uncommitted work.
> Deeper detail lives in `docs/` (see §12).

---

## 1. What Memora is

Memora is the **core memory service** for applications that need durable,
retrievable context. A client application registers files, URLs or chat
transcripts against a workspace; Memora stores them, extracts their content
with LLMs, chunks them, embeds them into pgvector, builds a knowledge graph,
and answers questions with a grounded, evidence-cited RAG pipeline.

It is being built for the **Nebius × NVIDIA Global AI Hackathon** — hence
**NVIDIA open models only** (via build.nvidia.com in development, Nebius
Token Factory for text models in production).

The full processing chain, all built and working:

```
Upload → Extraction Agent (NVIDIA LLMs) → Chunking (pure/local)
       → Embeddings (pgvector + HNSW)  → Retrieval (vector → MMR → rerank → grounded answer)
       → Knowledge Graph (FalkorDB, GraphRAG)
       → Evaluation harness (backend/evals/)
```

Three deployables:

| Directory | Domain | What it is |
|---|---|---|
| `backend/` | `api.memora.x.in` | FastAPI service, PostgreSQL, Alembic migrations |
| `console/` | `console.memora.x.in` | Operator console (Next.js 16), behind Google session login |
| `web/` | `memora.x.in` | Public landing page + static API reference, no login |

---

## 2. The ownership chain (the core data model)

Everything hangs off one chain. Reading it top to bottom is the whole model:

```
User ──owns──► Tenant (Acme Law Firm)
                └── Application (LegalCase)
                      ├── ApiCredential (memora_… tokens)
                      ├── Actor (lawyer_123 — who caused something; audit only)
                      └── Subject (case-ABC-456 — the workspace; nests as folders)
                            └── Source (complaint.pdf — file | url | chat)
                                  └── SourceExtraction (v1, v2, … versioned)
                                        ├── DocumentUnits → SemanticBlocks → RetrievalChunks
                                        ├── ChunkEmbeddings (pgvector, strategy-versioned)
                                        └── SourceGraphBuilds (FalkorDB facts)
```

Key rules that must never be violated:

- **An actor is not a subject.** Actor = who *caused* a workspace to exist;
  subject = the workspace itself. `actor_id` on a subject means "created by",
  never "may access".
- **Ownership columns are derived, never accepted from clients.** A source's
  `tenant_id`/`application_id` are copied from its subject. Clients never send
  them.
- **Cross-tenant access returns 404, never 403** — a 403 would confirm the id
  exists in someone else's tenant, which is itself a leak.
- **The whole chain must agree.** `GET /subjects/{a}/sources/{b}` fails if
  source `b` lives in a different subject, even when both belong to you.
- **Denormalized scope.** Every table from `sources` down carries
  `tenant_id`, `application_id`, `subject_id`, `source_id` so every read is
  one indexed predicate.
- Memora is **not an identity provider**: actors store only the client's own
  `external_id` — no email, no password, no role.

---

## 3. Architecture & stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, Uvicorn, SQLAlchemy 2, psycopg 3, Alembic, Pydantic v2, pydantic-settings |
| Database | PostgreSQL (Neon in practice; pgvector extension for embeddings) |
| Graph DB | FalkorDB (one graph per tenant; optional — endpoints 503 without `FALKORDB_URL`) |
| LLMs | NVIDIA models only via OpenAI-compatible API (`openai` SDK, one client class in `app/llm/`); every model id must start with `nvidia/` (validated at startup) |
| Embeddings | `nvidia/nemotron-3-embed-1b` (2048-d, L2-normalized, cosine) via NVIDIA NIM `/v1/embeddings` |
| Reranker | NVIDIA cross-encoder NIM |
| File storage | `StorageBackend` protocol; local disk (`STORAGE_DIR`) in dev, Cloudflare R2 (`R2_*`) in production |
| Console | Next.js 16 App Router, React 19, Tailwind 4, TypeScript (client-side fetching, no server components) |
| Public site | Next.js 16, static, no API calls |
| Tests | pytest against a **real PostgreSQL** (never SQLite — schema needs native UUIDs, `gen_random_uuid()`, ON DELETE semantics) |

**Layering rule:** routes are thin (resolve params → call a service); all
business logic and ownership checks live in `services/` so a new endpoint
cannot bypass them. Only `app/llm/` imports the OpenAI SDK. Automated tests
use fakes and never call a provider (no credits spent).

---

## 4. The processing pipeline (what was built, stage by stage)

### Stage 0 — Foundation (2026-09-09 → 09-23)
- Full ownership-chain CRUD (tenants, applications, credentials, actors,
  subjects, sources) with `Scope`-based ownership enforcement on every read/write.
- Google-only sign-in for console users (HS256 JWT session cookie, 7 days);
  API credentials for client apps (HMAC-SHA256 token hashing keyed by
  `API_SECRET` — indexed lookup, no per-row salt; rotating `API_SECRET`
  invalidates all credentials).
- Nested subjects as folders: adjacency list (`parent_subject_id`),
  recursive CTEs for ancestors/subtree/cycle-check, sibling-unique
  `external_id` via two partial unique indexes, subtree delete removes bytes.
- File upload (≤50 MiB, any type), metadata-only URL/chat registration,
  file move, content streaming. Write order: scope-check → bytes → row
  (rollback deletes bytes on failure); delete order: row → bytes.

### Stage 1 — Extraction Agent (2026-09-24, `docs/extraction-agent.md`)
- `Source → Document Processor (local parsing + per-page triage, no LLM cost) → Extraction Agent (NVIDIA layout/vision/text models) → source_extractions`.
- Runs only when asked: `extract=true` on upload, `POST /sources/{id}/extractions`,
  or the console Extract button. Every run is a **new append-only version**.
- `source_extractions` stores: `result` (JSONB distillation for API consumers),
  `readings` (JSONB per-page raw Markdown transcription — **what chunking
  reads**, so chunks can be regenerated without re-extracting), `content`
  (merged Markdown — what the graph reads), provider/models, token counts,
  `cost_usd`.
- `extraction_usage` = cost ledger, one row per model call (including failed
  calls), shared by extraction, graph and eval judge (role + `graph_build_id`).
- Parses PDF (tables/images), images, DOCX, XLSX, PPTX, plain text.

### Stage 2 — Chunking (2026-09-25, `docs/chunking.md`)
- Pure and local — no LLM, no cost. Runs automatically after a successful
  extraction (`chunk_on_extract=true`) or via `POST /sources/{id}/rechunk`.
- Pipeline: `readings → document_units → semantic_blocks → retrieval_chunks`.
- **Vocabulary (important):** `ReadingUnit` = a page/slide/sheet routed to a
  model (extraction layer, transient — renamed from `DocumentUnit` to avoid
  the name clash); `DocumentUnit` = a heading/text/list/table/figure/key_value
  element (chunking layer, persisted); `SemanticBlock` = units grouped per
  leaf section (consecutive heading-only blocks merge so a TOC doesn't become
  3-token chunks); `RetrievalChunk` = the final retrieval-sized piece.
- Chunk targets: 300–500 tokens, 600 soft max, 1200 hard max. A coherent
  section smaller than 300 tokens **stays small** — no padding.
- `retrieval_chunks.embedding_text` = `"Section: A > B\n\n" + content` —
  prepared here, embedded later. `token_count` counted on it (~4 chars/token).
- Versioning: chunks hang off `extraction_id`; re-chunking flips `is_active`
  on for the new version and off for all earlier ones in one transaction.
- QA tool: `backend/scripts/verify_chunking.py [source_id]` (read-only report).

### Stage 3 — Knowledge graph / GraphRAG (2026-09-25, `docs/graph-rag.md`)
- `source_extractions.content → page-aware units → one NVIDIA call per unit →
  lenient per-item validation → normalization (display name + canonical key) →
  resolution (one node per real-world thing) → FalkorDB`.
- **One FalkorDB graph per tenant.** Entity nodes are tenant-wide; a
  subject's view is filtered by provenance (facts from that subject's
  sources), covering its **subtree** by default.
- Ontology is a versioned file: `backend/ontology.json`. Unknown types map
  to a fallback, original label kept. Facts are typed edges (`[:WORKS_FOR]`)
  whitelisted against the ontology before reaching Cypher. One edge per
  (fact, source), carrying that source's chunk ids; chunk text stored on the
  Chunk node with offsets into `content`.
- Explicit builds: `POST /sources/{id}/graph` or `build_graph=true` on
  extract/upload. Every build is a `source_graph_builds` row; one extraction
  version is live per source (a full build replaces that source's prior
  facts). Graph writes happen **after** the Postgres commit, with a startup
  sweep for anything missed. Deletes/moves keep the graph in step.
- Retrieval: seeds → 1–3 deterministic hops → chunks with evidence
  (`POST /subjects/{id}/graph/query`).
- Console: `GraphPanel` (build/rebuild/retry-failed, polls status),
  `SubjectGraph` (SVG force-directed layout, subfolder toggle, "Ask the
  graph" highlighting).

### Stage 4 — Embeddings & retrieval (2026-10-05, `docs/embedding-retrieval.md`)
- **Model ≠ strategy.** A *model* is a physical embedding model
  (`nvidia/nemotron-3-embed-1b`, 2048-d, L2-normalized, cosine). A
  *strategy* is how Memora uses it (input field, templates, normalization,
  metric). Strategies are **immutable** — only description/active flag are
  editable; a materially different config is a new strategy row. Multiple
  strategies coexist for A/B. Default "Memora Dense v1" (embed
  `embedding_text` as-is) is seeded by migration.
- **Idempotency:** hash of strategy + model + exact text sent; unchanged
  content re-embeds as a no-op (no credits). Statuses:
  `pending → processing → completed | failed` (bounded retries, error kept).
- pgvector with HNSW index; coverage stats and per-source health exposed via API.
- **Query pipeline (`POST /api/v1/query`), four stages:**
  1. Vector search — HNSW top 50
  2. MMR diversification — top 10
  3. Cross-encoder reranking — top 5
  4. Grounded LLM answer with per-chunk evidence and scores
- The whole chain is scope-filtered: a query only ever sees chunks the caller owns.

### Stage 5 — Evaluation harness (uncommitted, `backend/evals/`)
- Self-contained package: **no file in `app/`, `migrations/` or `tests/` is
  modified; the database is only read (SELECT only).**
- `schemas.py` (EvalExample schema + validation + legacy mapping),
  `dataset.py` (JSONL loading, coverage, checksums), `metrics.py` (pure
  metric functions), `adapter.py` (runs the real pipeline stages for one
  query), `reports.py` (JSON + Markdown reports), `runner.py` (CLI:
  `python -m evals.runner validate|coverage|retrieval|full|chunking`).
- Scripts: `generate_eval_set.py` (sample chunks → LLM-generated questions →
  JSONL draft, with `--dry-run` cost preview), `review_eval_set.py`
  (interactive approve/reject/edit), `run_eval.py` (run approved questions
  through the **real** pipeline by importing the app's own stage functions),
  `compare_reports.py` (baseline vs new diff).
- Metrics: Recall@5/@10, MRR, nDCG@10, hit rate, context precision/recall,
  refusal/false-answer rate on unanswerables, latency p50/p95, cost,
  lost-at-stage (top-50 → MMR → reranker). Judge-based scoring is optional
  (`--judge`) and always labeled as LLM-judged.
- `evals/data/` and `evals/reports/` are gitignored (derived from real
  documents, may be sensitive). `datasets/example_synthetic.jsonl` is the
  committed synthetic example.
- Tests added: `test_eval_dataset.py`, `test_eval_metrics.py`,
  `test_eval_reports.py`, `test_eval_schema.py`.

---

## 5. Database tables (PostgreSQL, all UUID PKs, `created_at` timestamptz)

| Table | Purpose |
|---|---|
| `users`, `authenticated_user` | Console users (Google identity; `password_hash` is legacy from removed password login) |
| `tenants` | Top of the chain; `user_owner_id → users.id` |
| `applications` | Client app inside a tenant |
| `api_credentials` | `token_hash` (HMAC-SHA256), `token_preview`, expiry, status |
| `actors` | Client's own end users/agents — audit only, never ownership |
| `subjects` | Workspace/folder tree (`parent_subject_id`) |
| `sources` | Registered file/url/chat; `status` pending → processing → completed/failed |
| `source_extractions` | Versioned extraction runs: `result`, `readings`, `content`, models, tokens, `cost_usd` |
| `extraction_usage` | Cost ledger per model call (shared with graph + judge) |
| `document_units` | Fine-grained elements (kind, content, page, heading_level, section_path) |
| `semantic_blocks` | Units grouped per leaf section, with page range + full text |
| `retrieval_chunks` | Final chunks: `embedding_text`, `token_count`, `content_type`, provenance, `is_active` |
| `embedding_strategies` | Immutable model+templates+metric configs (versioned, swappable) |
| `chunk_embeddings` | Vectors (pgvector), status, idempotency hash, retry count |
| `source_graph_builds` | One row per graph build (status, counts, failed chunks) |
| `alembic_version` | Currently `cd75bae887c4` (merge of graph-rag `b6d4e8f1a2c3` and embedding `d4e5f6a7b8c9` branches) |

Migration chain: `a74a8f05366b` (base) → `5d39b5ba2882` (tenant owner) →
`c3f1a9d47b52` (avatar) → `e8b2c4f7a913` (nested subjects) →
`f4c1d2e3b5a6` (extractions) → `a1b2c3d4e5f6` (readings) /
`a9d3e5f7c2b1` (usage price) → `b2c3d4e5f6a7` (chunking tables) →
`d4e5f6a7b8c9` (embedding tables) / `b6d4e8f1a2c3` (graph builds) →
`cd75bae887c4` (merge).

---

## 6. API surface (`/api/v1`)

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/google`, `GET /auth/me`, `POST /auth/logout` |
| Tenants | `POST/GET /tenants`, `GET/PATCH /tenants/{id}` (no DELETE — deliberately, cascades are too destructive) |
| Applications | `POST/GET /tenants/{id}/applications`, `GET /applications`, `GET/PATCH /applications/{id}` |
| Credentials | `POST/GET /applications/{id}/credentials`, `GET /credentials`, `PATCH/DELETE /credentials/{id}` |
| Actors | `POST/GET /applications/{id}/actors`, `GET /actors`, `GET/PATCH /actors/{id}` |
| Subjects | `POST/GET /applications/{id}/subjects`, `GET /subjects`, `GET/PATCH/DELETE /subjects/{id}` |
| Sources | `POST/GET /subjects/{id}/sources`, `POST /subjects/{id}/sources/upload`, `GET /sources`, `GET/PATCH/DELETE /sources/{id}`, `GET /sources/{id}/content`, `POST /sources/{id}/move` |
| Extractions | `POST/GET /sources/{id}/extractions`, `GET /sources/{id}/extractions/{version}` |
| Chunks | `POST /sources/{id}/rechunk`, `GET /sources/{id}/chunks` |
| Embeddings | `GET /embedding/models`, `GET/POST /embedding/strategies`, `PATCH /embedding/strategies/{id}`, `POST /embedding/sources/{id}/embed`, `POST /embedding/subjects/{id}/embed`, `POST /embedding/strategies/{id}/rebuild`, `POST /embedding/retry-failed`, `GET /embedding/stats`, `POST /embedding/query` |
| Graph | `POST /sources/{id}/graph`, `GET /sources/{id}/graph/builds/latest`, `GET /subjects/{id}/graph`, `POST /subjects/{id}/graph/query` |
| Retrieval | `POST /query` |
| Meta | `GET /stats`, `GET /stats/metrics`, `GET /whoami`, `GET /usage/extractions`, `GET /health` |

Full request/response fields: `docs/api-reference.md`.

### Authentication (two callers, one dependency: `app/api/deps.py::get_scope`)
- **Applications:** `Authorization: Bearer memora_…` → token hashed (HMAC) →
  indexed lookup → `Scope(kind="credential")` = exactly one tenant + one
  application. The application can only ever see its own data regardless of
  the ids in the URL.
- **Console users:** `memora_session` HttpOnly cookie (HS256 JWT, 7 days),
  obtained only via `/auth/google` (Google tokeninfo verification) →
  `Scope(kind="user")` = every tenant where `tenants.user_owner_id = user.id`.
- No endpoint validates a token itself; everything goes through `get_scope`.
- The raw token exists in exactly two places: the creation response and
  wherever it is pasted. DB holds only the HMAC. No response schema contains
  `token_hash`.

---

## 7. Repository layout

```
backend/
  app/
    main.py               app factory, router registration
    api/deps.py           DB session, storage, get_scope
    api/routes/           thin HTTP handlers (one file per resource)
    services/             ALL business logic + Scope ownership checks
    db/models/            SQLAlchemy tables
    schemas/              Pydantic request/response models, enums
    storage/              StorageBackend protocol + local + s3 implementations
    core/                 config, security (hashing, JWT), errors, pricing
    processing/           document processor, parsers, per-page triage
    agents/               extraction agent + prompts
    llm/                  the ONLY place the OpenAI SDK is imported
    chunking/             units, blocks, chunks, structure, representation
    graph/                chunking, extraction, normalization, resolution,
                          store (FalkorDB), retrieval, sync, ontology, models
    embeddings/           provider (NIM), strategy
    retrieval/            mmr, reranker
  migrations/versions/    Alembic revisions (head: cd75bae887c4)
  evals/                  evaluation harness (uncommitted; see §4 stage 5)
  scripts/verify_chunking.py
  tests/                  pytest suite (real PostgreSQL required)
  ontology.json           versioned graph ontology
  pricing.json            model price list (cost estimation)
console/
  src/app/                login, tenants, applications, credentials, actors,
                          subjects, sources, chunks, embeddings, ask
  src/components/         Shell, DataTable, modals, form, charts, SubjectTree,
                          FileExplorer, ExtractionPanel, GraphPanel,
                          SubjectGraph, EmbeddingPanel, UsageCard
  src/lib/                api.ts (typed client), types.ts, useResource.ts, format.ts
web/
  src/app/                landing page + docs/* (static API reference)
docs/                     internal design records (see §12)
```

---

## 8. Running it

Requires Python 3.12+, Node 20.9+, PostgreSQL (Neon or local container).

```bash
# Database (local option)
docker run -d --name memora-pg \
  -e POSTGRES_USER=memora -e POSTGRES_PASSWORD=memora -e POSTGRES_DB=memora \
  -p 5432:5432 postgres:16-alpine

# Backend (:8000)
cd backend
cp .env.example .env        # set DATABASE_URL (postgresql+psycopg://…), API_SECRET,
                            # NVIDIA_API_KEY, FALKORDB_URL, R2_* (prod), ENVIRONMENT
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload

# Console (:3000)
cd console && cp .env.example .env.local && npm install && npm run dev

# Public site (:301)
cd web && npm install && npm run dev
```

- Generate a real `API_SECRET`: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
  Rotating it invalidates every issued credential.
- `ENVIRONMENT=local` → build.nvidia.com + local disk; `production` → Nebius
  (text models) + R2 storage. Nebius serves only text NVIDIA models, so
  vision/layout/embeddings/rerank stay on build.nvidia.com in production.
- **Use `localhost` (or add your host to `allowedDevOrigins` in
  `console/next.config.ts` and `CORS_ORIGINS` in backend).** Next 16 refuses
  unrecognized dev origins — the HMR websocket gets a bare `Unauthorized`,
  hydration stops, and every panel sits on "Loading…" forever with nothing in
  the terminal. `localhost` and `127.0.0.1` are different origins to a browser.

### Tests

```bash
cd backend
TEST_DATABASE_URL=postgresql+psycopg://… .venv/bin/python -m pytest tests/ -q
```

- Runs against a **real PostgreSQL, not SQLite** (native UUIDs,
  `gen_random_uuid()`, ON DELETE semantics). **Point it at a throwaway
  database** — fixtures truncate every table between tests.
- ~40+ test files covering: MVP flow end-to-end, ownership isolation,
  credentials, validation, nested subjects, auth/Google, document processor,
  extraction agent (fakes, no credits), extractions API, graph pipeline and
  units, graph API, chunking, LLM client, pricing, storage, eval schema/
  dataset/metrics/reports.

---

## 9. Key design decisions (the "why")

1. **HMAC-SHA256 token hashing, not bcrypt/argon2** — auth must answer "which
   credential is this?" from the token alone → indexed lookup; per-row salts
   would force a full scan. Tokens are 256 random bits, so slow hashing buys
   nothing. A DB dump alone cannot forge tokens (`API_SECRET` never touches
   the DB).
2. **Status columns are VARCHAR, not PG ENUM** — adding a status is an app
   change, not a type-rewriting migration. Pydantic enforces values at the edge.
3. **Folders are subjects** (adjacency list, not a separate `folders` table) —
   one concept reuses the ownership chain, scope checks, routes and console.
4. **Recursive CTEs, not Python loops** for ancestor/subtree/cycle queries.
5. **`DELETE /credentials/{id}` really deletes** — a revoked-but-present row
   keeps a dead hash in the unique index. Reversible suspension stays via PATCH.
6. **No `DELETE /tenants/{id}`** — cascades reach sources; too destructive for
   a convenience endpoint.
7. **Console is a plain API client** (client-side fetching via one typed client
   `src/lib/api.ts` + one `useResource` hook) — genuine loading/error/empty
   states implemented once; deployable as a static bundle.
8. **Extraction is explicit and versioned** — never automatic; every run is a
   new append-only version with its cost recorded. `readings` persisted so
   chunking never re-extracts; `content` persisted so the graph never re-extracts.
9. **Chunking is pure/local** — no LLM, no cost, regenerable at will.
10. **Strategies are immutable** — every vector knows exactly which strategy
    produced it; a model change can never silently corrupt existing results.
11. **Graph after commit** — Postgres is the source of truth; FalkorDB is
    derived, with a startup sweep for missed writes.
12. **NVIDIA-only guard** — every configured model id must start with
    `nvidia/`; settings validation fails at startup otherwise.
13. **Next 16, not 15** — separates `.next/dev` from `.next/build` so a
    concurrent build no longer breaks a running dev server. `next lint` was
    removed in Next 16; there is no ESLint config.

---

## 10. Current work state (as of 2026-10-09)

**Committed** (`main` @ `629f371`): everything in §4 stages 0–4, plus docs
updates for embedding/retrieval and the API reference.

**Uncommitted / working tree:**
- `backend/evals/` — the full evaluation harness (§4 stage 5): schemas,
  dataset, metrics, adapter, reports, runner CLI, 4 scripts, synthetic example
  dataset, README.
- `backend/tests/test_eval_{dataset,metrics,reports,schema}.py` — eval tests.
- `.gitignore` — added `backend/evals/data/` and `backend/evals/reports/`
  (derived from real documents, may be sensitive).
- `backend/Untitled` — stray file, probably deletable.

**Known gaps / not built (by design):**
- No background workers/queues, no Redis, no Docker Compose, no S3/MinIO
  (S3 backend exists for R2; local disk is the dev default).
- No per-subject actor permissions — the `subject_actors` table is designed
  but not built.
- No hybrid (lexical/tsvector) retrieval — vector-only today.
- No LLM-generated Cypher, community detection or summaries in the graph.
- No Memory Agent / conversational multi-turn memory yet.
- `AUTH_JWT_SECRET` missing from `backend/.env.example` — silently defaults to
  `"change-me-in-production"` (unsafe default; fix before real use).
- Upload has no file-type allow-list; size limit only enforced when
  `file.size` is known.
- README still mentions an `ADMIN_API_KEY` / `X-Admin-Key` console login that
  was removed from the code (console now uses the Google session cookie).

---

## 11. Conventions to follow when extending

- New endpoints: thin route → service function that takes a `Scope`
  (`app/services/scope.py`) and asserts ownership. 404, never 403.
- New tables: UUID PK + `created_at` (mixin), denormalized
  `tenant_id/application_id/subject_id/source_id` where applicable, cascade
  from `sources`, status as VARCHAR validated by Pydantic.
- New LLM roles: go through `app/llm/client.py`, model id must be `nvidia/…`,
  cost recorded in `extraction_usage`, prices in `backend/pricing.json`.
- Tests: fakes for anything calling a provider; never spend credits in CI.
- Eval work: stays inside `backend/evals/`, read-only against the DB.
- Docs: design records go in `docs/` as `*.md`; update
  `docs/current-state.md` and `docs/data-flow.md` when a stage lands.

---

## 12. Where the deeper documentation lives

| File | Contents |
|---|---|
| `README.md` | Setup, running, API summary, auth, ownership rules |
| `docs/current-state.md` | Snapshot of what exists, timeline of commits, questionnaire answers |
| `docs/decisions.md` | Foundation design decisions and the "not built" list |
| `docs/data-flow.md` | Every table in creation order, upload → chunks |
| `docs/extraction-agent.md` | Extraction stage design (models, routing, versioning, cost) |
| `docs/chunking.md` | Chunking strategy, vocabulary, targets, versioning |
| `docs/graph-rag.md` | GraphRAG design (ontology, FalkorDB model, retrieval) |
| `docs/embedding-retrieval.md` | Vector pipeline concepts, strategies, query stages |
| `docs/api-reference.md` | Full HTTP reference with request/response fields |
| `backend/evals/README.md` | Evaluation harness usage, schema, metrics, CLI |
