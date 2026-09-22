"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { api } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import { useMutation, useResource } from "@/lib/useResource";
import { Breadcrumbs } from "@/components/Shell";
import { DataTable } from "@/components/DataTable";
import { CreateSourceModal } from "@/components/modals";
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
  const subject = useResource(() => api.subjects.get(subjectId), [subjectId]);
  const sources = useResource(
    () => api.sources.list({ subjectId }),
    [subjectId],
  );
  const [registering, setRegistering] = useState(false);
  const remove = useMutation(api.sources.delete);

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
          { label: data.external_id },
        ]}
      />
      <PageHeader
        eyebrow="Subject / workspace"
        title={data.external_id}
        actions={
          <Button variant="primary" onClick={() => setRegistering(true)}>
            Register source
          </Button>
        }
      />

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

      <Panel
        title="Sources"
        counter={sources.data?.length}
        description="Everything registered in this workspace."
        actions={
          <Button variant="primary" onClick={() => setRegistering(true)}>
            Register source
          </Button>
        }
      >
        {remove.error && (
          <div className="px-4 pt-3">
            <ErrorState message={remove.error} />
          </div>
        )}
        <DataTable
          rows={sources.data}
          loading={sources.loading}
          error={sources.error}
          onRetry={sources.reload}
          rowKey={(source) => source.id}
          empty={{
            title: "No sources in this workspace",
            description:
              "Upload a file, or register a URL or chat transcript that lives elsewhere.",
            action: (
              <Button variant="primary" onClick={() => setRegistering(true)}>
                Register source
              </Button>
            ),
          }}
          columns={[
            {
              header: "Source",
              cell: (source) => (
                <Link
                  href={`/sources/${source.id}`}
                  className="font-medium text-ink hover:underline"
                >
                  {source.filename ?? source.storage_uri ?? source.id.split("-")[0]}
                </Link>
              ),
            },
            { header: "Type", width: "90px", cell: (source) => <TypeTag value={source.type} /> },
            {
              header: "MIME type",
              cell: (source) =>
                source.mime_type ?? <span className="text-ink-tertiary">–</span>,
            },
            {
              header: "Size",
              align: "right",
              width: "90px",
              cell: (source) => (
                <span className="tabular-nums text-ink-secondary">
                  {formatBytes(source.size_bytes)}
                </span>
              ),
            },
            {
              header: "Status",
              width: "110px",
              cell: (source) => <StatusBadge status={source.status} />,
            },
            {
              header: "Registered",
              align: "right",
              cell: (source) => (
                <span className="text-ink-secondary">{formatDate(source.created_at)}</span>
              ),
            },
            {
              header: "",
              align: "right",
              width: "90px",
              cell: (source) => (
                <Button
                  variant="danger"
                  disabled={remove.pending}
                  onClick={async () => {
                    if (
                      !window.confirm(
                        `Delete "${source.filename ?? source.id}"? Any content Memora stored for it is deleted too.`,
                      )
                    )
                      return;
                    await remove.mutate(source.id);
                    sources.reload();
                    subject.reload();
                  }}
                >
                  Delete
                </Button>
              ),
            },
          ]}
        />
      </Panel>

      {registering && (
        <CreateSourceModal
          subjectId={subjectId}
          applicationId={data.application_id}
          onClose={() => setRegistering(false)}
          onCreated={() => {
            setRegistering(false);
            sources.reload();
            subject.reload();
          }}
        />
      )}
    </>
  );
}
