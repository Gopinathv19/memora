"use client";

/**
 * Private mode: ask questions about Drive PDFs without importing them.
 *
 * The selected files are downloaded into server memory, read, and answered
 * from -- then discarded. No copy, chunks or embeddings are stored, so this
 * panel is the way to query documents that must never leave the Drive
 * account. Import (below) remains the path for the persistent knowledge
 * base.
 */

import { useMemo, useState } from "react";

import { Button, InlineError, Mono } from "@/components/ui";
import { googleDriveApi } from "./googleDriveApi";
import type {
  DriveFile,
  PrivateFileReport,
  PrivateQueryResponse,
} from "./googleDriveTypes";

const MAX_FILES = 10; // mirrors PRIVATE_RAG_MAX_FILES on the backend

function reportBadge(report: PrivateFileReport) {
  const styles: Record<string, string> = {
    processed: "bg-emerald-50 text-emerald-700",
    skipped: "bg-amber-50 text-amber-700",
    failed: "bg-red-50 text-red-700",
  };
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${styles[report.status] ?? "bg-surface-sunken text-ink-secondary"}`}
    >
      {report.status}
    </span>
  );
}

export function GoogleDrivePrivateAsk({ files }: { files: DriveFile[] }) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string>();
  const [result, setResult] = useState<PrivateQueryResponse | null>(null);

  const sorted = useMemo(
    () => [...files].sort((a, b) => b.modified_time.localeCompare(a.modified_time)),
    [files],
  );

  function toggle(fileId: string) {
    setResult(null);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(fileId)) {
        next.delete(fileId);
      } else if (next.size < MAX_FILES) {
        next.add(fileId);
      }
      return next;
    });
  }

  async function ask() {
    if (asking) return;
    const query = question.trim();
    if (!query) {
      setError("Type a question first.");
      return;
    }
    if (selected.size === 0) {
      setError("Select at least one PDF to ask about.");
      return;
    }
    setAsking(true);
    setError(undefined);
    setResult(null);
    try {
      setResult(
        await googleDriveApi.query({
          query,
          drive_file_ids: [...selected],
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "The private query failed");
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="px-4 py-4">
      <p className="mb-3 text-xs text-ink-secondary">
        Select up to {MAX_FILES} PDFs and ask a question. The files are read in
        memory and discarded with the answer -- nothing is imported or stored.
      </p>

      <ul className="mb-3 max-h-56 divide-y divide-line-soft overflow-y-auto rounded-md border border-line">
        {sorted.map((file) => {
          const checked = selected.has(file.drive_file_id);
          return (
            <li key={file.drive_file_id}>
              <label className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-surface-sunken">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggle(file.drive_file_id)}
                  className="h-4 w-4 accent-brand"
                />
                <span className="min-w-0 flex-1 truncate text-sm text-ink">
                  {file.file_name}
                </span>
              </label>
            </li>
          );
        })}
      </ul>

      <div className="flex items-start gap-2">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={2}
          placeholder="Ask about the selected PDFs…"
          className="min-w-0 flex-1 rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-tertiary focus:border-brand focus:outline-none"
        />
        <Button onClick={ask} disabled={asking}>
          {asking ? "Reading…" : "Ask privately"}
        </Button>
      </div>
      <p className="mt-1.5 text-[11px] text-ink-tertiary">
        {selected.size} of {MAX_FILES} files selected · reading can take a
        moment: the PDFs are parsed and embedded on the spot.
      </p>

      {error && (
        <div className="mt-3">
          <InlineError message={error} />
        </div>
      )}

      {result && (
        <div className="mt-4 space-y-4">
          <div className="rounded-md border border-line bg-surface-sunken px-4 py-3">
            <p className="whitespace-pre-wrap text-sm text-ink">{result.answer}</p>
            <p className="mt-2 text-[11px] text-ink-tertiary">
              {result.usage.prompt_tokens + result.usage.completion_tokens}{" "}
              tokens · {result.usage.latency_ms} ms · nothing stored
            </p>
          </div>

          <div>
            <p className="mb-1.5 text-xs font-medium text-ink-secondary">
              Files read for this answer
            </p>
            <ul className="space-y-1">
              {result.files.map((report) => (
                <li
                  key={report.drive_file_id}
                  className="flex flex-wrap items-center gap-2 text-xs"
                >
                  {reportBadge(report)}
                  <span className="min-w-0 flex-1 truncate text-ink">
                    {report.file_name || <Mono>{report.drive_file_id}</Mono>}
                  </span>
                  {report.status === "processed" && (
                    <span className="text-ink-tertiary">
                      {report.pages} page{report.pages === 1 ? "" : "s"} ·{" "}
                      {report.chunks} chunk{report.chunks === 1 ? "" : "s"}
                    </span>
                  )}
                  {report.detail && (
                    <span className="basis-full pl-1 text-ink-tertiary">
                      {report.detail}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>

          {result.evidence.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-ink-secondary">
                Evidence ({result.evidence.length} chunk
                {result.evidence.length === 1 ? "" : "s"})
              </p>
              <ul className="space-y-2">
                {result.evidence.map((chunk, index) => (
                  <li
                    key={index}
                    className="rounded-md border border-line-soft px-3 py-2"
                  >
                    <p className="mb-1 text-[11px] text-ink-tertiary">
                      {chunk.file_name}
                      {chunk.section_path.length > 0 &&
                        ` · ${chunk.section_path.join(" › ")}`}
                      {chunk.page_start !== null &&
                        ` · page ${chunk.page_start}${
                          chunk.page_end && chunk.page_end !== chunk.page_start
                            ? `–${chunk.page_end}`
                            : ""
                        }`}
                    </p>
                    <p className="whitespace-pre-wrap text-xs text-ink-secondary">
                      {chunk.content}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
