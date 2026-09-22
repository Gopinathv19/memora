"use client";

import Link from "next/link";
import { useState } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useResource } from "@/lib/useResource";
import { DataTable } from "@/components/DataTable";
import { Field, Select } from "@/components/form";
import { CreateApplicationModal } from "@/components/modals";
import {
  Button,
  ButtonLink,
  Mono,
  PageHeader,
  Panel,
  StatusBadge,
} from "@/components/ui";

export default function ApplicationsPage() {
  const [tenantFilter, setTenantFilter] = useState("");
  const tenants = useResource(() => api.tenants.list(), []);
  const applications = useResource(
    () => api.applications.list(tenantFilter || undefined),
    [tenantFilter],
  );
  const [creatingIn, setCreatingIn] = useState<string>();

  return (
    <>
      <PageHeader
        title="Applications"
        description="Each application belongs to one tenant and holds its own API credentials, actors and workspaces."
        actions={
          tenantFilter ? (
            <Button variant="primary" onClick={() => setCreatingIn(tenantFilter)}>
              Create application
            </Button>
          ) : (
            <ButtonLink href="/tenants" variant="normal">
              Choose a tenant to create in
            </ButtonLink>
          )
        }
      />

      <Panel
        title="All applications"
        counter={applications.data?.length}
        actions={
          <div className="w-64">
            <Field label="Filter by tenant">
              <Select
                value={tenantFilter}
                onChange={setTenantFilter}
                placeholder="All tenants"
                options={(tenants.data ?? []).map((tenant) => ({
                  value: tenant.id,
                  label: tenant.name,
                }))}
              />
            </Field>
          </div>
        }
      >
        <DataTable
          rows={applications.data}
          loading={applications.loading}
          error={applications.error}
          onRetry={applications.reload}
          rowKey={(application) => application.id}
          empty={{
            title: tenantFilter
              ? "No applications in this tenant"
              : "No applications yet",
            description:
              "Applications are created inside a tenant. Open a tenant to add one.",
            action: <ButtonLink href="/tenants">Go to tenants</ButtonLink>,
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

      {creatingIn && (
        <CreateApplicationModal
          tenantId={creatingIn}
          onClose={() => setCreatingIn(undefined)}
          onCreated={() => {
            setCreatingIn(undefined);
            applications.reload();
          }}
        />
      )}
    </>
  );
}
