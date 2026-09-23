"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useResource } from "@/lib/useResource";
import { DataTable } from "@/components/DataTable";
import { Field, Select } from "@/components/form";
import { CreateSubjectModal } from "@/components/modals";
import { Button, Mono, PageHeader, Panel, StatusBadge } from "@/components/ui";

export default function SubjectsPage() {
  const search = useSearchParams();
  const [applicationFilter, setApplicationFilter] = useState(
    () => search.get("application_id") ?? "",
  );
  const [rootsOnly, setRootsOnly] = useState(() => (search.get("roots_only") ?? "true") === "true");
  // Optional: actor filter picked up from the URL if present
  const actorIdFromUrl = search.get("actor_id") ?? undefined;
  const applications = useResource(() => api.applications.list(), []);
  const subjects = useResource(
    () =>
      api.subjects.list({
        applicationId: applicationFilter || undefined,
        actorId: actorIdFromUrl || undefined,
        rootsOnly,
      }),
    [applicationFilter, rootsOnly, actorIdFromUrl],
  );
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Subjects"
        description="A subject is a workspace — the boundary that knowledge is scoped to. Its external ID is your application's own identifier for the workspace, such as a case or customer number; Memora keeps its own UUID alongside it."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            Create subject
          </Button>
        }
      />

      <Panel
        title="All subjects"
        counter={subjects.data?.length}
        actions={
          <div className="flex items-end gap-3">
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
            <div className="w-44">
              <Field label="Scope">
                <Select
                  value={rootsOnly ? "roots" : "all"}
                  onChange={(value) => setRootsOnly(value === "roots")}
                  options={[
                    { value: "roots", label: "Root workspaces only" },
                    { value: "all", label: "Include folders" },
                  ]}
                />
              </Field>
            </div>
            {actorIdFromUrl && (
              <span className="pb-2 text-sm text-ink-tertiary">Filtered by actor</span>
            )}
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
              "A subject belongs to one application, and the create form asks which.",
            action: (
              <Button variant="primary" onClick={() => setCreating(true)}>
                Create subject
              </Button>
            ),
          }}
          columns={[
            {
              header: "Workspace",
              cell: (subject) => (
                <Link
                  href={`/subjects/${subject.id}`}
                  className="font-medium text-ink hover:underline flex items-center gap-2"
                >
                  {/* inline folder icon for folders */}
                  {subject.parent_subject_id && (
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
                  )}
                  <span>{subject.external_id}</span>
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
              header: "Folders",
              align: "right",
              width: "90px",
              cell: (subject) => (
                <span className="tabular-nums">{subject.child_count}</span>
              ),
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

      {creating && (
        <CreateSubjectModal
          // The filter is a starting point, not a precondition: the form
          // asks for one when nothing is filtered.
          applicationId={applicationFilter || undefined}
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
