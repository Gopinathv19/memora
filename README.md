# Memora

The core service that persists memory for applications needing durable,
retrievable context.

This repository currently contains the **console and registration foundation**:
a Next.js operations console and a FastAPI backend over Neon PostgreSQL that
model who owns what, and let a file be registered and stored against the right
owner, plus the first processing stages: the Extraction Agent and the
knowledge graph (see below). Embeddings and vector search are the next phase.

## The ownership chain

Everything in Memora hangs off one chain. Reading it top to bottom is the whole
data model:

```
Tenant              the organization that owns the data      Acme Law Firm
  └── Application   the consuming application                LegalCase
        └── Actor   who or what operates on it               lawyer_123
              └── Subject   the workspace / data boundary    case-ABC-456
                    └── Source   registered knowledge        complaint.pdf
```

Two distinctions matter and are easy to lose:

- **An actor is not a subject.** The actor is who *caused* a workspace to
  exist; the subject is the workspace itself. A lawyer opens a case; the case
  holds the documents.
- **`actor_id` on a subject means "created by", not "may access".** Several
  people and agents will work inside one case. Per-actor authorization, when it
  is needed, belongs in a separate `subject_actors` table — designed for, not
  built yet.

Memora also does not own your identity system. An actor stores your own
identifier (`external_id`) and nothing else: no email, no password, no role.

## Repository layout

```
backend/                 FastAPI + SQLAlchemy + Alembic
  app/
    core/                config, token hashing, domain errors
    db/models/           the six tables
    schemas/             Pydantic request/response contracts
    services/            all business logic and ownership enforcement
    api/routes/          thin HTTP handlers that call services
    storage/             StorageBackend interface + local-disk implementation
  migrations/            Alembic revisions
  tests/                 24 tests over a real PostgreSQL

console/                 Next.js 16 App Router + TypeScript + Tailwind 4
  src/lib/               typed API client, fetch hooks, formatters
  src/components/        console shell, table, forms, create modals
  src/app/               one route per resource, list + detail
```

Routes are deliberately thin: they resolve parameters and call a service.
Ownership checks live in the service layer so they cannot be bypassed by a new
endpoint that forgets them.

## Running it

You need Python 3.12+, Node 20.9+, and a PostgreSQL database (Neon, or a local
container). The console is on Next 16, which builds with Turbopack and requires
React 19.

### 1. Database

Create a Neon project and copy its connection string, changing the driver
prefix to `postgresql+psycopg://`:

```
postgresql+psycopg://USER:PASSWORD@ep-xxx.region.aws.neon.tech/memora?sslmode=require
```

For a purely local database instead:

```bash
docker run -d --name memora-pg \
  -e POSTGRES_USER=memora -e POSTGRES_PASSWORD=memora -e POSTGRES_DB=memora \
  -p 5432:5432 postgres:16-alpine
```

### 2. Backend

```bash
cd backend
cp .env.example .env          # then set DATABASE_URL and API_SECRET
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

The API is on http://localhost:8000, with interactive docs at `/docs`.

Generate a real `API_SECRET` before doing anything you care about:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

It is the key that API tokens are hashed with. Rotating it invalidates every
issued credential.

### 3. Console

```bash
cd console
cp .env.example .env.local    # NEXT_PUBLIC_API_BASE_URL defaults to :8000
npm install
npm run dev
```

The console is on http://localhost:3000.

**Use `localhost`, or add your host to `allowedDevOrigins`.** Next 16 refuses
dev requests — including the HMR websocket — from origins it does not
recognize, answering with a bare `Unauthorized`. Because the HMR client is part
of the Turbopack dev runtime, that refusal stops hydration completely: the page
renders, then every panel sits on "Loading…" forever with nothing in the
terminal to explain it. `localhost` and `127.0.0.1` are different origins to a
browser, so both are listed in `next.config.ts`. If you open the console from
another machine or a container host, add that origin there too.

### Tests

```bash
cd backend
TEST_DATABASE_URL=postgresql+psycopg://... .venv/bin/python -m pytest tests/ -q
```

The suite runs against a real PostgreSQL, not SQLite — the schema relies on
native UUIDs, `gen_random_uuid()` and `ON DELETE` behaviour that SQLite cannot
represent, so testing on it would verify something other than what ships.
**Point it at a throwaway database:** the fixtures truncate every table between
tests.

## API

All endpoints are under `/api/v1`. Nested paths express ownership — an
application is created inside a tenant, a subject inside an application, a
source inside a subject.

| Resource | Endpoints |
|---|---|
| Tenants | `POST/GET /tenants`, `GET/PATCH /tenants/{id}` |
| Applications | `POST/GET /tenants/{id}/applications`, `GET /applications`, `GET/PATCH /applications/{id}` |
| Credentials | `POST/GET /applications/{id}/credentials`, `GET /credentials`, `PATCH/DELETE /credentials/{id}` |
| Actors | `POST/GET /applications/{id}/actors`, `GET /actors`, `GET/PATCH /actors/{id}` |
| Subjects | `POST/GET /applications/{id}/subjects`, `GET /subjects`, `GET/PATCH /subjects/{id}` |
| Sources | `POST/GET /subjects/{id}/sources`, `POST /subjects/{id}/sources/upload`, `GET /sources`, `GET/PATCH/DELETE /sources/{id}`, `GET /sources/{id}/content` |
| Meta | `GET /stats`, `GET /whoami`, `GET /health` |

### Registering a file

Two ways, both landing in `pending`:

```bash
# Post the bytes. Memora stores them and returns a tracked source row.
curl -X POST http://localhost:8000/api/v1/subjects/$SUBJECT_ID/sources/upload \
  -H "Authorization: Bearer memora_..." \
  -F "file=@complaint.pdf;type=application/pdf" \
  -F "created_by_actor_id=$ACTOR_ID"

# Or record a source whose content lives elsewhere, moving no bytes.
curl -X POST http://localhost:8000/api/v1/subjects/$SUBJECT_ID/sources \
  -H "Authorization: Bearer memora_..." -H "Content-Type: application/json" \
  -d '{"type":"url","filename":"Statute reference",
       "storage_uri":"https://law.example.gov/statutes/1204"}'
```

Notice what the client does *not* send: `tenant_id` or `application_id`. Those
are copied from the subject named in the path, so a caller cannot attach a
source to a tenant it does not own.

## Authentication

There are two kinds of caller, and one dependency (`app/api/deps.py::get_scope`)
resolves both. No endpoint validates a token itself.

**Applications** send `Authorization: Bearer memora_...`. The token is hashed,
looked up, and checked for status and expiry; the credential then resolves to
its application and tenant. That pair *is* the request's scope, so an
application can only ever see its own data regardless of the ids it puts in a
URL. `GET /whoami` shows the resolution.

**The console** sends no bearer token and gets an unrestricted scope, optionally
gated by an `X-Admin-Key` header when `ADMIN_API_KEY` is set. This is
intentionally simple for the MVP: the console key reaches the browser, so it is
a speed bump, not a security boundary. Anything beyond localhost needs real
operator sign-in.

### How tokens are stored

The raw token exists in exactly two places: the response that creates it, and
wherever you paste it. The database holds only an HMAC-SHA256 of it, keyed by
`API_SECRET`.

The keyed HMAC — rather than bcrypt or argon2 — is a deliberate choice.
Authenticating a request has to answer "which credential is this?" from the
token alone, which means an indexed lookup; a per-row random salt would force a
scan of every credential in the table. HMAC keeps that a single indexed
equality check while ensuring a stolen database dump is not enough to forge
tokens, because `API_SECRET` never touches the database. Tokens are 256 bits of
randomness, so the offline brute-force resistance a slow hash buys for human
passwords is not needed here.

`token_preview` stores a short, unusable fragment so the console can label a row
without handling the secret. No response schema anywhere contains `token_hash`.

## Ownership enforcement

> A source must never be accessible merely because its `id` is known.

Every service read and write takes a `Scope` (`app/services/scope.py`), loads
the row, and asserts the row's owners match. Three properties fall out of that:

- **Cross-tenant reads return 404, not 403.** A 403 would confirm the id exists
  in someone else's tenant, which is itself a leak.
- **Ownership columns are derived, never accepted.** A source's
  `tenant_id`/`application_id` are copied from its subject.
- **The whole chain has to agree.** `GET /subjects/{a}/sources/{b}` fails if
  source `b` lives in a different subject, even when both belong to you.

`backend/tests/test_ownership.py` covers each of these.

## Extraction Agent

Below `Source` sits the first processing stage: the **Extraction Agent**
(`Document → Extracted Information`), running inside the backend on NVIDIA
models via build.nvidia.com (development, free) or Nebius Token Factory (demo).

```
Source → Document Processor (local, per-page triage) → Extraction Agent → source_extractions (v1, v2, …)
```

It runs only when asked — `extract=true` on an upload, `POST
/api/v1/sources/{id}/extractions`, or the **Extract** button in the console —
and every run is a new version with its model calls and cost recorded. Design:
[docs/extraction-agent.md](docs/extraction-agent.md). API reference: the
`Extractions` page of the docs site.

Setup: put `NVIDIA_API_KEY` (and later `NEBIUS_API_KEY`) in `backend/.env`,
choose `LLM_PROVIDER`, run `alembic upgrade head`. See `backend/.env.example`.

## Knowledge graph

Below extraction sits the **knowledge graph** on FalkorDB: each extraction's
merged text is chunked, an NVIDIA model reads each chunk for entities and
relationships from a fixed ontology (`backend/ontology.json`), duplicates are
resolved, and the facts land in one graph per tenant with provenance back to
source and chunk. A subject's graph (and its folders') can be viewed or
queried with deterministic 1–3 hop retrieval.

```
source_extractions.content → chunks → entities + relationships → FalkorDB → graph evidence
```

It runs only when asked — `build_graph=true` on an extract or upload, or `POST
/api/v1/sources/{id}/graph`. Design: [docs/graph-rag.md](docs/graph-rag.md).
API reference: the `Knowledge graph` page of the docs site.

Setup: set `FALKORDB_URL` (FalkorDB Cloud: `falkors://user:pass@host:port`) in
`backend/.env`, `pip install -r requirements.txt`, `alembic upgrade head`.
Without `FALKORDB_URL` the graph endpoints answer 503 and nothing else changes.

Embeddings and vector search are a later phase; none exist yet.

## Repository layout

| Directory   | Domain                  | What it is                                        |
| ----------- | ----------------------- | ------------------------------------------------- |
| `backend/`  | `api.memora.x.in`       | FastAPI service, PostgreSQL, Alembic migrations.  |
| `console/`  | `console.memora.x.in`   | Operator console. Behind a session login.         |
| `web/`      | `memora.x.in`           | Public landing page and API reference. No login.  |
| `docs/`     | —                       | Internal decision records, not a published site.  |

`web/` is deliberately public: someone integrating against Memora can read the
reference without an account, and every "use Memora" call to action on it is a
link into the console rather than an operation it performs itself.

```bash
cd backend  && uvicorn app.main:app --reload   # :8000
cd console  && npm run dev                     # :3000
cd web      && npm run dev                     # :3001
```
