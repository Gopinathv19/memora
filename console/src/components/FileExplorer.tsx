"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { api } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import type { Source, Subject } from "@/lib/types";
import { useMutation, useResource } from "@/lib/useResource";
import { DataTable } from "@/components/DataTable";
import { CreateFolderModal, CreateSourceModal } from "@/components/modals";
import { Button, ErrorState, Panel, StatusBadge, TypeTag } from "@/components/ui";

type Entry =
  | { kind: "folder"; folder: Subject }
  | { kind: "file"; file: Source };

export function FileExplorer({
  subjectId,
  applicationId,
}: {
  subjectId: string;
  applicationId: string;
}) {
  const router = useRouter();
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [registering, setRegistering] = useState(false);

  const folders = useResource<Subject[]>(
    () => api.subjects.list({ parentSubjectId: subjectId }),
    [subjectId]
  );
  const files = useResource<Source[]>(
    () => api.sources.list({ subjectId }),
    [subjectId]
  );

  const removeSource = useMutation(api.sources.delete);
  const removeFolder = useMutation(api.subjects.delete);

  const entries: Entry[] = useMemo(() => {
    const fds = (folders.data ?? []).map((folder) => ({ kind: "folder" as const, folder }));
    const fls = (files.data ?? []).map((file) => ({ kind: "file" as const, file }));
    // Folders first, then files; name ascending inside each bucket
    return [
      ...fds.sort((a, b) => a.folder.external_id.localeCompare(b.folder.external_id)),
      ...fls.sort((a, b) => (a.file.filename ?? a.file.id).localeCompare(b.file.filename ?? b.file.id)),
    ];
  }, [folders.data, files.data]);

  const loading = folders.loading || files.loading;
  const error = folders.error || files.error;
  const reloadAll = () => {
    folders.reload();
    files.reload();
  };

  return (
    <Panel
      title="Explorer"
      counter={entries.length}
      description="Browse folders and files inside this subject."
      actions={
        <>
          <Button onClick={() => setCreatingFolder(true)}>New folder</Button>
          <Button variant="primary" onClick={() => setRegistering(true)}>
            Register source
          </Button>
        </>
      }
    >
      {error && (
        <div className="px-4 pt-3">
          <ErrorState message={error} onRetry={reloadAll} />
        </div>
      )}

      <DataTable
        rows={entries}
        loading={loading}
        error={undefined}
        onRetry={reloadAll}
        rowKey={(row) => (row.kind === "folder" ? row.folder.id : row.file.id)}
        empty={{
          title: "This folder is empty",
          description: "Create a new folder, upload a file or register metadata.",
          action: (
            <Button variant="primary" onClick={() => setRegistering(true)}>
              Register source
            </Button>
          ),
        }}
        columns={[
          {
            header: "Name",
            cell: (row: Entry) =>
              row.kind === "folder" ? (
                <Link
                  href={`/subjects/${row.folder.id}`}
                  className="font-medium text-ink hover:underline flex items-center gap-2"
                >
                  <svg
                    aria-hidden
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    className="size-[16px] text-ink-secondary"
                  >
                    <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.5h9A1.5 1.5 0 0 1 21 10v8.5A1.5 1.5 0 0 1 19.5 20h-15A1.5 1.5 0 0 1 3 18.5Z" />
                  </svg>
                  <span>{row.folder.external_id}</span>
                </Link>
              ) : (
                <Link
                  href={`/sources/${row.file.id}`}
                  className="font-medium text-ink hover:underline"
                >
                  {row.file.filename ?? row.file.storage_uri ?? row.file.id.split("-")[0]}
                </Link>
              ),
          },
          {
            header: "Type",
            width: "100px",
            cell: (row: Entry) =>
              row.kind === "folder" ? (
                <span className="text-ink-secondary">Folder</span>
              ) : (
                <TypeTag value={row.file.type} />
              ),
          },
          {
            header: "MIME type",
            cell: (row: Entry) =>
              row.kind === "file" ? (
                row.file.mime_type ?? <span className="text-ink-tertiary">–</span>
              ) : (
                <span className="text-ink-tertiary">–</span>
              ),
          },
          {
            header: "Size",
            align: "right",
            width: "100px",
            cell: (row: Entry) =>
              row.kind === "file" ? (
                <span className="tabular-nums text-ink-secondary">{formatBytes(row.file.size_bytes)}</span>
              ) : (
                <span className="text-ink-tertiary">–</span>
              ),
          },
          {
            header: "Status",
            width: "120px",
            cell: (row: Entry) =>
              row.kind === "file" ? (
                <StatusBadge status={row.file.status} />
              ) : (
                <span className="text-ink-tertiary">–</span>
              ),
          },
          {
            header: "Registered",
            align: "right",
            cell: (row: Entry) => (
              <span className="text-ink-secondary">
                {formatDate((row.kind === "folder" ? row.folder.created_at : row.file.created_at) as any)}
              </span>
            ),
          },
          {
            header: "",
            align: "right",
            width: "140px",
            cell: (row: Entry) => (
              <div className="flex justify-end gap-2">
                {row.kind === "file" && row.file.storage_uri?.startsWith("file://") && (
                  <a
                    href={api.sources.downloadUrl(row.file.id)}
                    className="inline-flex items-center rounded-md border border-line bg-panel px-3 h-[28px] text-sm font-medium text-ink transition-colors hover:bg-surface-strong"
                  >
                    Download
                  </a>
                )}
                {row.kind === "folder" ? (
                  <Button
                    variant="danger"
                    disabled={removeFolder.pending}
                    onClick={async () => {
                      if (
                        !window.confirm(
                          `Delete "${row.folder.external_id}" and everything nested inside it?`
                        )
                      )
                        return;
                      await removeFolder.mutate(row.folder.id);
                      reloadAll();
                    }}
                  >
                    Delete
                  </Button>
                ) : (
                  <Button
                    variant="danger"
                    disabled={removeSource.pending}
                    onClick={async () => {
                      if (
                        !window.confirm(
                          `Delete "${row.file.filename ?? row.file.id}"? Any content Memora stored for it is deleted too.`
                        )
                      )
                        return;
                      await removeSource.mutate(row.file.id);
                      reloadAll();
                    }}
                  >
                    Delete
                  </Button>
                )}
              </div>
            ),
          },
        ]}
      />

      {creatingFolder && (
        <CreateFolderModal
          applicationId={applicationId}
          parentSubjectId={subjectId}
          onClose={() => setCreatingFolder(false)}
          onCreated={() => {
            setCreatingFolder(false);
            reloadAll();
          }}
        />
      )}

      {registering && (
        <CreateSourceModal
          subjectId={subjectId}
          applicationId={applicationId}
          onClose={() => setRegistering(false)}
          onCreated={() => {
            setRegistering(false);
            reloadAll();
          }}
        />
      )}
    </Panel>
  );
}
