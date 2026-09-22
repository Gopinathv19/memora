# Memora foundation — decisions

Where this build made a judgement call, and why. Written so the next person
does not have to re-derive the reasoning or wonder whether something was an
oversight.

## Schema

**`actors` sits between application and subject.** Without it there is no
record of *who* caused a workspace to exist, which for something like a legal
application is not optional. The table stores only the consuming
application's own identifier (`external_id`) plus a type and display name.
Memora does not become an identity provider: no email, no password, no role.

**`type` on an actor is broader than "user"** — `user`, `service`, `agent`,
`system`. A workspace can be opened by a background job or an autonomous
agent, and forcing those through a "user" type would make the audit trail lie.

**`external_id`, not `subject_key` or `subject_id`.** The original brief
suggested renaming `subjects.subject_id` to `subject_key`. The revised model
used `external_id`, and that is what shipped, for both `actors` and `subjects`:
one name for "the consuming application's own identifier for this thing",
used consistently. What matters is the rule the rename existed to enforce —
`id` is *always* Memora's internal UUID and never the customer's identifier.

**`subjects.actor_id` is nullable, with `ON DELETE RESTRICT`.** Nullable so a
subject created by a process with no meaningful originating actor is still
representable. RESTRICT rather than CASCADE because deleting an actor must not
silently delete the workspaces they opened.

**`sources` carries `tenant_id` and `application_id` redundantly.** They are
derivable through `subject_id`, but every read filters on the ownership chain,
and denormalizing turns that into one indexed predicate instead of a
three-table join. Only the service layer writes them, always copied from the
parent subject.

**`sources.created_by_actor_id` exists now, not later.** The brief suggested
deferring it. It landed early because the upload endpoint is the application's
ingestion point, and "which actor uploaded this" is the question asked
immediately afterwards. It is audit metadata and is explicitly *not* part of
the ownership chain — ownership is tenant/application/subject.

**Status columns are `VARCHAR`, not PostgreSQL `ENUM`.** Adding a status later
is then an application change rather than a migration that rewrites a type
other tables depend on. Allowed values are enforced by Pydantic at the edge.

**Two columns not in the brief:** `api_credentials.token_preview` (so the
console can label a credential row without handling the secret) and
`sources.size_bytes` (the upload path knows it; not recording it would mean the
console cannot show a file size).

## Token storage

Tokens are hashed with **HMAC-SHA256 keyed by `API_SECRET`**, not bcrypt or
argon2. The reasoning is in the README and in `app/core/security.py`: request
authentication needs an indexed lookup by hash, which a per-row random salt
makes impossible without scanning the table. The keyed HMAC keeps the lookup a
single equality check while ensuring a database dump alone cannot be turned
into working tokens, since the key never touches the database. Tokens are 256
bits of randomness, so slow hashing buys nothing here.

Consequence worth knowing: **rotating `API_SECRET` invalidates every issued
credential.** That is the correct behaviour for a compromise, but it is not a
routine operation.

## Storage

A `StorageBackend` protocol with a local-disk implementation, rather than
either no upload at all or a direct S3 dependency. The brief said not to build
S3 upload unless trivial, but "registering and posting a file as an application
point" was the explicit goal, so the bytes have to go somewhere. Three methods
(`put`, `open`, `delete`) addressed by an opaque `storage_uri` that the backend
mints; callers never construct a path. Swapping in S3 is one implementation
plus one line in `get_storage()`.

Objects are laid out as `{tenant_id}/{subject_id}/{uuid}-{filename}` so a
tenant's bytes can be located, or bulk-deleted, without consulting the
database. Filenames are sanitized and every resolved path is checked to be
inside the storage root.

**Write order on upload:** the subject is resolved and scope-checked *before*
any byte is written, so an unauthorized upload never touches storage; the row
is written last, and if it fails the stored object is removed, so a failed
request cannot leave an orphan file. **On delete** the order reverses — row
first, then bytes — so a failed cleanup leaves a stray object rather than a row
pointing at bytes that are gone.

## API surface

**Flat list endpoints in addition to the nested ones.** The brief specified
nested routes only, but the console's Applications / Actors / Subjects /
Sources pages each need a cross-tenant list with optional filters. Both exist:
`POST /tenants/{id}/applications` to create, `GET /applications?tenant_id=` to
list. The flat lists are scope-filtered like everything else, so an API
credential calling one sees only its own rows.

**`DELETE /credentials/{id}` really deletes.** A revoked-but-present row would
keep a dead hash occupying the unique index and invite "is this still usable?"
ambiguity in the console. Recording who revoked what belongs in an audit log,
not in this table. Setting `status: revoked` via PATCH remains available for a
reversible suspension.

**There is no `DELETE /tenants/{id}`.** The foreign keys cascade all the way to
sources, so a tenant delete is a very large destructive operation that deserves
a deliberate design — confirmation, soft-delete, or an export first — rather
than arriving as a convenience endpoint.

**Cross-tenant access returns 404, never 403.** A 403 confirms that the id
exists in someone else's tenant, which is itself an information leak.

## Console

**Client-side data fetching, not server components.** The console is a plain
API client: it holds no privileged server tier, and every page needs genuine
loading / error / empty states. Fetching in the browser through one typed
client (`src/lib/api.ts`) and one hook (`useResource`) means those three states
are implemented once and every page has them, rather than each page inventing
its own. It also keeps the console deployable as a static bundle against any
backend URL.

**Console authentication is deliberately thin.** `ADMIN_API_KEY`, when set, is
checked on management requests — but `NEXT_PUBLIC_ADMIN_API_KEY` reaches the
browser, so it is a speed bump against casual access, not a security boundary.
This is fine for a localhost operations console and is *not* fine exposed to a
network; that needs real operator sign-in, which the MVP does not attempt.

**Both loopback origins are allowed, in two separate places.** `localhost:3000`
and `127.0.0.1:3000` are distinct origins to a browser, and picking the "wrong"
one broke the console twice during verification, in two unrelated ways:

1. The backend's `CORS_ORIGINS` rejected it, so every API call failed. The
   console's error state reported this clearly, but the cause is not obvious.
2. Next 16 rejected it for `next dev` requests, answering the HMR websocket
   with a bare `Unauthorized`. Because the HMR client is part of the Turbopack
   dev runtime, that refusal **stops hydration entirely** — no effect ever
   runs, so every panel sits on "Loading…" indefinitely with nothing in the
   browser console but a websocket error and nothing at all in the terminal.
   This one is genuinely hard to diagnose from the symptom.

Both spellings are therefore listed in `backend/.env.example` (`CORS_ORIGINS`)
and in `console/next.config.ts` (`allowedDevOrigins`). If the console is ever
opened from another host, both need that origin added.

**Next 16, not 15.** The upgrade was requested, and it also removes a real
footgun: Next 15 wrote `next dev` and `next build` output into the same
`.next/` directory, so running a build while the dev server was up left the dev
server serving chunk references that no longer existed (`Cannot find module
'./960.js'`, MODULE_NOT_FOUND, HTTP 500 on every page). Next 16 separates
`.next/dev` from `.next/build`, and a concurrent build no longer disturbs a
running dev server — verified. Note that `next lint` was removed in Next 16, so
the `lint` script is gone; the project has no ESLint config to run.

## Testing

The suite runs against **real PostgreSQL, not SQLite**. The schema depends on
native UUID columns, `gen_random_uuid()` and `ON DELETE RESTRICT`/`SET NULL`
semantics that SQLite cannot represent, so a SQLite run would verify something
other than what ships. The fixtures truncate every table between tests, so the
target must be a throwaway database.

Coverage is concentrated where a mistake is expensive: the full MVP flow
end to end, cross-tenant isolation on every resource and verb, and proof that
the raw token appears in no table and no response but the one that creates it.

## Not built

GraphDB, pgvector, embeddings, LLM calls, RAG, semantic search, document
chunking, knowledge-graph extraction, event systems, Kafka, Redis,
microservices, Kubernetes. The chain stops at `Source`.

The extension point is the `Source` row and its `status` lifecycle
(`pending → processing → completed | failed`), which the API already exposes
and nothing yet advances. When extraction arrives it reads pending sources and
writes `documents` / `document_chunks` beneath them; no table here has to
change to allow it.
