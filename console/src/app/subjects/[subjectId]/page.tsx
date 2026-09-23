"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { api } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import { useMutation, useResource } from "@/lib/useResource";
import { Breadcrumbs } from "@/components/Shell";
import { FileExplorer } from "@/components/FileExplorer";
import {
  Button,
  ErrorState,
  KeyValueGrid,
  LoadingState,
  Mono,
  PageHeader,
  Panel,
  StatusBadge,
  TypeTag,
} from "@/components/ui";

export default function SubjectDetailPage() {
  const { subjectId } = useParams<{ subjectId: string }>();
  const router = useRouter();
  const subject = useResource(() => api.subjects.get(subjectId), [subjectId]);
  // The folder tree: children of this subject, and the sources directly in it.
  const removeSubject = useMutation(api.subjects.delete);

  if (subject.loading) {
    return (
      <Panel>
        <LoadingState label="Loading subject" />
      </Panel>
    );
  }
  if (subject.error || !subject.data) {
    return (
      <Panel>
        <ErrorState
          message={subject.error ?? "Subject not found"}
          onRetry={subject.reload}
        />
      </Panel>
    );
  }

  const data = subject.data;
  const isFolder = data.parent_subject_id !== null;
  const reloadAll = () => {
    subject.reload();
  };

  return (
    <>
      <Breadcrumbs
        items={[
          { label: "Tenants", href: "/tenants" },
          { label: data.tenant_name ?? "Tenant", href: `/tenants/${data.tenant_id}` },
          {
            label: data.application_name ?? "Application",
            href: `/applications/${data.application_id}`,
          },
          // The folder chain above this subject, nearest parent first.
          ...[...data.path].reverse().map((entry) => ({
            label: entry.external_id,
            href: `/subjects/${entry.id}`,
          })),
          { label: data.external_id },
        ]}
      />
      <PageHeader
        eyebrow={isFolder ? "Subject / folder" : "Subject / workspace"}
        title={data.external_id}
        actions={
          <Button
            variant="danger"
            disabled={removeSubject.pending}
            onClick={async () => {
              const what = isFolder
                ? "this folder, everything nested inside it, and all their sources"
                : "this workspace and all its sources";
              if (!window.confirm(`Delete ${what}? This cannot be undone.`))
                return;
              await removeSubject.mutate(data.id);
              router.push(`/applications/${data.application_id}`);
            }}
          >
            {removeSubject.pending ? "Deleting…" : "Delete"}
          </Button>
        }
      />

      {removeSubject.error && (
        <div className="mb-5">
          <Panel>
            <ErrorState message={removeSubject.error} />
          </Panel>
        </div>
      )}

      <div className="mb-5">
        <Panel title="Subject information">
          <KeyValueGrid
            items={[
              { label: "Subject ID (Memora)", value: <Mono>{data.id}</Mono> },
              { label: "External ID (yours)", value: <Mono>{data.external_id}</Mono> },
              { label: "Status", value: <StatusBadge status={data.status} /> },
              {
                label: "Application",
                value: (
                  <Link
                    href={`/applications/${data.application_id}`}
                    className="text-ink-secondary hover:text-ink hover:underline"
                  >
                    {data.application_name}
                  </Link>
                ),
              },
              {
                label: "Tenant",
                value: (
                  <Link href={`/tenants/${data.tenant_id}`} className="text-ink-secondary hover:text-ink hover:underline">
                    {data.tenant_name}
                  </Link>
                ),
              },
              {
                label: "Opened by",
                value: data.actor_external_id ? (
                  <Mono>{data.actor_external_id}</Mono>
                ) : (
                  <span className="text-ink-tertiary">No originating actor</span>
                ),
              },
              { label: "Created", value: formatDate(data.created_at) },
            ]}
          />
        </Panel>
      </div>

      <div className="mb-5">
        <FileExplorer subjectId={subjectId} applicationId={data.application_id} />
      </div>

      {/* Modals are handled inside FileExplorer */}
    </>
  );
}
