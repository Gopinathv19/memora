/**
 * The Google Drive connector's typed client for the backend's /gdrive/* routes.
 *
 * Everything goes through the shared `request()` in lib/api.ts, so the base
 * URL, credentials and error handling are defined once. No OAuth token ever
 * appears here: the browser only ever sees "connect" (a redirect) and JSON
 * about files and imports.
 */

import { request } from "@/lib/api";
import type {
  DriveFile,
  DriveImportResult,
  DriveStatus,
  PrivateQueryResponse,
} from "./googleDriveTypes";

const json = (body: unknown) => JSON.stringify(body);

export const googleDriveApi = {
  /** Is the connector configured, and is this user connected? */
  status: () => request<DriveStatus>("/gdrive/status"),

  /** The connected account's PDFs, newest first. */
  files: (limit = 100) =>
    request<DriveFile[]>("/gdrive/files", {
      query: { limit: String(limit) },
    }),

  /** Download a Drive PDF into the existing RAG pipeline, under a subject. */
  import: (body: {
    drive_file_id: string;
    subject_id: string;
    extract?: boolean;
    build_graph?: boolean;
    reimport?: boolean;
  }) =>
    request<DriveImportResult>("/gdrive/import", {
      method: "POST",
      body: json(body),
    }),

  /** What this user has imported so far (the V2 sync baseline). */
  imports: () => request<DriveImportResult[]>("/gdrive/imports"),

  /**
   * Private mode: zero-retention Q&A over Drive PDFs. The files are read in
   * memory and discarded when the answer is ready -- no copy, no chunks, no
   * embeddings are stored. Use `import` for the persistent knowledge base.
   */
  query: (body: { query: string; drive_file_ids: string[] }) =>
    request<PrivateQueryResponse>("/gdrive/query", {
      method: "POST",
      body: json(body),
    }),

  /** Disconnect: the backend deletes the connection row and its tokens. */
  disconnect: () =>
    request<void>("/gdrive/connection", { method: "DELETE" }),
};