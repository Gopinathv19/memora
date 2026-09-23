# Memora — current state

A snapshot of what exists in this repository as of **2026-09-23** (`main` @ `3dc302e`).

> **Update 2026-09-24:** the Extraction Agent has since been built on top of
> this — see [extraction-agent.md](extraction-agent.md). Sections below describe
> the foundation it sits on; where they say "no LLM / nothing advances a source",
> that is now superseded by the extraction stage.

It describes only what is implemented. For the reasoning behind the design see
[decisions.md](decisions.md); for setup and running see the [README](../README.md).

---

## 1. What Memora is

Memora is meant to be the core service that persists memory for applications
needing durable, retrievable context. Client applications register files, URLs
or chat transcripts against a workspace (a *Subject*), and a later phase will
turn those into documents, chunks, vectors and a knowledge graph.

**Today the chain stops at `Source`.** Memora stores the uploaded bytes and a
metadata row with `status = "pending"`. Nothing parses, extracts, chunks,
embeds or answers questions yet.

---

## 2. Architecture

```text
                                   CALLERS
 ┌────────────────────────┐  ┌──────────────────────────┐  ┌────────────────────────┐
 │ Console (Next.js 16)   │  │ Client application       │  │ Public site (Next.js)  │
 │ :3000                  │  │ (any external app)       │  │ :3001                  │
 │ login, tenants, apps,  │  │                          │  │ landing + static API   │
 │ SubjectTree,           │  │                          │  │ reference (/docs/*)    │
 │ FileExplorer           │  │                          │  │ no API calls           │
 └───────────┬────────────┘  └────────────┬─────────────┘  └────────────────────────┘
             │ cookie memora_session       │ Authorization: Bearer memora_…
             │ (JWT HS256, 7 days)         │ (API credential)
             ▼                             ▼
 ┌──────────────────────────────────────────────────────────────────────────────────┐
 │                          FastAPI backend  /api/v1  (:8000)                        │
 │                                                                                   │
 │  AUTH LAYER   api/deps.py → get_scope()                                           │
 │   ├─ "memora_…" token → credential_service.authenticate_token (HMAC-SHA256)       │
 │   │                      → Scope(kind="credential", one tenant + one application) │
 │   └─ session cookie   → auth_services (bcrypt / Google tokeninfo)                 │
 │                          → Scope(kind="user", every tenant the user owns)         │
 │   Rows outside the scope → 404                                                    │
 │                                                                                   │
 │  ROUTES    api/routes/                                                            │
 │   auth · dashboard · tenants · applications · credentials · actors ·              │
 │   subjects · sources · /health                                                    │
 │                                                                                   │
 │  SERVICES  services/   business logic + ownership checks via Scope                │
 │   subject_service → folder tree (recursive CTEs, cycle check, subtree delete)     │
 │   source_service  → register / upload / move / delete / stream content            │
 │                                                                                   │
 │  SCHEMAS   schemas/ (Pydantic)        CORE  core/ (config, security, errors)      │
 └───────────────┬──────────────────────────────────────────────┬───────────────────┘
                 │ SQLAlchemy 2 + psycopg3, Alembic              │ StorageBackend protocol
                 ▼                                               ▼
 ┌──────────────────────────────────────┐        ┌──────────────────────────────────┐
 │ PostgreSQL (Neon or local)           │        │ LocalStorageBackend              │
 │                                      │        │ STORAGE_DIR (./var/storage)/     │
 │ users ─ authenticated_user           │        │   {tenant_id}/{subject_id}/      │
 │ tenants (user_owner_id → users)      │        │     {uuid}-{filename}            │
 │   └─ applications                    │        │ sources.storage_uri = file://…   │
 │        ├─ api_credentials            │        │ (no S3 / MinIO implementation)   │
 │        ├─ actors                     │        └──────────────────────────────────┘
 │        └─ subjects                   │
 │             (parent_subject_id tree) │         Outbound HTTP: Google tokeninfo
 │              └─ sources              │         (sign-in verification only)
 │                 status = "pending"   │
 └──────────────────────────────────────┘

 NOT BUILT: parser/OCR · LLM extraction · chunking · embeddings · pgvector ·
            graph DB · chat/search endpoint · queue/worker · Redis · S3
```

### Technology stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, Uvicorn, SQLAlchemy 2, psycopg 3, Alembic, Pydantic v2, pydantic-settings |
| Auth libraries | PyJWT (HS256), bcrypt, httpx (Google tokeninfo) |
| Database | PostgreSQL (Neon in practice, or local `postgres:16-alpine`) |
| File storage | Local filesystem behind a `StorageBackend` protocol |
| Console | Next.js 16 App Router, React 19, Tailwind 4, TypeScript |
| Public site | Next.js 16 (static landing page + API reference) |
| Tests | pytest (32 tests across 5 files) |

Not present: any LLM SDK, OCR/PDF/docx parser, vector library, graph database,
Redis, message queue, S3 client, Docker Compose.

---

## 3. Ownership chain and data model

```text
User ──owns──► Tenant ──► Application ──► Actor
                                  │
                                  ├──► ApiCredential
                                  │
                                  └──► Subject ──► Subject (child folder) ──► …
                                          │
                                          └──► Source (file | url | chat)
```

Every table uses a UUID primary key (`gen_random_uuid()`) and `created_at`
(`timestamptz`, default `now()`), from `backend/app/db/models/mixins.py`.

| Table | Key columns | Notes |
|---|---|---|
| `users` | `email` (unique), `email_verified`, `name`, `avatar_url` | A console user |
| `authenticated_user` | `user_id`, `provider`, `provider_user_id`, `password_hash` | One row per login method (password or Google) |
| `tenants` | `name`, `user_owner_id` → `users.id`, `status` | The top of the ownership chain |
| `applications` | `tenant_id`, `name`, `slug`, `status` | A client app inside a tenant |
| `api_credentials` | `application_id`, `name`, `token_hash`, `token_preview`, `last_used_at`, `expires_at`, `status` | Only the HMAC hash of the token is stored |
| `actors` | `tenant_id`, `application_id`, `external_id`, `type` (user/service/agent/system), `name`, `status` | The client's own identity for who did something; a label, not an access rule |
| `subjects` | `tenant_id`, `application_id`, `actor_id` (created by), `external_id`, `parent_subject_id`, `status` | A workspace; when `parent_subject_id` is set, it is a folder |
| `sources` | `tenant_id`, `application_id`, `subject_id`, `created_by_actor_id`, `type`, `mime_type`, `filename`, `storage_uri`, `size_bytes`, `status` | A registered file, URL or chat |

**Subjects as folders**
- Folders are stored as an adjacency list on the `subjects` table.
- `external_id` must be unique among siblings. Two partial unique indexes enforce this: one for roots and one for children of the same parent.
- Ancestor paths and subtrees are read with recursive CTEs (`services/subject_service.py`).
- `PATCH /subjects/{id}` renames or moves a folder. A move that would create a cycle is rejected.
- Deleting a subject removes its whole subtree and the stored bytes of every source inside it.

**Status values** (`backend/app/schemas/enums.py`)
- **Resources:** `active`, `suspended`, `revoked`.
- **Sources:** `pending`, `processing`, `completed`, `failed`. Nothing moves a source past `pending` yet; only a manual `PATCH` can change it.

**Migrations** (`backend/migrations/versions/`)
1. `a74a8f05366b_created_the_user_and_auth_user`: the full base schema.
2. `5d39b5ba2882_add_tenant_owner`: adds `tenants.user_owner_id`.
3. `c3f1a9d47b52_add_user_avatar_url`: adds `users.avatar_url`.
4. `e8b2c4f7a913_nested_subjects`: adds `subjects.parent_subject_id` and the sibling-unique indexes.

---

## 4. Authentication model

There are exactly two ways to call the API. `api/deps.py → get_scope()` decides
which one a request uses.

| Caller | How it authenticates | Resulting scope |
|---|---|---|
| **API credential** (client application) | `Authorization: Bearer memora_<32 random bytes>` | `Scope(kind="credential")`: exactly one tenant and one application |
| **Console user** | `memora_session` HttpOnly cookie holding an HS256 JWT, valid 7 days. Obtained through `/auth/signup`, `/auth/login` (bcrypt) or `/auth/google` (tokeninfo). | `Scope(kind="user")`: every tenant where `tenants.user_owner_id = user.id` |

**Rules**
- A row outside the caller's scope returns **404, never 403**, so its existence isn't revealed.
- Tokens are stored as `HMAC-SHA256(API_SECRET, token)`. The raw token is shown once, when it is created.
- `/whoami` requires an API credential.

---

## 5. API surface (`/api/v1`)

| Area | Routes |
|---|---|
| Auth | `POST /auth/signup`, `POST /auth/login`, `POST /auth/google`, `GET /auth/me`, `POST /auth/logout` |
| Dashboard | `GET /stats`, `GET /stats/metrics`, `GET /whoami` |
| Tenants | `POST /tenants`, `GET /tenants`, `GET/PATCH /tenants/{id}` |
| Applications | `POST/GET /tenants/{id}/applications`, `GET /applications`, `GET/PATCH /applications/{id}` |
| Credentials | `POST/GET /applications/{id}/credentials`, `GET /credentials`, `PATCH/DELETE /credentials/{id}` |
| Actors | `POST/GET /applications/{id}/actors`, `GET /actors`, `GET/PATCH /actors/{id}` |
| Subjects | `POST/GET /applications/{id}/subjects`, `GET /subjects`, `GET/PATCH/DELETE /subjects/{id}` |
| Sources | `POST /subjects/{id}/sources` (metadata only), `POST /subjects/{id}/sources/upload`, `GET /subjects/{id}/sources[/{sid}]`, `GET /sources`, `GET/PATCH/DELETE /sources/{id}`, `GET /sources/{id}/content`, `POST /sources/{id}/move` |
| Health | `GET /health` |

There is no chat, ask, query or search endpoint.

---

## 6. Request flows

### Upload a file
```text
POST /api/v1/subjects/{subject_id}/sources/upload   (multipart: file, created_by_actor_id?)
  │
  ├─ get_scope()                       → credential or user scope
  ├─ size check                        → 413 if > 50 MiB (MAX_UPLOAD_BYTES)
  └─ source_service.create_source_from_upload()
       ├─ get_subject()                → subject must be inside the scope, else 404
       ├─ _resolve_actor_id()          → actor must belong to the same application
       ├─ build_key()                  → {tenant_id}/{subject_id}/{uuid}-{safe_filename}
       ├─ storage.put()                → bytes written to local disk
       └─ INSERT sources(type="file", status="pending", storage_uri="file://…")
            (if the commit fails: roll back and delete the stored file)
  ▼
201 → SourceRead JSON.  END — no parsing, OCR, extraction, queue or worker.
```
- Any file type is accepted: there is no MIME or extension allow-list.
- The client's `content_type` is stored as `mime_type`.

### Other flows
- **Register without bytes:** `POST /subjects/{id}/sources` records a URL or chat source with metadata only.
- **Move a file:** `POST /sources/{id}/move` updates a single row (`subject_id`). The bytes are not moved.
- **Move or rename a folder:** `PATCH /subjects/{id}` with `parent_subject_id` and/or `external_id`. Cycle checks and sibling-name checks apply.
- **Delete a source:** the row is deleted, then the file is removed from disk after the commit.
- **Delete a subject:** the whole subtree is removed, together with the stored bytes of every source in it.
- **Read content:** `GET /sources/{id}/content` streams the original file back.

---

## 7. Repository layout

```text
memora/
├── backend/                    FastAPI service
│   ├── app/
│   │   ├── main.py             app factory, router registration
│   │   ├── api/deps.py         DB session, storage, get_scope
│   │   ├── api/routes/         thin HTTP handlers (one file per resource)
│   │   ├── services/           business logic + Scope ownership checks
│   │   ├── db/models/          SQLAlchemy tables
│   │   ├── schemas/            Pydantic request/response models, enums
│   │   ├── storage/            StorageBackend protocol + LocalStorageBackend
│   │   └── core/               config, security (hashing, JWT), errors
│   ├── migrations/             Alembic (4 revisions)
│   ├── tests/                  pytest
│   └── var/storage/            uploaded files (local, git-ignored)
├── console/                    Next.js admin console (:3000)
│   └── src/
│       ├── app/                pages: login, tenants, applications, credentials,
│       │                       actors, subjects, sources (+ detail pages)
│       ├── components/         Shell, DataTable, modals, form, charts,
│       │                       SubjectTree, FileExplorer
│       └── lib/                api.ts, types.ts, useResource.ts, format.ts
├── web/                        Next.js public site (:3001)
│   └── src/app/docs/           quickstart, authentication, tenants, applications,
│                               credentials, actors, subjects, sources, errors
└── docs/
    ├── decisions.md            design decisions and the "Not built" list
    └── current-state.md        this file
```

---

## 8. What has been built so far

### Timeline

| Date | Commit | Work |
|---|---|---|
| 2026-09-09 | `d0d2c9d`, `ef0e2bb` | Initial commit; planning and iteration |
| 2026-09-11 | `3c7df89` | First rollout of the backend skeleton |
| 2026-09-15 | `92b29e3` | Alembic migrations set up properly |
| 2026-09-17 | `e745b28` | `users` table |
| 2026-09-19 | `f79c0e4` | `tenants.user_owner_id` → `users.id` |
| 2026-09-20 | `d8db7a9` | `config.py` and `security.py` (settings, hashing, JWT) |
| 2026-09-22 | `e212542` | User-based authentication (signup/login/Google, session cookie) |
| 2026-09-22 | `95fc19c` | Frontend changes |
| 2026-09-22 | `fae2bbd` | "Memora base done": `frontend/` renamed to `console/`, form components, public `web/` site with the full API reference |
| 2026-09-23 | `c489036` | Nested folders: `parent_subject_id`, recursive CTEs, move/cycle checks, file move, migration `e8b2c4f7a913`, `test_nested_subjects.py` |
| 2026-09-23 | `836a3d8` | Console `SubjectTree` and `FileExplorer`, subject/source docs pages |
| 2026-09-23 | `3dc302e` | PR #1 (`initiall-rollout`) merged into `main` |

### Feature summary
- **Accounts and auth:** email/password and Google sign-in for console users, with a session cookie. Scoped API credentials for client apps. Credentials can be revoked and expire.
- **Ownership chain:** CRUD for tenants, applications, credentials, actors, subjects and sources, with every query scoped to what the caller owns.
- **Workspaces as a folder tree:** nested subjects, with rename, move (cycle-safe) and delete (subtree plus bytes).
- **File registration:** upload (up to 50 MiB, any type), metadata-only registration, file move between folders, content streaming, delete.
- **Console:** dashboard with stats and metrics, list and detail pages per resource, a hierarchical `SubjectTree`, and a `FileExplorer` for browsing a subject.
- **Public API reference:** static docs pages for every resource, the quickstart, authentication and errors.

### Tests (`backend/tests/`, 32 tests)

| File | Tests | Covers |
|---|---|---|
| `test_mvp_flow.py` | 2 | End-to-end: tenant → application → credential → actor → subject → upload |
| `test_ownership.py` | 6 | Cross-tenant and cross-application isolation (404s) |
| `test_credentials.py` | 6 | Token issue, hashing, revoke, expiry |
| `test_validation.py` | 10 | Request validation rules |
| `test_nested_subjects.py` | 8 | Folder tree, sibling uniqueness, moves, cycles, subtree delete |

---

## 9. Not built yet and known gaps

**Built since:** the Extraction Agent — see
[extraction-agent.md](extraction-agent.md).

**Not built (by design, next phase):**
- **Ingestion:** no parsing or OCR of uploaded files, and no LLM extraction.
- **Retrieval:** no Document or Chunk tables, no chunking, no embeddings, no pgvector or other vector store, no graph database.
- **Answering:** no chat, ask or search endpoint, and no LLM calls of any kind.
- **Infrastructure:** no background workers or queues, no Redis, no S3/MinIO backend, no Docker Compose.
- **Permissions:** no per-subject permissions for actors. The `subject_actors` table is designed but not built.

**Known gaps and inconsistencies:**
- **Shared files:** commit `c489036` mentions a "shared files concept", but no column, table, endpoint or UI for sharing exists.
- **Stale docs:** the README and `backend/.env.example` still describe an `ADMIN_API_KEY` / `X-Admin-Key` console login. It has been removed from the code: `Settings` ignores it, and the console uses the session cookie.
- **Unsafe default:** `AUTH_JWT_SECRET` is missing from `backend/.env.example`, so it silently defaults to `"change-me-in-production"`.
- **Upload limits:** there is no file-type allow-list on upload, and the size limit is only enforced when `file.size` is known.

---

## 10. Questionnaire answers

Answers to "what does the current application do", written for planning where
Graph, Chunks and Embeddings will fit.

### 1. What is the application supposed to do?
- **Input:** a client application, using an API credential, gives Memora files, URLs or chat transcripts, registered against a workspace (Subject) that can be nested into folders.
- **Planned processing:** turn them into Documents → Chunks → vectors and a knowledge graph.
- **Today's processing:** the file is stored and a metadata row is recorded.
- **Output today:** the source metadata and the original file. There is no retrieval yet.

### 2. What inputs are supported?
```text
[~] PDF          accepted and stored, not parsed
[~] Images       accepted and stored, not parsed
[~] Text/chat    "chat" / "url" source types exist, metadata-only registration
[~] Word docs    accepted and stored, not parsed
[~] Audio        accepted and stored, not parsed
[x] Parsing of any kind — none
```

### 3. What happens immediately after input?
```text
Upload file → auth scope check → size check → save to local disk
            → INSERT sources(status="pending") → END
```
See [section 6](#6-request-flows) for the detailed trace.

### 4. What does the extraction agent produce?
No extraction agent exists. The only output is the source row:
```json
{
  "id": "…uuid…",
  "tenant_id": "…uuid…",
  "application_id": "…uuid…",
  "subject_id": "…uuid…",
  "created_by_actor_id": null,
  "type": "file",
  "filename": "complaint.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 27,
  "storage_uri": "file://<tenant>/<subject>/<uuid>-complaint.pdf",
  "status": "pending",
  "created_at": "2026-09-23T10:00:00Z"
}
```

### 5. Where is extracted information stored?
Nothing is extracted. Metadata lives in PostgreSQL. See [section 3](#3-ownership-chain-and-data-model).

### 6. Is the original document stored?
Yes, on local disk under `STORAGE_DIR/{tenant}/{subject}/{uuid}-{filename}`.
- It is kept until the source or its subject is deleted.
- `sources.storage_uri` points to it.

### 7. Chunks?
No chunks currently.

### 8. Embeddings?
No embeddings currently: no model, no vector database, nothing is embedded.

### 9. How does the application answer a question?
```text
User: "What is my address?"  →  no endpoint, no retrieval, no LLM  →  cannot answer today
```

### 10. Technologies already implemented
PostgreSQL, Alembic, local filesystem storage, FastAPI, Next.js, JWT/bcrypt/HMAC
auth. No pgvector, graph database, Redis, S3 or queue.

### 11. Where does the LLM have control?
```text
No LLM is integrated.   ✗ extract   ✗ answer   ✗ tools   ✗ write DB
```

### 12. Current architecture
See [section 2](#2-architecture).

### Where the next phase attaches
The `sources.status` lifecycle (`pending → processing → completed | failed`) is
already part of the API contract. A processor can pick up `pending` sources,
read the bytes through `StorageBackend.open()`, and write the Document, Chunk,
embedding and graph data keyed by `source_id`. That inherits `tenant_id`,
`application_id` and `subject_id` for scoping without changing the existing
ownership chain.
