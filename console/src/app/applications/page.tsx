"use client";

import Link from "next/link";
import { useState } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useResource } from "@/lib/useResource";
import { useTenant } from "@/lib/tenant-context";
import { DataTable } from "@/components/DataTable";
import { CreateApplicationModal } from "@/components/modals";
import {
  Button,
  Mono,
  PageHeader,
  Panel,
  StatusBadge,
} from "@/components/ui";

export default function ApplicationsPage() {
  // The active tenant comes from the top-bar switcher, not a per-page filter.
  const { tenant } = useTenant();
  const applications = useResource(
    () => api.applications.list(tenant?.id),
    [tenant?.id],
  );
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Applications"
        description="Each application belongs to one tenant and holds its own API credentials, actors and workspaces."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            Create application
          </Button>
        }
      />

      <Panel
        title={tenant ? `Applications in ${tenant.name}` : "All applications"}
        counter={applications.data?.length}
      >
        <DataTable
          rows={applications.data}
          loading={applications.loading}
          error={applications.error}
          onRetry={applications.reload}
          rowKey={(application) => application.id}
          empty={{
            title: tenant
              ? "No applications in this tenant"
              : "No applications yet",
            description:
              "An application belongs to one tenant, and the create form asks which.",
            action: (
              <Button variant="primary" onClick={() => setCreating(true)}>
                Create application
              </Button>
            ),
          }}
          columns={[
            {
              header: "Name",
              cell: (application) => (
                <Link
                  href={`/applications/${application.id}`}
                  className="font-medium text-ink hover:underline"
                >
                  {application.name}
                </Link>
              ),
            },
            { header: "Slug", cell: (application) => <Mono>{application.slug}</Mono> },
            {
              header: "Tenant",
              cell: (application) => (
                <Link
                  href={`/tenants/${application.tenant_id}`}
                  className="text-ink-secondary hover:text-ink hover:underline"
                >
                  {application.tenant_name}
                </Link>
              ),
            },
            {
              header: "Status",
              width: "110px",
              cell: (application) => <StatusBadge status={application.status} />,
            },
            {
              header: "Credentials",
              align: "right",
              width: "110px",
              cell: (application) => (
                <span className="tabular-nums">{application.credential_count}</span>
              ),
            },
            {
              header: "Actors",
              align: "right",
              width: "80px",
              cell: (application) => (
                <span className="tabular-nums">{application.actor_count}</span>
              ),
            },
            {
              header: "Subjects",
              align: "right",
              width: "90px",
              cell: (application) => (
                <span className="tabular-nums">{application.subject_count}</span>
              ),
            },
            {
              header: "Sources",
              align: "right",
              width: "90px",
              cell: (application) => (
                <span className="tabular-nums">{application.source_count}</span>
              ),
            },
            {
              header: "Created",
              align: "right",
              cell: (application) => (
                <span className="text-ink-secondary">
                  {formatDate(application.created_at)}
                </span>
              ),
            },
          ]}
        />
      </Panel>

      {creating && (
        <CreateApplicationModal
          // The active tenant pre-fills the form; with none selected the
          // form asks which tenant, as before.
          tenantId={tenant?.id}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            applications.reload();
          }}
        />
      )}
    </>
  );
}
