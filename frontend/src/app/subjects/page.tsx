"use client";

import Link from "next/link";
import { useState } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useResource } from "@/lib/useResource";
import { DataTable } from "@/components/DataTable";
import { Field, Select } from "@/components/form";
import { CreateSubjectModal } from "@/components/modals";
import {
  Button,
  ButtonLink,
  Mono,
  PageHeader,
  Panel,
  StatusBadge,
} from "@/components/ui";

export default function SubjectsPage() {
  const [applicationFilter, setApplicationFilter] = useState("");
  const applications = useResource(() => api.applications.list(), []);
  const subjects = useResource(
    () => api.subjects.list({ applicationId: applicationFilter || undefined }),
    [applicationFilter],
  );
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Subjects"
        description="A subject is a workspace — the boundary that knowledge is scoped to. Its external ID is your application's own identifier for the workspace, such as a case or customer number; Memora keeps its own UUID alongside it."
        actions={
          applicationFilter ? (
            <Button variant="primary" onClick={() => setCreating(true)}>
              Create subject
            </Button>
          ) : (
            <ButtonLink href="/applications" variant="normal">
              Choose an application to create in
            </ButtonLink>
          )
        }
      />

      <Panel
        title="All subjects"
        counter={subjects.data?.length}
        actions={
          <div className="w-72">
            <Field label="Filter by application">
              <Select
                value={applicationFilter}
                onChange={setApplicationFilter}
                placeholder="All applications"
                options={(applications.data ?? []).map((application) => ({
                  value: application.id,
                  label: `${application.name} (${application.tenant_name})`,
                }))}
              />
            </Field>
          </div>
        }
      >
        <DataTable
          rows={subjects.data}
          loading={subjects.loading}
          error={subjects.error}
          onRetry={subjects.reload}
          rowKey={(subject) => subject.id}
          empty={{
            title: "No subjects yet",
            description:
              "Subjects are created inside an application. Open one to add a workspace.",
            action: <ButtonLink href="/applications">Go to applications</ButtonLink>,
          }}
          columns={[
            {
              header: "Workspace",
              cell: (subject) => (
                <Link
                  href={`/subjects/${subject.id}`}
                  className="font-medium text-ink hover:underline"
                >
                  {subject.external_id}
                </Link>
              ),
            },
            {
              header: "Application",
              cell: (subject) => (
                <Link
                  href={`/applications/${subject.application_id}`}
                  className="text-ink-secondary hover:text-ink hover:underline"
                >
                  {subject.application_name}
                </Link>
              ),
            },
            {
              header: "Tenant",
              cell: (subject) => (
                <Link href={`/tenants/${subject.tenant_id}`} className="text-ink-secondary hover:text-ink hover:underline">
                  {subject.tenant_name}
                </Link>
              ),
            },
            {
              header: "Opened by",
              cell: (subject) =>
                subject.actor_external_id ?? <span className="text-ink-tertiary">–</span>,
            },
            {
              header: "Subject ID",
              cell: (subject) => <Mono>{subject.id.split("-")[0]}</Mono>,
            },
            {
              header: "Status",
              width: "110px",
              cell: (subject) => <StatusBadge status={subject.status} />,
            },
            {
              header: "Sources",
              align: "right",
              width: "90px",
              cell: (subject) => <span className="tabular-nums">{subject.source_count}</span>,
            },
            {
              header: "Created",
              align: "right",
              cell: (subject) => (
                <span className="text-ink-secondary">{formatDate(subject.created_at)}</span>
              ),
            },
          ]}
        />
      </Panel>

      {creating && applicationFilter && (
        <CreateSubjectModal
          applicationId={applicationFilter}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            subjects.reload();
          }}
        />
      )}
    </>
  );
}
