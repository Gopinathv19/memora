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

export interface Subject {
  id: string;
  tenant_id: string;
  application_id: string;
  actor_id: string | null;
  external_id: string;
  status: string;
  created_at: string;
  application_name: string | null;
  tenant_name: string | null;
  actor_external_id: string | null;
  source_count: number;
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

 