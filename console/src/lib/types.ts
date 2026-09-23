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
  role: "layout" | "vision" | "extract";
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
