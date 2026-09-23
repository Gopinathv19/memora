/**
 * The console's single point of contact with the FastAPI backend.
 *
 * Everything goes through `request()` so that the base URL, the admin header
 * and error handling are defined once. `ApiError` carries the backend's own
 * `detail` string, which every failure response is guaranteed to include, so
 * the UI can show the real reason a call failed instead of a generic message.
 */

import type {
  Actor,
  Application,
  AuthResponse,
  Credential,
  CredentialCreated,
  DashboardMetrics,
  DashboardStats,
  Source,
  Subject,
  Tenant,
  User,
} from "./types";

const BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

  const PUBLIC_PATHS = ["/auth/login","/auth/signup","/auth/google"]

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Query = Record<string, string | undefined | null>;

function buildUrl(path: string, query?: Query): string {
  const url = new URL(`${BASE_URL}/api/v1${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value) url.searchParams.set(key, value);
  }
  return url.toString();
}

async function request<T>(
  path: string,
  init: RequestInit & { query?: Query } = {},
): Promise<T> {
  const { query, ...rest } = init;
  const headers = new Headers(rest.headers);
 
  // Only set JSON content-type when there is a JSON body: setting it on a
  // FormData request would override the multipart boundary the browser adds.
  if (rest.body && typeof rest.body === "string") {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), { ...rest, headers , credentials: "include"});
  } catch {
    throw new ApiError(
      `Cannot reach the Memora API at ${BASE_URL}. Is the backend running?`,
      0,
    );
  }
  if (response.status == 401 && 
    typeof window !== "undefined" &&
    !PUBLIC_PATHS.includes(path) &&
    window.location.pathname !="/login"
  ) {
    window.location.href="/login";
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? safeJson(text) : null;

  if (!response.ok) {
    throw new ApiError(
      extractDetail(payload) ?? `Request failed with status ${response.status}`,
      response.status,
      payload?.code,
    );
  }
  return payload as T;
}

function safeJson(text: string): any {
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

/**
 * FastAPI reports its own request-validation failures as an array of objects
 * under `detail`, while our domain errors use a plain string. Flatten both into
 * one readable sentence.
 */
function extractDetail(payload: any): string | null {
  const detail = payload?.detail;
  if (!detail) return null;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const field = Array.isArray(item?.loc)
          ? item.loc.filter((p: unknown) => p !== "body").join(".")
          : null;
        return field ? `${field}: ${item.msg}` : item?.msg;
      })
      .filter(Boolean)
      .join("; ");
  }
  return null;
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  stats: () => request<DashboardStats>("/stats"),

  /** Daily activity, storage and type mix. Bucketing happens in PostgreSQL. */
  metrics: (days = 30) =>
    request<DashboardMetrics>("/stats/metrics", { query: { days: String(days) } }),

  tenants: {
    list: () => request<Tenant[]>("/tenants"),
    get: (id: string) => request<Tenant>(`/tenants/${id}`),
    create: (body: { name: string }) =>
      request<Tenant>("/tenants", { method: "POST", body: json(body) }),
    update: (id: string, body: { name?: string; status?: string }) =>
      request<Tenant>(`/tenants/${id}`, { method: "PATCH", body: json(body) }),
  },

  applications: {
    list: (tenantId?: string) =>
      request<Application[]>("/applications", { query: { tenant_id: tenantId } }),
    get: (id: string) => request<Application>(`/applications/${id}`),
    create: (tenantId: string, body: { name: string; slug: string }) =>
      request<Application>(`/tenants/${tenantId}/applications`, {
        method: "POST",
        body: json(body),
      }),
    update: (
      id: string,
      body: { name?: string; slug?: string; status?: string },
    ) =>
      request<Application>(`/applications/${id}`, {
        method: "PATCH",
        body: json(body),
      }),
  },

  credentials: {
    list: (applicationId?: string) =>
      request<Credential[]>("/credentials", {
        query: { application_id: applicationId },
      }),
    /** The response is the one and only time the raw token exists client-side. */
    create: (
      applicationId: string,
      body: { name: string; expires_at?: string | null },
    ) =>
      request<CredentialCreated>(`/applications/${applicationId}/credentials`, {
        method: "POST",
        body: json(body),
      }),
    update: (id: string, body: { name?: string; status?: string }) =>
      request<Credential>(`/credentials/${id}`, {
        method: "PATCH",
        body: json(body),
      }),
    revoke: (id: string) =>
      request<void>(`/credentials/${id}`, { method: "DELETE" }),
  },

  actors: {
    list: (params: { tenantId?: string; applicationId?: string } = {}) =>
      request<Actor[]>("/actors", {
        query: {
          tenant_id: params.tenantId,
          application_id: params.applicationId,
        },
      }),
    get: (id: string) => request<Actor>(`/actors/${id}`),
    create: (
      applicationId: string,
      body: { external_id: string; type: string; name?: string | null },
    ) =>
      request<Actor>(`/applications/${applicationId}/actors`, {
        method: "POST",
        body: json(body),
      }),
    update: (id: string, body: { name?: string; status?: string }) =>
      request<Actor>(`/actors/${id}`, { method: "PATCH", body: json(body) }),
  },

  subjects: {
    list: (
      params: {
        tenantId?: string;
        applicationId?: string;
        actorId?: string;
        /** List only the children of this subject (folder). */
        parentSubjectId?: string;
        /** List only root workspaces, not folders inside them. */
        rootsOnly?: boolean;
      } = {},
    ) =>
      request<Subject[]>("/subjects", {
        query: {
          tenant_id: params.tenantId,
          application_id: params.applicationId,
          actor_id: params.actorId,
          parent_subject_id: params.parentSubjectId,
          roots_only: params.rootsOnly ? "true" : undefined,
        },
      }),
    get: (id: string) => request<Subject>(`/subjects/${id}`),
    create: (
      applicationId: string,
      body: {
        external_id: string;
        parent_subject_id?: string | null;
        actor_id?: string | null;
      },
    ) =>
      request<Subject>(`/applications/${applicationId}/subjects`, {
        method: "POST",
        body: json(body),
      }),
    update: (
      id: string,
      body: {
        external_id?: string;
        parent_subject_id?: string | null;
        status?: string;
        actor_id?: string;
      },
    ) =>
      request<Subject>(`/subjects/${id}`, { method: "PATCH", body: json(body) }),
    /** Deletes the subject, its whole folder subtree, and their sources. */
    delete: (id: string) => request<void>(`/subjects/${id}`, { method: "DELETE" }),
  },

  sources: {
    list: (
      params: {
        tenantId?: string;
        applicationId?: string;
        subjectId?: string;
        status?: string;
      } = {},
    ) =>
      request<Source[]>("/sources", {
        query: {
          tenant_id: params.tenantId,
          application_id: params.applicationId,
          subject_id: params.subjectId,
          status_filter: params.status,
        },
      }),
    get: (id: string) => request<Source>(`/sources/${id}`),
    /** Register a source from metadata: a URL, a chat, or external storage. */
    create: (
      subjectId: string,
      body: {
        type: string;
        mime_type?: string | null;
        filename?: string | null;
        storage_uri?: string | null;
        created_by_actor_id?: string | null;
      },
    ) =>
      request<Source>(`/subjects/${subjectId}/sources`, {
        method: "POST",
        body: json(body),
      }),
    /** Register a source by posting the file itself. */
    upload: (subjectId: string, file: File, createdByActorId?: string | null) => {
      const form = new FormData();
      form.append("file", file);
      if (createdByActorId) form.append("created_by_actor_id", createdByActorId);
      return request<Source>(`/subjects/${subjectId}/sources/upload`, {
        method: "POST",
        body: form,
      });
    },
    update: (id: string, body: { status?: string; filename?: string }) =>
      request<Source>(`/sources/${id}`, { method: "PATCH", body: json(body) }),
    /** Move a file to another subject (folder) in the same application. */
    move: (id: string, targetSubjectId: string) =>
      request<Source>(`/sources/${id}/move`, {
        method: "POST",
        body: json({ target_subject_id: targetSubjectId }),
      }),
    delete: (id: string) =>
      request<void>(`/sources/${id}`, { method: "DELETE" }),
    downloadUrl: (id: string) => buildUrl(`/sources/${id}/content`),
  },

    auth: {
    me: () => request<User>("/auth/me"),
    signup: (body: { email: string; password: string; name?: string }) =>
      request<AuthResponse>("/auth/signup", { method: "POST", body: json(body) }),
    login: (body: { email: string; password: string }) =>
      request<AuthResponse>("/auth/login", { method: "POST", body: json(body) }),
    google: (idToken: string) =>
      request<AuthResponse>("/auth/google", {
        method: "POST",
        body: json({ id_token: idToken }),
      }),
    logout: () => request<void>("/auth/logout", { method: "POST" }),
  },

};
