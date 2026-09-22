"use client";

import Link from "next/link";
import { useState } from "react";

import { api } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import { useResource } from "@/lib/useResource";
import { DataTable } from "@/components/DataTable";
import { Field, Select } from "@/components/form";
import { CreateSourceModal } from "@/components/modals";
import {
  Button,
  ButtonLink,
  PageHeader,
  Panel,
  StatusBadge,
  TypeTag,
} from "@/components/ui";

const STATUS_OPTIONS = [
  { value: "pending", label: "Pending" },
  { value: "processing", label: "Processing" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
];

export default function SourcesPage() {
  const [subjectFilter, setSubjectFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const subjects = useResource(() => api.subjects.list(), []);
  const sources = useResource(
    () =>
      api.sources.list({
        subjectId: subjectFilter || undefined,
        status: statusFilter || undefined,
      }),
    [subjectFilter, statusFilter],
  );
  const [registering, setRegistering] = useState(false);

  const selectedSubject = subjects.data?.find(
    (subject) => subject.id === subjectFilter,
  );

  return (
    <>
      <PageHeader
        title="Sources"
        description="A source is a registered resource inside a workspace — a file, a URL or a chat transcript. Memora records and tracks it; extraction, chunking and embeddings are a later phase, so a new source stays pending."
        actions={
          selectedSubject ? (
            <Button variant="primary" onClick={() => setRegistering(true)}>
              Register source
            </Button>
          ) : (
            <ButtonLink href="/subjects" variant="normal">
              Choose a subject to register in
            </ButtonLink>
          )
        }
      />

      <Panel
        title="All sources"
        counter={sources.data?.length}
        actions={
          <div className="flex flex-wrap gap-3">
            <div className="w-64">
              <Field label="Filter by subject">
                <Select
                  value={subjectFilter}
                  onChange={setSubjectFilter}
                  placeholder="All subjects"
                  options={(subjects.data ?? []).map((subject) => ({
                    value: subject.id,
                    label: `${subject.external_id} (${subject.application_name})`,
                  }))}
                />
              </Field>
            </div>
            <div className="w-44">
              <Field label="Filter by status">
                <Select
                  value={statusFilter}
                  onChange={setStatusFilter}
                  placeholder="Any status"
                  options={STATUS_OPTIONS}
                />
              </Field>
            </div>
          </div>
        }
      >
        <DataTable
          rows={sources.data}
          loading={sources.loading}
          error={sources.error}
          onRetry={sources.reload}
          rowKey={(source) => source.id}
          empty={{
            title:
              subjectFilter || statusFilter
                ? "No sources match these filters"
                : "No sources registered yet",
            description:
              "Sources are registered inside a subject. Open a workspace to add one.",
            action: <ButtonLink href="/subjects">Go to subjects</ButtonLink>,
          }}
          columns={[
            {
              header: "Filename",
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
              header: "Subject",
              cell: (source) => (
                <Link
                  href={`/subjects/${source.subject_id}`}
                  className="text-ink-secondary hover:text-ink hover:underline"
                >
                  {source.subject_external_id}
                </Link>
              ),
            },
            {
              header: "Application",
              cell: (source) => (
                <Link
                  href={`/applications/${source.application_id}`}
                  className="text-ink-secondary hover:text-ink hover:underline"
                >
                  {source.application_name}
                </Link>
              ),
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
          ]}
        />
      </Panel>

      {registering && selectedSubject && (
        <CreateSourceModal
          subjectId={selectedSubject.id}
          applicationId={selectedSubject.application_id}
          onClose={() => setRegistering(false)}
          onCreated={() => {
            setRegistering(false);
            sources.reload();
          }}
        />
      )}
    </>
  );
}
