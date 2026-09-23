"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { api } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import { useMutation, useResource } from "@/lib/useResource";
import { Breadcrumbs } from "@/components/Shell";
import { ExtractionPanel } from "@/components/ExtractionPanel";
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

export default function SourceDetailPage() {
  const { sourceId } = useParams<{ sourceId: string }>();
  const router = useRouter();
  const source = useResource(() => api.sources.get(sourceId), [sourceId]);
  const remove = useMutation(api.sources.delete);

  if (source.loading) {
    return (
      <Panel>
        <LoadingState label="Loading source" />
      </Panel>
    );
  }
  if (source.error || !source.data) {
    return (
      <Panel>
        <ErrorState
          message={source.error ?? "Source not found"}
          onRetry={source.reload}
        />
      </Panel>
    );
  }

  const data = source.data;
  // Only content Memora stored itself can be streamed back; an s3:// or
  // https:// URI registered as metadata is not ours to serve.
  const hasStoredContent = !!data.storage_uri?.startsWith("file://");

  return (
    <>
      <Breadcrumbs
        items={[
          { label: "Sources", href: "/sources" },
          {
            label: data.subject_external_id ?? "Subject",
            href: `/subjects/${data.subject_id}`,
          },
          { label: data.filename ?? data.id.split("-")[0] },
        ]}
      />
      <PageHeader
        eyebrow="Source"
        title={data.filename ?? data.storage_uri ?? data.id}
        actions={
          <>
            {hasStoredContent && (
              <a
                href={api.sources.downloadUrl(data.id)}
                className="inline-flex items-center rounded-md border border-line bg-panel px-3 h-[34px] text-sm font-medium text-ink transition-colors hover:bg-surface-strong"
              >
                Download
              </a>
            )}
            <Button
              variant="danger"
              disabled={remove.pending}
              onClick={async () => {
                if (
                  !window.confirm(
                    "Delete this source? Any content Memora stored for it is deleted too.",
                  )
                )
                  return;
                await remove.mutate(data.id);
                router.push(`/subjects/${data.subject_id}`);
              }}
            >
              {remove.pending ? "Deleting…" : "Delete source"}
            </Button>
          </>
        }
      />

      {remove.error && (
        <div className="mb-5">
          <Panel>
            <ErrorState message={remove.error} />
          </Panel>
        </div>
      )}

      <div className="mb-5">
        <Panel
          title="Source details"
          description="Every source carries its full ownership chain, so it can never be reached by ID alone."
        >
          <KeyValueGrid
            items={[
              { label: "Source ID", value: <Mono>{data.id}</Mono> },
              {
                label: "Tenant",
                value: (
                  <Link href={`/tenants/${data.tenant_id}`} className="text-ink-secondary hover:text-ink hover:underline">
                    {data.tenant_name}
                  </Link>
                ),
              },
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
                label: "Subject",
                value: (
                  <Link
                    href={`/subjects/${data.subject_id}`}
                    className="text-ink-secondary hover:text-ink hover:underline"
                  >
                    {data.subject_external_id}
                  </Link>
                ),
              },
              {
                label: "Registered by",
                value: data.actor_external_id ? (
                  <Mono>{data.actor_external_id}</Mono>
                ) : (
                  <span className="text-ink-tertiary">Not recorded</span>
                ),
              },
              { label: "Type", value: <TypeTag value={data.type} /> },
              {
                label: "Filename",
                value: data.filename ?? <span className="text-ink-tertiary">–</span>,
              },
              {
                label: "MIME type",
                value: data.mime_type ?? <span className="text-ink-tertiary">–</span>,
              },
              { label: "Size", value: formatBytes(data.size_bytes) },
              {
                label: "Storage URI",
                value: data.storage_uri ? (
                  <Mono>{data.storage_uri}</Mono>
                ) : (
                  <span className="text-ink-tertiary">No stored content</span>
                ),
              },
              { label: "Status", value: <StatusBadge status={data.status} /> },
              { label: "Registered", value: formatDate(data.created_at) },
            ]}
          />
        </Panel>
      </div>

      <ExtractionPanel source={data} onStatusChange={source.reload} />
    </>
  );
}
