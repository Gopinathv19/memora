/** Mirrors the Pydantic schemas in backend/app/schemas. */

export type ResourceStatus = "active" | "suspended" | "revoked";
export type ActorType = "user" | "service" | "agent" | "system";
export type SourceType = "file" | "chat" | "url";
export type SourceStatus = "pending" | "processing" | "completed" | "failed";

export interface Tenant {
  id: string;
  name: string;
  status: string;
  created_at: string;
  application_count: number;
  subject_count: number;
  source_count: number;
}

export interface Application {
  id: string;
  tenant_id: string;
  name: string;
  slug: string;
  status: string;
  created_at: string;
  tenant_name: string | null;
  credential_count: number;
  actor_count: number;
  subject_count: number;
  source_count: number;
}

export interface Credential {
  id: string;
  application_id: string;
  name: string;
  /** A truncated fragment of the token. The full token is never returned. */
  token_preview: string;
  status: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
}

/**
 * The create-credential response, and the only shape that ever carries `token`.
 * Hold it in component state just long enough to show it once; never persist it.
 */
export interface CredentialCreated extends Credential {
  token: string;
  warning: string;
}

export interface Actor {
  id: string;
  tenant_id: string;
  application_id: string;
  external_id: string;
  type: string;
  name: string | null;
  status: string;
  created_at: string;
  application_name: string | null;
  subject_count: number;
}

/** One hop of a subject's ancestor chain, nearest parent first. */
export interface SubjectPathEntry {
  id: string;
  external_id: string;
}

export interface Subject {
  id: string;
  tenant_id: string;
  application_id: string;
  actor_id: string | null;
  /** The containing subject when this one is a folder; null for a root. */
  parent_subject_id: string | null;
  external_id: string;
  status: string;
  created_at: string;
  application_name: string | null;
  tenant_name: string | null;
  actor_external_id: string | null;
  source_count: number;
  /** How many subjects (folders) live directly inside this one. */
  child_count: number;
  /** Ancestor chain, nearest parent first. Empty for a root. */
  path: SubjectPathEntry[];
}

export interface Source {
  id: string;
  tenant_id: string;
  application_id: string;
  subject_id: string;
  created_by_actor_id: string | null;
  type: string;
  mime_type: string | null;
  filename: string | null;
  storage_uri: string | null;
  // True when Memora stored the bytes itself (local disk or its R2 bucket),
  // so they can be extracted and downloaded. Decided by the backend.
  has_stored_content: boolean;
  size_bytes: number | null;
  status: string;
  created_at: string;
  tenant_name: string | null;
  application_name: string | null;
  subject_external_id: string | null;
  actor_external_id: string | null;
}

export interface DashboardStats {
  tenants: number;
  applications: number;
  actors: number;
  subjects: number;
  sources: number;
  active_credentials: number;
  sources_by_status: Record<string, number>;
}

/** One day of a metric series, as returned by GET /api/v1/stats/metrics. */
export interface TimeseriesPoint {
  /** ISO `YYYY-MM-DD`, in server time. */
  date: string;
  value: number;
}

/**
 * A zero-filled daily series plus the window totals that frame it. `previous`
 * covers the identical span immediately before the window, which is what makes
 * a delta possible without a second request.
 */
export interface MetricSeries {
  points: TimeseriesPoint[];
  current: number;
  previous: number;
}

export interface DashboardMetrics {
  days: number;
  sources: MetricSeries;
  subjects: MetricSeries;
  actors: MetricSeries;
  sources_by_type: Record<string, number>;
  storage_bytes: number;
  largest_source_bytes: number;
  last_source_at: string | null;
}

export interface User {
  id: string;
  email: string;
  email_verified: boolean;
  name: string;
  avatar_url: string;
}

export interface AuthResponse {
  user: User;
}

 
/* ------------------------------------------------------ Extraction Agent */

export type ExtractionStatus = "processing" | "completed" | "partial" | "failed";
export type ExtractionMode = "standard" | "deep";

export interface ExtractedField {
  key: string;
  value: string;
  page: number | null;
  confidence: number | null;
}

export interface ExtractedTable {
  title: string | null;
  page: number | null;
  columns: string[];
  rows: string[][];
}

/** How one page / slide / sheet / image was read: the routing audit trail. */
export interface PageProvenance {
  page: number;
  kind: string;
  difficulty: "easy" | "medium" | "hard";
  route: "text" | "vision" | "layout";
  model: string | null;
  status: "ok" | "fallback" | "failed" | "skipped";
  note: string | null;
}

export interface ExtractionResult {
  source_id: string;
  document_type: string;
  title: string | null;
  language: string | null;
  summary: string;
  fields: ExtractedField[];
  tables: ExtractedTable[];
  pages: PageProvenance[];
  status: ExtractionStatus;
  warnings: string[];
}

export interface ExtractionSummary {
  id: string;
  source_id: string;
  tenant_id: string;
  application_id: string;
  version: number;
  status: ExtractionStatus;
  mode: ExtractionMode;
  instructions: string | null;
  provider: string;
  models: Record<string, string>;
  error: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  triggered_by_kind: "user" | "credential";
  triggered_by_user_id: string | null;
  triggered_by_credential_id: string | null;
  actor_id: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface ExtractionUsageCall {
  id: string;
  provider: string;
  model: string;
  role: "layout" | "vision" | "extract" | "graph";
  page: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  /** The operator's rate applied to this call; null when the model is unpriced. */
  price: {
    input_per_1m: number;
    output_per_1m: number;
    per_image: number;
    per_call: number;
    free: boolean;
    effective_from: string;
  } | null;
  latency_ms: number;
  status: string;
  error: string | null;
  created_at: string;
}

export interface Extraction extends ExtractionSummary {
  result: ExtractionResult | null;
  usage: ExtractionUsageCall[];
}

export interface UsageTotals {
  runs: number;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

export interface UsageGroup extends UsageTotals {
  key: string;
  label: string | null;
}

export interface UsageReport {
  totals: UsageTotals;
  by_application: UsageGroup[];
  by_model: UsageGroup[];
  by_trigger: UsageGroup[];
}

/* ------------------------------------------------------- Knowledge graph */

export type GraphBuildStatus = "processing" | "completed" | "partial" | "failed";

export interface FailedChunk {
  index: number;
  chunk_id: string;
  page_start: number | null;
  page_end: number | null;
  error: string;
}

export interface GraphBuildSummary {
  id: string;
  source_id: string;
  tenant_id: string;
  application_id: string;
  extraction_id: string;
  extraction_version: number;
  retry_of_id: string | null;
  status: GraphBuildStatus;
  provider: string;
  model: string;
  chunk_chars: number;
  chunk_overlap: number;
  chunk_count: number;
  failed_chunk_count: number;
  entity_count: number;
  relationship_count: number;
  failed_chunks: FailedChunk[];
  stats: Record<string, number>;
  error: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  triggered_by_kind: "user" | "credential";
  created_at: string;
  finished_at: string | null;
}

export interface GraphBuild extends GraphBuildSummary {
  usage: ExtractionUsageCall[];
}

export interface GraphEntity {
  entity_id: string;
  name: string;
  entity_type: string;
  description: string | null;
  aliases: string[];
  mention_count: number;
  source_ids: string[];
  source_chunk_ids: string[];
}

export interface GraphRelationship {
  source_entity_id: string;
  source_name: string;
  relation: string;
  target_entity_id: string;
  target_name: string;
  description: string | null;
  confidence: number | null;
  raw_relation: string | null;
  source_ids: string[];
  source_chunk_ids: string[];
}

export interface GraphChunk {
  chunk_id: string;
  source_id: string;
  index: number;
  page_start: number | null;
  page_end: number | null;
  text: string;
}

export interface GraphResult {
  seed_entity_ids: string[];
  entities: GraphEntity[];
  relationships: GraphRelationship[];
  source_chunk_ids: string[];
  chunks: GraphChunk[];
}

/* --------------------------------------------------------- Chunking */

/** A retrieval chunk, as returned by GET /sources/{id}/chunks. */
export interface RetrievalChunk {
  id: string;
  semantic_block_id: string;
  extraction_id: string;
  source_version: number;
  chunk_index: number;
  content: string;
  embedding_text: string;
  token_count: number;
  content_type: "text" | "table" | "figure" | "mixed" | "key_value" | string;
  page_start: number | null;
  page_end: number | null;
  section_path: string[];
  is_active: boolean;
  created_at: string;
}

/** Response for POST /sources/{id}/rechunk. */
export interface RechunkResponse {
  extraction_id: string;
  source_id: string;
  version: number;
  chunk_count: number;
}

/* --------------------------------------------------------- Embeddings */

export type EmbeddingStatus =
  | "pending"
  | "processing"
  | "completed"
  | "failed"
  | "stale";

/** A registered embedding model, as returned by GET /embedding/models. */
export interface EmbeddingModel {
  id: string;
  provider: string;
  model_name: string;
  model_identifier: string;
  model_version: string | null;
  embedding_type: string;
  dimension: number;
  max_input_tokens: number | null;
  normalization: string;
  similarity_metric: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/** A strategy, with the joined model fields the console displays. */
export interface EmbeddingStrategy {
  id: string;
  name: string;
  description: string | null;
  model_id: string;
  input_type: string;
  document_template: string;
  query_template: string;
  normalization: string;
  similarity_metric: string;
  dimension: number;
  configuration_json: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  model_provider: string | null;
  model_name: string | null;
  model_identifier: string | null;
}

/** What POST .../embed returns: work queued, not work done. */
export interface EmbeddingEnqueueResponse {
  enqueued: number;
  strategy_id: string;
  strategy_name: string;
}

export interface RetryFailedResponse {
  reset: number;
  strategy_id: string | null;
}

/** Scope-wide embedding health, as returned by GET /embedding/stats. */
export interface EmbeddingStats {
  total_chunks: number;
  embedded: number;
  pending: number;
  processing: number;
  failed: number;
  stale: number;
  coverage_percent: number;
}

/** Per-source embedding health, for the source detail page. */
export interface SourceEmbeddingStatus {
  source_id: string;
  strategy_id: string;
  strategy_name: string;
  total_chunks: number;
  embedded: number;
  pending: number;
  processing: number;
  failed: number;
  stale: number;
  coverage_percent: number;
}

/** Chunk-level embedding info for the debug view. */
export interface ChunkEmbeddingDebug {
  id: string;
  strategy_id: string;
  strategy_name: string;
  status: EmbeddingStatus | string;
  input_hash: string;
  attempt_count: number;
  error_message: string | null;
  dimension: number;
  vector_preview: (number | string)[];
  model_metadata: Record<string, unknown>;
  created_at: string;
  embedded_at: string | null;
}

/* --------------------------------------------------------- Retrieval */

/** One supporting chunk behind a generated answer, with per-stage scores. */
export interface RetrievedChunk {
  chunk_id: string;
  source_id: string;
  subject_id: string;
  content: string;
  content_type: string;
  section_path: string[];
  page_start: number | null;
  page_end: number | null;
  token_count: number;
  similarity: number;
  rerank_score: number | null;
}

/** Pipeline observability: candidates at each stage. */
export interface RetrievalTrace {
  hnsw_candidates: number;
  mmr_candidates: number;
  final_chunks: number;
  reranker_used: boolean;
}

export interface QueryUsage {
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
}

/** Response of POST /query: the grounded answer plus its evidence. */
export interface QueryResponse {
  query: string;
  answer: string;
  strategy_id: string;
  strategy_name: string;
  chunks: RetrievedChunk[];
  retrieval: RetrievalTrace;
  usage: QueryUsage;
}
