/** Types for the Google Drive connector's API surface. */

export interface DriveConnection {
  id: string;
  drive_account_email: string;
  granted_scopes: string;
  status: string;
  created_at: string;
}

export interface DriveStatus {
  configured: boolean;
  connected: boolean;
  connection: DriveConnection | null;
}

export interface DriveFile {
  drive_file_id: string;
  file_name: string;
  mime_type: string;
  modified_time: string;
  size_bytes: number | null;
}

export interface DriveImportResult {
  source_id: string;
  drive_file_id: string;
  file_name: string;
  size_bytes: number;
  already_imported: boolean;
}

/* Private mode (zero-retention Q&A): the files are read in memory and
   discarded when the answer is ready -- nothing is stored server-side. */

export interface PrivateFileReport {
  drive_file_id: string;
  file_name: string;
  status: string; // processed | skipped | failed
  detail: string | null;
  pages: number;
  chunks: number;
}

export interface PrivateQueryEvidence {
  drive_file_id: string;
  file_name: string;
  content: string;
  section_path: string[];
  page_start: number | null;
  page_end: number | null;
  similarity: number;
  rerank_score: number | null;
}

export interface PrivateQueryUsage {
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
}

export interface PrivateQueryResponse {
  answer: string;
  files: PrivateFileReport[];
  evidence: PrivateQueryEvidence[];
  usage: PrivateQueryUsage;
}
