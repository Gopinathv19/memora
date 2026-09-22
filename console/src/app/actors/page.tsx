"use client";

import Link from "next/link";
import { useState } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useResource } from "@/lib/useResource";
import { DataTable } from "@/components/DataTable";
import { Field, Select } from "@/components/form";
import { CreateActorModal } from "@/components/modals";
import {
  Button,
  Mono,
  PageHeader,
  Panel,
  StatusBadge,
  TypeTag,
} from "@/components/ui";

export default function ActorsPage() {
  const [applicationFilter, setApplicationFilter] = useState("");
  const applications = useResource(() => api.applications.list(), []);
  const actors = useResource(
    () => api.actors.list({ applicationId: applicationFilter || undefined }),
    [applicationFilter],
  );
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Actors"
        description="An actor is whoever or whatever operates on Memora inside an application — a person, a service account, an agent or a system job. Memora holds no passwords or profiles for actors: it stores only your own identifier, so your identity system stays yours."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            Create actor
          </Button>
        }
      />

      <Panel
        title="All actors"
        counter={actors.data?.length}
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
          rows={actors.data}
          loading={actors.loading}
          error={actors.error}
          onRetry={actors.reload}
          rowKey={(actor) => actor.id}
          empty={{
            title: "No actors yet",
            description:
              "A actor belongs to one application, and the create form asks which.",
            action: (
              <Button variant="primary" onClick={() => setCreating(true)}>
                Create actor
              </Button>
            ),
          }}
          columns={[
            {
              header: "External ID",
              cell: (actor) => <span className="font-medium">{actor.external_id}</span>,
            },
            { header: "Name", cell: (actor) => actor.name ?? <span className="text-ink-tertiary">–</span> },
            { header: "Type", width: "100px", cell: (actor) => <TypeTag value={actor.type} /> },
            {
              header: "Application",
              cell: (actor) => (
                <Link
                  href={`/applications/${actor.application_id}`}
                  className="text-ink-secondary hover:text-ink hover:underline"
                >
                  {actor.application_name}
                </Link>
              ),
            },
            { header: "Actor ID", cell: (actor) => <Mono>{actor.id.split("-")[0]}</Mono> },
            { header: "Status", width: "110px", cell: (actor) => <StatusBadge status={actor.status} /> },
            {
              header: "Subjects",
              align: "right",
              width: "90px",
              cell: (actor) => (
                <Link
                  href={`/subjects?actor=${actor.id}`}
                  className="tabular-nums text-ink-secondary hover:text-ink hover:underline"
                >
                  {actor.subject_count}
                </Link>
              ),
            },
            {
              header: "Created",
              align: "right",
              cell: (actor) => (
                <span className="text-ink-secondary">{formatDate(actor.created_at)}</span>
              ),
            },
          ]}
        />
      </Panel>

      {creating && (
        <CreateActorModal
          // The filter is a starting point, not a precondition: the form
          // asks for one when nothing is filtered.
          applicationId={applicationFilter || undefined}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            actors.reload();
          }}
        />
      )}
    </>
  );
}
