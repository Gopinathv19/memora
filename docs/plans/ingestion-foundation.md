# Memora — Ingestion & Storage Foundation

## Context

`memora` is an empty repository (initial commit: README, LICENSE, .gitignore). Memora is intended to become a general-purpose persistent intelligence engine following the flow
`INPUT → INGESTION → SOURCE → ARTIFACT → UNDERSTANDING → KNOWLEDGE → MEMORY → RETRIEVAL → REASONING`.

Today's pass builds **only the first four stages plus the infrastructure to expose them safely as an API**: a source can be submitted, validated, stored immutably, checksummed, registered, processed asynchronously into basic artifacts, and observed through its lifecycle. Explicitly **not** today: LLM understanding, embeddings, vector search, entity extraction, knowledge graph construction, memory, retrieval, reasoning, agents, or any application-specific logic. Those get clean extension points and nothing more.

The stated need was "a simple layer which can get the source and store both locally as well as in the deployment," with deployment target not yet chosen. So the goal is a **thin but complete vertical slice** with the real architectural boundaries in place — not a toy upload endpoint, and not all 35 spec sections. Everything is 12-factor (env-driven config, one image per app, no host-path or local-disk assumptions), so "local vs deployment" is a configuration difference rather than a code difference.

### Decisions made with the user

| Decision | Choice |
|---|---|
| Scope | Thin complete vertical slice; real interfaces, minimal implementations |
| Storage | **One** S3-compatible provider. MinIO locally, S3/R2/GCS in deployment, same code |
| Upload flow | Single-step: client POSTs multipart, API streams to storage while hashing/sniffing |
| Duplicate bytes | Return the **existing** source (200 + duplicate flag), discard the new object; `on_duplicate=create` escape hatch |
| Tenant isolation | Repository-layer enforcement **plus** Postgres RLS as a backstop |
| PDF | Metadata only (page count). No text extraction |
| Console | Three screens: Upload, Sources list, Source detail |
| Stack | pnpm workspaces + TS, Fastify, Drizzle, BullMQ, Vitest, Vite+React |

### Where the spec is internally impossible (and how it's resolved)

1. **`validate → store → checksum` cannot happen in that order** with a streaming upload — the SHA-256 is only known after the last byte lands. Real order: validate the header bytes → store while hashing → register. Cost: a duplicate upload writes bytes we then delete. Mitigated by an optional `X-Content-SHA256` pre-flight header that lets a client skip the upload entirely.
2. **`SourceProcessor.process(source): Promise<Artifact[]>`** as literally specified would require every processor to hold DB and storage handles — making processors untestable without a database and capable of writing cross-tenant rows. Changed to `process(source, ctx): Promise<ArtifactDraft[]>`; the **worker** persists drafts inside one tenant-scoped transaction. This is the one interface deviating from the spec.
3. **Credential prefix** `ctx_` in the spec looks like a leftover from another project. Using `mk_local_` / `mk_live_` so local-vs-hosted is visible in the token itself.

---

## Stack, per package

| Package | Choice | Why |
|---|---|---|
| Monorepo | pnpm workspaces + TS project references (`tsc -b`) | 10 packages doesn't justify Turbo/Nx |
| API | **Fastify 5** + `@fastify/multipart` | Only option giving a true `Readable` per file with a mid-stream size abort. Express+multer buffers or writes to local disk, violating the no-local-disk rule. Hooks map 1:1 onto the §17 middleware chain |
| Postgres | **Drizzle** + `postgres-js` | Its query builder is composable data, which is what lets the tenant predicate be wrapped and made non-overridable. Prisma's is not |
| Queue | **BullMQ** behind `QueueProvider` | Only Node queue with first-class stalled-job detection — the exact mechanism satisfying "never stuck in PROCESSING". The abstraction's real payoff is an in-memory impl for unit tests, not vendor portability |
| Tests | **Vitest** + Testcontainers | One runner for node and jsdom; `globalSetup` boots containers |
| Console | **Vite + React**, static SPA | No SSR, no BFF, no server session. The console is a plain API client holding a key, not a privileged tier |
| Support libs | `pino` (redacted logging), `zod` (env + request validation), `file-type` (magic bytes), `uuidv7` (index locality), `papaparse` (CSV), `argon2` (credential hashing), `@aws-sdk/lib-storage` (**required** — streams unknown-length bodies; raw `PutObject` cannot) |

---

## Directory layout

```
memora/
  package.json  pnpm-workspace.yaml  tsconfig.base.json
  docker-compose.yml  .env.example  vitest.workspace.ts  eslint.config.js
  apps/api/src/
    server.ts             # buildServer(deps) -> FastifyInstance   (tests import this)
    config.ts  container.ts
    plugins/{request-id,error-handler,auth,authorize,tenant-context,rate-limit}.ts
    routes/sources/{create,list,get,delete,status,retry,schemas}.ts
    routes/credentials/{create,revoke,list}.ts
    routes/dev/bootstrap.ts          # local mode ONLY, 404 in hosted
    ingest/{upload-pipeline,storage-key,idempotency,dedup}.ts
  apps/console/src/{api/client.ts,pages/{Upload,SourceList,SourceDetail}.tsx}
  workers/ingestion-worker/src/
    worker.ts             # buildWorker(deps)                       (tests import this)
    handlers/process-source.ts  registry.ts  reaper.ts
  packages/shared/src/{branded,state-machine,errors}.ts + types/{source,artifact,principal,scopes}.ts
  packages/database/{drizzle.config.ts,migrations/,src/{schema/,tenant-db.ts,repositories/,internal/connection.ts,migrate.ts,index.ts}}
  packages/storage/src/{provider.ts,s3-provider.ts,key.ts,testing/in-memory.ts}
  packages/queue/src/{provider.ts,bullmq-provider.ts,in-memory-provider.ts}
  packages/auth/src/{token,hash,principal,scopes,authorize,credential-service}.ts
  packages/ingestion/src/{processor,registry,mime,checksum,size-guard}.ts + processors/
  packages/graph/src/{client,health}.ts        # FalkorDB: connection + healthcheck ONLY
  infrastructure/{postgres/init/00-roles.sql,minio/init.sh,falkordb/README.md}
  tests/{setup/,integration/,e2e/full-pipeline.spec.ts}
```

`packages/graph` is separate from `packages/database` so "database = Postgres" stays true.

---

## Postgres schema

Eight tables. IDs are UUIDv7 generated in app code (time-sortable → good index locality), typed `uuid`.

**`tenants`** — id, slug (unique), name, status, timestamps.
**`applications`** — id, tenant_id FK, slug, name, `UNIQUE(tenant_id, slug)`.
**`credentials`** — id, tenant_id, application_id, user_id (nullable, opaque — no users table today), name, `token_prefix`, `token_lookup char(16) UNIQUE` (first 16 hex of sha256(secret) → O(1) index probe), `token_hash` (argon2id), `scopes text[]`, mode, expires_at, last_used_at, revoked_at.
**`sources`** — the full §3 model plus: `storage_provider`, `error_code`, `error_message`, `retry_count`, `idempotency_key`, `duplicate_of_source_id`, `created_by_credential_id`, `processing_started_at`, `processing_deadline_at`, `deleted_at`.
- `filename` is **client-supplied, display-only, sanitized** — never used to build a storage path.
- `mime_type` is always the **sniffed** value, never the client's declaration.
- `storage_location` (`s3://bucket/key`) is **never returned to API clients**.
- `CHECK (checksum ~ '^sha256:[0-9a-f]{64}$')`.

**`artifacts`** — id, source_id, tenant_id (denormalized deliberately, so RLS and tenant-scoped queries need no join), type, mime_type, storage_location, size, `ordinal` (page/chunk index), metadata.
**`processing_jobs`** — id, tenant_id, source_id, queue, `external_job_id` (BullMQ id), attempt, max_attempts, status, `processor` (which one ran), error fields, enqueued/started/finished/`lease_expires_at`.
**`audit_events`** — id, tenant_id, application_id, actor_credential_id, actor_user_id, action, resource_type, resource_id, request_id, ip, outcome, `detail jsonb`. **Never** source content, never secrets.
**`idempotency_keys`** — PK `(tenant_id, application_id, key)`, `request_fingerprint`, state, source_id, stored response, `expires_at` (24h).

### Indexes

```
sources:    (tenant_id, created_at DESC)                      -- list
            (tenant_id, application_id, created_at DESC)
            (tenant_id, status)
            (tenant_id, checksum)                             -- dedup lookup
            (status, processing_deadline_at) WHERE status='PROCESSING'   -- reaper
  UNIQUE    (tenant_id, application_id, idempotency_key) WHERE idempotency_key IS NOT NULL
  UNIQUE    (tenant_id, application_id, checksum)
              WHERE checksum IS NOT NULL AND deleted_at IS NULL AND status <> 'FAILED'
processing_jobs: (tenant_id, source_id, enqueued_at DESC); (status, lease_expires_at) WHERE status='active'
artifacts:  (tenant_id, source_id, ordinal); (tenant_id, type)
audit_events: (tenant_id, created_at DESC); (tenant_id, resource_type, resource_id)
```

The partial unique dedup index is what makes concurrent identical uploads race-safe: one wins, the loser catches the unique violation and re-reads the winner.

### RLS — `migrations/0001_rls.sql` (hand-written)

```sql
ALTER TABLE sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE sources FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON sources
  USING      (tenant_id = current_setting('memora.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('memora.tenant_id', true)::uuid);
-- repeated for applications, credentials, artifacts, processing_jobs, audit_events, idempotency_keys
-- tenants gets the same policy on id
```

`current_setting(..., true)` yields NULL when unset, and `tenant_id = NULL` is never true — so **a query with no tenant context returns zero rows rather than everything**. Two roles: `memora_owner` (owns schema, runs migrations, BYPASSRLS) and `memora_app` (used by api/worker, subject to RLS). Hence two URLs: `DATABASE_URL` (app) and `DATABASE_MIGRATION_URL` (owner).

### Migrations

Generated by `pnpm db:generate` (drizzle-kit) → SQL reviewed → committed. Never `drizzle-kit push` outside dev. Applied by a **one-shot compose service** `migrate`; `api` and `worker` use `depends_on: {migrate: {condition: service_completed_successfully}}`. No auto-migrate on app boot (two replicas would race).

---

## Core interfaces

```ts
// packages/storage/src/provider.ts
export interface StorageProvider {
  readonly provider: string;                                  // 's3'
  put(key: string, body: Readable | Buffer, opts: PutOptions): Promise<PutResult>;
  get(key: string, opts?: GetOptions): Promise<GetResult>;    // { body: Readable, metadata }
  delete(key: string): Promise<void>;                         // idempotent; missing key is not an error
  exists(key: string): Promise<boolean>;
  getMetadata(key: string): Promise<ObjectMetadata | null>;
}
```
No `getSignedUrl` today — adding it later is purely additive and does not touch the Source model, which is what keeps the presigned two-step path available as a future option.

```ts
// packages/queue/src/provider.ts
export interface QueueProvider {
  enqueue<T>(queue, name, payload: T, opts?: EnqueueOptions): Promise<{jobId: string; deduplicated: boolean}>;
  consume<T>(queue, handler: JobHandler<T>, opts: ConsumeOptions): Promise<Consumer>;
  onTerminalFailure<T>(queue, cb): Promise<Consumer>;   // retries exhausted or stalled too often
  getJob(queue, jobId): Promise<JobStatus | null>;
  schedulePeriodic(queue, name, everyMs): Promise<void>;
  healthcheck(): Promise<boolean>;
  close(): Promise<void>;
}
// JobHandler receives (job: JobEnvelope<T>, signal: AbortSignal); JobEnvelope carries
// attempt/maxAttempts/leaseExpiresAt so visibility-timeout semantics stay portable.
```

```ts
// packages/ingestion/src/processor.ts
export interface SourceProcessor {
  readonly name: string;                        // recorded in processing_jobs.processor
  supports(source: Source): boolean;
  process(source: Source, ctx: ProcessorContext): Promise<ArtifactDraft[]>;
}
// ProcessorContext: { storage (read-only use), logger, signal, limits, openSource(): Promise<Readable> }
// ArtifactDraft: { type, mimeType, ordinal?, metadata?, and exactly one of body | storageKey | inlineOnly }
```
Processors hold **no DB handle and no tenant identity** — the worker persists drafts in one tenant-scoped transaction. This is what makes processors unit-testable without a database and structurally unable to write cross-tenant rows.

```ts
// packages/shared/src/types/principal.ts
export interface AuthenticatedPrincipal {
  readonly credentialId: string;
  readonly tenantId: TenantId;                 // branded types, see branded.ts
  readonly applicationId: ApplicationId;
  readonly userId?: string;
  readonly scopes: ReadonlySet<Scope>;
  readonly mode: 'local' | 'hosted';
  readonly tokenPrefix: string;
  readonly issuedAt: Date;
  readonly expiresAt?: Date;
  has(scope: Scope): boolean;                  // supports 'ns:*' wildcards in stored scopes
}
```
Carries no raw key, no token hash, no DB handle. `authorize(principal, scope)` is a **pure function** in `packages/auth`, unit-testable with no Fastify. Scopes: `document:read|write|delete` enforced today; `knowledge:*`, `memory:*`, `graph:*`, `retrieval:execute`, `agent:execute` declared as future capabilities.

**Token format:** `mk_local_<26-char id>_<43-char secret>` / `mk_live_...`. Split on the last `_`; `token_lookup = sha256(secret)[0..16]` probes the unique index; argon2id verifies the single row. Constant-time compare, and a **uniform 401** for unknown / revoked / expired / wrong-mode so the endpoint is not an oracle.

---

## Tenant isolation — the critical requirement

Three layers. The first two are structural; the third is the backstop.

**(a) `TenantScopedDb` — the tenant predicate cannot be omitted or replaced.**

```ts
// packages/database/src/tenant-db.ts
export type TenantTable = PgTable & { tenantId: PgColumn };   // table without tenant_id won't typecheck

export class TenantScopedDb {
  private constructor(private tx: Tx, readonly scope: TenantScope) {}   // only withTenant() constructs

  private guard<T extends TenantTable>(t: T, p?: Pred<T>): SQL {
    return and(eq(t.tenantId, this.scope.tenantId), p?.(t))!;   // tenant clause ALWAYS first
  }
  findMany<T extends TenantTable>(t: T, p?: Pred<T>, o?: PageOpts): Promise<T['$inferSelect'][]>;
  findOne, count, update, delete                                 // all route through guard()
  insert<T extends TenantTable>(t: T, rows: Array<Omit<T['$inferInsert'], 'tenantId'>>);
  //                                              ^ tenantId not accepted -> cannot be spoofed
}

export async function withTenant<R>(scope: TenantScope, fn: (tdb: TenantScopedDb) => Promise<R>) {
  return db.transaction(async (tx) => {
    await tx.execute(sql`SELECT set_config('memora.tenant_id', ${scope.tenantId}, true)`); // tx-LOCAL
    return fn(TenantScopedDb.__create(tx, scope));
  });
}
```

The returned builders expose **no `.where()`** — predicates go in as callbacks and get `AND`ed. This closes Drizzle's real footgun: `.where()` *replaces* rather than appends, so an innocent-looking `.where(eq(sources.id, id))` would otherwise silently drop the tenant clause.

**(b) Repositories take the scope, never a bare id.** Every method's first argument is the tenant-bound handle, so `findById(id)` is literally unwritable — there is no db to write it against.

```ts
export interface SourceRepository {
  create(tdb: TenantScopedDb, draft: NewSource): Promise<Source>;
  findById(tdb: TenantScopedDb, id: SourceId): Promise<Source | null>;
  list(tdb: TenantScopedDb, filter: SourceListFilter, page: Page): Promise<Paged<Source>>;
  findByChecksum(tdb: TenantScopedDb, checksum: string): Promise<Source | null>;
  transition(tdb: TenantScopedDb, id: SourceId, t: Transition): Promise<Source>;
  softDelete(tdb: TenantScopedDb, id: SourceId): Promise<Source | null>;
  incrementRetry(tdb: TenantScopedDb, id: SourceId): Promise<Source>;
}
```

**(c) The raw pool is unreachable.** It lives in `packages/database/src/internal/connection.ts`, excluded from the package `exports` map, so `import { db } from '@memora/database'` does not resolve. Backed by an ESLint `no-restricted-imports` rule and a CI test asserting no `.select(` or `drizzle(` outside `packages/database`. Plus RLS (above) as the layer that survives all of this being bypassed.

**Worker caveat:** the worker derives its scope from the job payload's `tenantId`, written by the API from the principal. Not client input, but still data — so `process-source.ts` re-verifies `source.tenantId === job.tenantId` before processing. **This check must not be removed as redundant.**

---

## API

Middleware chain, exactly §17:

```
genReqId → authenticate → resolvePrincipal → rateLimit → authorize(scope) → tenantContext → handler → audit
```

A `defineRoute({ scope, schema, handler })` helper makes `scope` a **required field**, so a route cannot be registered without declaring one. Request schemas are zod `.strict()`, and `tenantId`/`applicationId` are stripped at that layer — with a unit test asserting `?tenantId=` is rejected on every route. The handler has no access to a non-scoped db.

| Method | Path | Scope | Notes |
|---|---|---|---|
| POST | `/v1/sources` | `document:write` | multipart (file) **or** JSON (text/url/api/email/conversation). Honors `Idempotency-Key`. 201, or 200 on duplicate |
| GET | `/v1/sources` | `document:read` | cursor pagination on `(created_at, id)`; filters status/type/checksum |
| GET | `/v1/sources/:id` | `document:read` | **404, not 403**, for another tenant's id — no existence oracle |
| DELETE | `/v1/sources/:id` | `document:delete` | soft-delete row, cascade artifact rows, hard-delete objects, audit |
| GET | `/v1/sources/:id/status` | `document:read` | status, error code/message, retry_count, timestamps, artifact count, job state |
| POST | `/v1/sources/:id/retry` | `document:write` | only from FAILED; bumps retry_count, re-enqueues, → QUEUED |
| POST/GET | `/v1/credentials`, `/v1/credentials/:id/revoke` | `credential:manage` | plaintext returned exactly once |
| GET | `/v1/dev/bootstrap` | none | **local mode only**; 404 in hosted |
| GET | `/healthz`, `/readyz` | none | readyz checks pg + redis + s3 + falkordb |

### The single-pass streaming upload — `apps/api/src/ingest/upload-pipeline.ts`

One `Readable` from busboy, two transforms, one async consumer. No temp files, no full buffering.

```
busboy file stream
  → HeadSniffTransform   buffers first 4100B; calls fileTypeFromBuffer(head);
  │                      unsupported → destroy(new UnsupportedMediaType()); then pass-through
  → SizeGuard + HashTap  bytes += chunk.length; over cap → destroy(new SourceTooLarge());
  │                      hash.update(chunk) in the same transform (no extra stream)
  → lib-storage Upload({ Body: stream, ContentType: sniffedMime, partSize: 8MB, queueSize: 4,
                         leavePartsOnError: false })
```

1. **Cheap pre-flight rejects** before reading a byte: `Content-Length` over cap → 413 immediately. `@fastify/multipart` also configured with `limits: {fileSize, files: 1}` as a second net.
2. `sourceId = uuidv7()`; **insert the `sources` row at `RECEIVED`** (filename sanitized, mime null). A durable record exists before any bytes land, so there are never unattributed objects in the bucket.
3. **Server-side storage key, from IDs only:** `t/{tenantId}/a/{applicationId}/{yyyy}/{mm}/{dd}/{sourceId}/original`. No client filename, no user-controlled segment → **path traversal is structurally impossible**. Extension appended only from the *sniffed* mime.
4. `RECEIVED → VALIDATING`.
5. S3 `ContentType` must be known when `Upload` starts, so `await headSniff.sniffed` first (resolves within 4100 bytes; backpressure holds the stream), then construct `Upload` and pipe. This is the one place ordering matters and it costs nothing.
6. `await Promise.all([upload.done(), pipelinePromise])`. On any error: `upload.abort()`, best-effort `storage.delete(key)` (covers a landed part), transition to FAILED with the specific code, respond 4xx.
7. On success: `checksum = 'sha256:' + hash.digest('hex')`; `VALIDATING → STORED`, persisting mime/size/checksum/storage_location.
8. **Dedup** (below).
9. Insert `processing_jobs`, `queue.enqueue('ingestion', 'process-source', {sourceId, tenantId, applicationId}, {jobId: 'src:'+sourceId, maxAttempts: 3, backoff: exponential 5s})`, then `STORED → QUEUED`. Commit-then-enqueue; if the process dies in the gap, the reaper re-enqueues `STORED` rows older than 2 minutes. (A transactional outbox is the fully correct answer; the reaper is the honest 80% for today.)
10. 201 with the source DTO — **no `storage_location`**.

**MIME policy.** Allowlist by *sniffed* type. `text/plain`, `text/csv`, `application/json`, `text/markdown` are not reliably detectable by magic bytes, so for those: the declared `Content-Type` must be in the text allowlist **and** the head buffer must decode as UTF-8 with no NUL bytes and low control-char density. Magic bytes for binary, structural sniffing for text, **extension never**.

**JSON-body sources** (`type: text|url|api|email|conversation`) run the same helper over `Readable.from(Buffer.from(content))`, so exactly one code path produces checksum/size/storage_location. A `url` source stores a small JSON descriptor; **no fetching today**.

### Idempotency vs checksum dedup — different questions

- **`Idempotency-Key` = "is this the same *request*?"** Protects client retries. `INSERT ... ON CONFLICT DO NOTHING` into `idempotency_keys` with a `request_fingerprint`. Won → proceed, store the response on completion. Conflict + `completed` + fingerprint match → **replay the stored response**, `Idempotency-Replayed: true`. Conflict + `in_progress` → 409. Fingerprint mismatch → 422 `IDEMPOTENCY_KEY_REUSED`. The fingerprint covers method, path, type, filename, declared content type and metadata — it *cannot* cover the streamed bytes without reading them, which is a real and unavoidable limitation.
- **Checksum = "are these the same *bytes*?"** Runs after storage and hashing, scoped to `(tenant_id, application_id)` — not tenant-wide, since cross-application dedup would leak the existence of another app's document.

**On identical bytes:** delete the object just written, delete the duplicate row in the same transaction, and return **200** with the original source DTO plus `"duplicate": true` and `X-Memora-Duplicate: true`. Record the attempt as an `audit_events` row (`source.duplicate_rejected`, differing filename/metadata in `detail`) — that is where the "uploaded again under a different name" signal belongs, rather than in ghost rows that `GET /v1/sources` would then have to hide. `duplicate_of_source_id` stays in the schema anyway; it costs nothing if this decision is revisited. Escapes: `on_duplicate=create` per request, `INGEST_DEDUP_ENABLED=false` globally. **Fast path worth documenting for API consumers:** send `X-Content-SHA256` and dedup is checked *before* streaming, avoiding the wasted upload entirely.

### Error model

```ts
class MemoraError extends Error {
  constructor(readonly code: string, readonly httpStatus: number,
              message: string, readonly details?: Record<string, unknown>) {}
}
```
Catalog: `UNAUTHENTICATED` 401 (also returned for revoked/expired), `FORBIDDEN_SCOPE` 403, `SOURCE_NOT_FOUND` 404, `IDEMPOTENCY_IN_PROGRESS`/`INVALID_STATE_TRANSITION`/`RETRY_NOT_ALLOWED` 409, `SOURCE_TOO_LARGE` 413, `UNSUPPORTED_MEDIA_TYPE` 415, `INVALID_REQUEST`/`IDEMPOTENCY_KEY_REUSED` 422, `RATE_LIMITED` 429, `STORAGE_UNAVAILABLE` 503, `INTERNAL` 500; worker-side `PROCESSING_TIMEOUT`, `PROCESSOR_ERROR`, `ARTIFACT_LIMIT_EXCEEDED`.

Fastify `setErrorHandler`: known `MemoraError` → `{error: {code, message, request_id, details?}}`; anything else → log with stack server-side, return generic `INTERNAL`. **No stack traces to clients.** pino `redact`: `req.headers.authorization`, `*.token`, `*.apiKey`, `*.token_hash`, `*.content`, `*.body`.

Rate limiting is one `RateLimiter` interface + one Redis token-bucket implementation keyed on `credentialId`, config-driven, off in tests. Not over-built.

---

## Source lifecycle

```ts
export const TRANSITIONS: Record<SourceStatus, readonly SourceStatus[]> = {
  RECEIVED:   ['VALIDATING', 'FAILED'],
  VALIDATING: ['STORED', 'FAILED'],
  STORED:     ['QUEUED', 'FAILED'],
  QUEUED:     ['PROCESSING', 'FAILED'],
  PROCESSING: ['READY', 'FAILED'],
  READY:      [],              // terminal; delete is orthogonal via deleted_at
  FAILED:     ['QUEUED'],      // retry only
};
```

| Transition | Written by |
|---|---|
| `→ RECEIVED` | API `create.ts`, at row insert |
| `RECEIVED → VALIDATING → STORED → QUEUED` | API `upload-pipeline.ts` |
| `QUEUED → PROCESSING` | Worker; sets `processing_started_at`, `processing_deadline_at` |
| `PROCESSING → READY` | Worker, after artifacts commit; sets `ingested_at` |
| `* → FAILED` | API (pre-queue) / Worker (post-queue) / **Reaper** — always with `error_code` + `error_message` |
| `FAILED → QUEUED` | API `retry.ts`; `retry_count += 1`, new job |

Enforcement lives in the **repository, not the caller**: `transition()` is a conditional update — `UPDATE sources SET status=$to WHERE id=$id AND tenant_id=$tid AND status = ANY($allowedFrom)` — throwing `InvalidStateTransition` on zero rows affected. Race-safe (two workers cannot both advance a source) without table locks.

### Never stuck in PROCESSING — four layers

1. **Per-processor timeout.** `AbortSignal.timeout(PROCESSING_TIMEOUT_MS)`; processors honor `ctx.signal`. On abort → FAILED/`PROCESSING_TIMEOUT`.
2. **BullMQ stalled detection.** `lockDuration = JOB_LEASE_MS` (30s), `stalledInterval` 15s, `maxStalledCount` 2. A worker killed by SIGKILL or OOM has its job re-delivered, then goes terminal.
3. **Terminal-failure hook.** `onTerminalFailure` writes source → FAILED and job → failed. Runs in every worker replica, so the write is conditional-update-idempotent.
4. **Reaper** (`schedulePeriodic('maintenance', 'reap', 60s)`) — the layer that satisfies the requirement independently of Redis behavior:
   - `PROCESSING` past `processing_deadline_at` → cross-check `queue.getJob()`; not active → FAILED/`PROCESSING_TIMEOUT`
   - `STORED` older than 2min → re-enqueue (covers the commit-then-enqueue gap)
   - expire `idempotency_keys`; sweep storage prefixes for sources stuck pre-`STORED` over 1h

---

## Processors

`packages/ingestion/src/registry.ts` is an **ordered array; first `supports()` wins**, terminated by a `PassthroughProcessor` returning `true` always — so selection can never fail. Selection lives entirely in the worker; the API imports only `mime.ts`, `checksum.ts`, `size-guard.ts` from this package, enforced by a lint rule.

| Processor | Extraction today | Artifacts |
|---|---|---|
| `TextProcessor` (`text/plain`, `text/markdown`) | yes | 1 `text` (normalized UTF-8, BOM/CRLF stripped) |
| `JsonProcessor` (`application/json`) | yes | 1 `text` (canonical pretty-print) + metadata `{keys, depth, arrayLengths}` |
| `CsvProcessor` (`text/csv`) | yes | 1 `table` (headers, row count, inferred column types) + 1 `text` (TSV) |
| `PdfProcessor` | **no** — page count only | 1 metadata-only artifact `{pageCount?}` |
| `ImageProcessor` (`image/*`) | **no** | 1 `image` artifact referencing the original + dimensions if a trivial header parse |
| `UrlProcessor` (`type: url`) | **no** — no fetching | 1 metadata-only `{url, host, scheme}` |
| `PassthroughProcessor` | n/a | 1 metadata-only; the source still reaches READY |

No `pdf-parse`, no `unpdf`, no `sharp`. Read the PDF page count from the trailer or record `{}`.

---

## FalkorDB

Compose service + `packages/graph` with a connection module and a healthcheck wired into `/readyz`. **No graph nodes created today.** It will look like scaffolding because it is — the point is that `readyz` and compose don't churn when the knowledge layer lands.

---

## Console — three screens

1. **Upload** — drag/drop one file, or a textarea for `type: 'text'`, or a URL box. Shows the size cap, optional `Idempotency-Key` field, and surfaces structured errors verbatim (this screen is really an API debugging tool).
2. **Sources list** — created, type, filename, size, status badge, error code. Polls every 3s while any row is non-terminal; filter by status.
3. **Source detail** — the 7-state timeline with timestamps, error code + message, retry_count, Retry (when FAILED) and Delete buttons, artifacts table. No content preview — that needs a download endpoint outside today's scope.

Plus a mode banner: `LOCAL` (orange) / `HOSTED` (neutral) with the token prefix.

**Local credentials without a login system:** on first paint the console calls `GET /v1/dev/bootstrap`. In `MEMORA_MODE=local` the API returns `{mode, apiKey: 'mk_local_...', tenantId, applicationId}` from the dev credential the seed step generates (stored in Redis, not a file — no host paths). The console keeps it in `sessionStorage` and sends it as a bearer token like any other client. In hosted mode the route 404s and the console shows a "paste your API key" box. No cookies, no server session, and the hosted deployment is never publicly usable.

**Guard:** `/v1/dev/bootstrap` requires `MEMORA_MODE==='local'`, *and* the API refuses to boot in local mode unless the bind address is loopback-ish or `MEMORA_ALLOW_LOCAL_MODE_PUBLIC` is explicitly set. A mis-set env var is the obvious way this becomes a vulnerability.

---

## Config and Docker

`.env.example` with safe placeholders, one `config.ts` per app, zod-parsed at boot, **fail fast** listing every missing var. In hosted mode the schema additionally *requires* real storage credentials and *rejects* the local defaults (asserts secrets ≠ `change-me`).

Key vars: `MEMORA_MODE`, `DATABASE_URL` / `DATABASE_MIGRATION_URL`, `REDIS_URL`, `OBJECT_STORAGE_{ENDPOINT,REGION,BUCKET,ACCESS_KEY_ID,SECRET_ACCESS_KEY,FORCE_PATH_STYLE}`, `FALKORDB_URL`, `INGEST_MAX_FILE_BYTES=26214400` (25MB), `INGEST_ALLOWED_MIME`, `INGEST_DEDUP_ENABLED`, `IDEMPOTENCY_TTL_SECONDS`, `QUEUE_CONCURRENCY`, `PROCESSING_TIMEOUT_MS`, `JOB_LEASE_MS`, `JOB_MAX_ATTEMPTS`, `JOB_MAX_STALLS`, `REAPER_INTERVAL_MS`, `RATE_LIMIT_*`.

`OBJECT_STORAGE_FORCE_PATH_STYLE=true` for MinIO/R2, `false` for AWS — this env var is the entire local-vs-deployment storage difference.

**docker-compose.yml services:** `postgres` (16-alpine, `pg_isready` healthcheck, roles init mounted), `redis`, `falkordb`, `minio`, `minio-init` (one-shot `mc` bucket create), `migrate` (one-shot), `api`, `worker`, `console`. All `env_file: .env`. Named volumes only for postgres/minio/falkordb data; **no host bind mounts in api/worker** in the base file (host-path-free is the 12-factor answer; an optional `docker-compose.dev.override.yml` can mount source for in-container reload).

Multi-stage Dockerfiles, `pnpm deploy --filter` for pruned `node_modules`, `node:20-alpine`, non-root user, `--init` for signals. The worker installs a SIGTERM handler that stops accepting jobs and awaits in-flight ones via `consumer.close()`.

---

## Build order

Each phase ends in something runnable.

| # | Phase | Verifiable by |
|---|---|---|
| 0 | Skeleton: workspaces, tsconfig, lint, `packages/shared` (types, branded ids, error catalog, state machine) | `pnpm build && pnpm test` green on state-machine + scope tests. No infra |
| 1 | Infra: compose, `.env.example`, MinIO/Postgres init, FalkorDB, `packages/graph` health | `docker compose up postgres redis minio falkordb minio-init` healthy; `pnpm infra:check` pings all four |
| 2 | Database: 8-table Drizzle schema, `0000_init.sql`, hand-written `0001_rls.sql`, `migrate.ts`, `withTenant` + `TenantScopedDb`, repositories, seed (tenant + app + local dev credential) | Migrate runs; test proves RLS blocks a no-context query and `TenantScopedDb` always injects the predicate |
| 3 | Storage + Queue + Auth packages (+ in-memory impls) | put/get/delete/exists/getMetadata round-trip against MinIO; enqueue→consume→complete against Redis; generated key verifies, revoked one doesn't |
| 4 | API auth + read paths: `buildServer`, request-id, error handler, auth chain, rate limit, GET endpoints, credentials, healthz/readyz, audit hook | curl with the seeded key lists sources; wrong key → 401; missing scope → 403; tenant-isolation test green |
| 5 | API upload: `upload-pipeline.ts`, storage keys, MIME/size guards, idempotency, dedup, enqueue, DELETE, retry | Upload a CSV → 201, row QUEUED, object in MinIO, job in Redis. 26MB → 413. Fake-PNG-that's-a-PDF → 415 |
| 6 | Worker: `buildWorker`, `process-source`, registry + 7 processors, artifact persistence, terminal-failure hook, reaper | **First end-to-end**: upload → READY with artifacts; forced failure → FAILED with a code; `kill -9` mid-job → observably FAILED |
| 7 | Tests hardened: Testcontainers `globalSetup`, full integration matrix, `e2e/full-pipeline.spec.ts` | `pnpm test` green on a clean machine with only Docker |
| 8 | Console: Vite app, 3 screens, `/v1/dev/bootstrap`, nginx prod stage | `docker compose up` → localhost:5173 → drag a file → QUEUED → PROCESSING → READY |
| 9 | Polish: Dockerfiles, graceful shutdown, README quickstart, `.env.example` audit | Literal `git clone && cp .env.example .env && docker compose up` on a clean checkout |

**Phase 1 must confirm Compose supports `depends_on: condition: service_completed_successfully`** — the reported "Compose v5.3.1" is an unusual version string, and the whole "just works" migration story depends on it. Check early, not at phase 9.

---

## Verification

`buildServer(deps)` and `buildWorker(deps)` return instances rather than self-starting — enforced from phase 0, and it's what makes the integration suite in-process and fast.

**Test infrastructure: Testcontainers** (`@testcontainers/postgresql`, `@testcontainers/redis`, generic container for MinIO) via Vitest `globalSetup`, with a `TEST_USE_RUNNING_STACK=1` escape hatch pointing at compose. Not compose-for-tests (manual step, leaked state, can't parallelize). Not mocks — the two highest-value requirements, tenant isolation and "reaches READY", depend on RLS policies, partial unique indexes, and BullMQ stalled handling, which only exist in the real thing. In-memory storage/queue impls are for **unit** tests only.

**Unit** (no containers): checksum; size guard; MIME sniffing incl. a spoofed `.png` that's really a PDF; `canTransition` matrix; `authorize` + scope wildcards; token parse/hash/verify; storage-key generation against `../`, absolute paths, NUL bytes, unicode; processor registry selection per type; a `TenantScopedDb` SQL snapshot asserting **every** generated query contains the tenant predicate; error serialization contains no stack; config zod validation.

**Integration** (containers):

| Spec | Asserts |
|---|---|
| `upload` | multipart → 201; STORED→QUEUED; object in MinIO; checksum matches an independently computed sha256; `storage_location` absent from the response |
| `worker-pipeline` | in-process `buildWorker` → READY; artifacts for text/json/csv |
| `failure` | processor throws → attempts exhausted → FAILED with `error_code` |
| `stalled` | PROCESSING with a past deadline + reaper → FAILED/`PROCESSING_TIMEOUT` |
| `retry` | FAILED → retry → QUEUED → READY, `retry_count == 1` |
| `tenant-isolation` | tenant A's key GETs B's id → **404**; repo call with A's scope → null; raw SQL as `memora_app` without `memora.tenant_id` set → **0 rows** (proves RLS) |
| `revoked` | key works → revoke → same key → 401 |
| `idempotency` | same key twice → one source, replayed response; identical bytes with a different key → 200 duplicate, one source, one object |
| `limits` | 26MB body → 413 mid-stream, **no object left in the bucket** |
| `audit` | expected rows exist; **no** row's `detail` contains file content or a `mk_` token |

**E2E** (`tests/e2e/full-pipeline.spec.ts`): boot API + worker + all containers; upload a CSV; poll `GET /:id/status` to READY; assert artifacts; round-trip the bytes back through `StorageProvider.get`; assert the audit trail; DELETE and assert the objects are gone. This is the §32 required `upload → storage → queue → worker → READY`.

**Manual, against §34:** `docker compose up` → console → upload a PDF → see it registered with its SHA-256 → confirm the object in the MinIO console → watch the lifecycle advance → READY → inspect metadata → delete → force a failure and retry → upload a CSV → repeat via curl → verify no-key 401, wrong-scope 403, and cross-tenant 404.

---

## Known sharp edges

1. **Checksum-after-store is inherent** to streaming (see Context). `X-Content-SHA256` is the mitigation, not a fix.
2. **`RECEIVED` rows accumulate for rejected uploads.** Inserting before validation buys observability and audit provenance; the cost is FAILED rows for garbage, which is why the dedup unique index is partial on `status <> 'FAILED'`. Deferring the insert would make rejects invisible instead. Observability was chosen deliberately.
3. **RLS needs two DB roles and two URLs** — slightly more setup than a single `DATABASE_URL`. Worth it: it's the only enforcement that survives someone bypassing the repository layer.
4. **`QueueProvider` leaks BullMQ shape.** `leaseExpiresAt`, `maxStalls` and `onTerminalFailure` are emulable by SQS or a Postgres queue, not by Pub/Sub. Don't claim more portability than that.
5. **Idempotency replay vs streaming bodies.** Replaying a stored response means not consuming the body, but HTTP wants it drained. We respond immediately and let Fastify close the connection; some clients will report a broken pipe rather than reading the 200. A known industry wart.
6. **`POST /v1/sources` is two endpoints in one coat** — multipart for files, JSON for the other five types. Keep the content-type branch three lines long and share everything after "produce a `Readable`".
7. **`processing_jobs` duplicates BullMQ state.** Postgres is authoritative for what the user sees; Redis for what runs; the reaper reconciles. A Redis flush wipes queued jobs, so the `STORED`-too-long sweep is not optional.
8. **argon2 verification costs ~50–100ms per request.** Accept it for phase 1, or add a 30s in-process principal cache keyed on `token_lookup` with a Redis revocation epoch. **Do not** switch to a fast hash.
9. **Delete asymmetry.** DELETE soft-deletes the row and hard-deletes the objects — so content is unrecoverable, the audit trail survives, and because the dedup index excludes `deleted_at IS NOT NULL`, re-uploading the same bytes after a delete creates a fresh source. That's intended; worth confirming it matches expectations in use.
