"use client";

/**
 * The connected Drive account's PDFs, with an "Import" action per file.
 *
 * Importing downloads the file server-side and feeds it into the existing
 * RAG pipeline under the chosen subject. The subject picker is a plain
 * <select> of the user's subjects; the backend scope-checks it again.
 */

import { useMemo, useState } from "react";

import { api } from "@/lib/api";
import type { Subject } from "@/lib/types";
import { Button, ErrorState, Mono } from "@/components/ui";
import { googleDriveApi } from "./googleDriveApi";
import type { DriveFile, DriveImportResult } from "./googleDriveTypes";

function formatBytes(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

export function GoogleDriveFiles({
  files,
  onImported,
}: {
  files: DriveFile[];
  onImported?: (result: DriveImportResult) => void;
}) {
  const [subjects, setSubjects] = useState<Subject[] | null>(null);
  const [subjectId, setSubjectId] = useState<string>("");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const [done, setDone] = useState<Record<string, DriveImportResult>>({});

  // Load the subject list once, when the first import is attempted.
  async function ensureSubjects() {
    if (subjects) return;
    const list = await api.subjects.list();
    setSubjects(list);
    if (list.length > 0 && !subjectId) setSubjectId(list[0].id);
  }

  async function importFile(file: DriveFile) {
    if (pendingId) return;
    await ensureSubjects();
    if (!subjectId) {
      setError("Create a subject first: Drive files are imported into one.");
      return;
    }
    setPendingId(file.drive_file_id);
    setError(undefined);
    try {
      const result = await googleDriveApi.import({
        drive_file_id: file.drive_file_id,
        subject_id: subjectId,
      });
      setDone((prev) => ({ ...prev, [file.drive_file_id]: result }));
      onImported?.(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The import failed");
    } finally {
      setPendingId(null);
    }
  }

  const sorted = useMemo(
    () => [...files].sort((a, b) => b.modified_time.localeCompare(a.modified_time)),
    [files],
  );

  if (sorted.length === 0) {
    return (
      <div className="px-4 py-10 text-center text-sm text-ink-secondary">
        No PDFs found in this Google Drive account.
      </div>
    );
  }

  return (
    <div className="px-4 py-3">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-secondary">
        <label htmlFor="gdrive-subject" className="font-medium">
          Import into subject:
        </label>
        <select
          id="gdrive-subject"
          value={subjectId}
          onFocus={ensureSubjects}
          onChange={(e) => setSubjectId(e.target.value)}
          className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink focus:border-brand focus:outline-none"
        >
          {(subjects ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.external_id || s.id}
            </option>
          ))}
        </select>
      </div>

      {error && <ErrorState message={error} />}

      <ul className="divide-y divide-line-soft">
        {sorted.map((file) => {
          const result = done[file.drive_file_id];
          const pending = pendingId === file.drive_file_id;
          return (
            <li key={file.drive_file_id} className="flex flex-wrap items-center gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink">{file.file_name}</p>
                <p className="text-xs text-ink-tertiary">
                  {formatBytes(file.size_bytes)} · modified {file.modified_time}
                </p>
              </div>
              {result ? (
                <span className="text-xs text-ink-secondary">
                  {result.already_imported ? "already imported as" : "imported as"} source{" "}
                  <Mono>{result.source_id.split("-")[0]}</Mono>
                </span>
              ) : (
                <Button onClick={() => importFile(file)} disabled={!!pendingId}>
                  {pending ? "Importing…" : "Import"}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}