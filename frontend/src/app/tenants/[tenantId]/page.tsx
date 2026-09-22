"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useMutation, useResource } from "@/lib/useResource";
import { Breadcrumbs } from "@/components/Shell";
import { DataTable } from "@/components/DataTable";
import { Field, Modal, Select, TextInput } from "@/components/form";
import { CreateApplicationModal } from "@/components/modals";
import {
  Button,
  ErrorState,
  InlineError,
  KeyValueGrid,
  LoadingState,
  Mono,
  PageHeader,
  Panel,
  StatusBadge,
} from "@/components/ui";

export default function TenantDetailPage() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const tenant = useResource(() => api.tenants.get(tenantId), [tenantId]);
  const applications = useResource(
    () => api.applications.list(tenantId),
    [tenantId],
  );
  const [editing, setEditing] = useState(false);
  const [creatingApp, setCreatingApp] = useState(false);

  if (tenant.loading) {
    return (
      <Panel>
        <LoadingState label="Loading tenant" />
      </Panel>
    );
  }
  if (tenant.error || !tenant.data) {
    return (
      <Panel>
        <ErrorState
          message={tenant.error ?? "Tenant not found"}
          onRetry={tenant.reload}
        />
      </Panel>
    );
  }

  return (
    <>
      <Breadcrumbs
        items={[
          { label: "Tenants", href: "/tenants" },
          { label: tenant.data.name },
        ]}
      />
      <PageHeader
        eyebrow="Tenant"
        title={tenant.data.name}
        actions={
          <>
            <Button onClick={() => setEditing(true)}>Edit</Button>
            <Button variant="primary" onClick={() => setCreatingApp(true)}>
              Create application
            </Button>
          </>
        }
      />

      <div className="mb-5">
        <Panel title="Tenant information">
          <KeyValueGrid
            items={[
              { label: "Tenant ID", value: <Mono>{tenant.data.id}</Mono> },
              { label: "Name", value: tenant.data.name },
              { label: "Status", value: <StatusBadge status={tenant.data.status} /> },
              { label: "Created", value: formatDate(tenant.data.created_at) },
              { label: "Subjects", value: tenant.data.subject_count },
              { label: "Sources", value: tenant.data.source_count },
            ]}
          />
        </Panel>
      </div>

      <Panel
        title="Applications"
        counter={applications.data?.length}
        description="Applications that talk to Memora on this tenant's behalf."
        actions={
          <Button variant="primary" onClick={() => setCreatingApp(true)}>
            Create application
          </Button>
        }
      >
        <DataTable
          rows={applications.data}
          loading={applications.loading}
          error={applications.error}
          onRetry={applications.reload}
          rowKey={(application) => application.id}
          empty={{
            title: "No applications in this tenant",
            description:
              "An application is what holds API credentials, actors and workspaces.",
            action: (
              <Button variant="primary" onClick={() => setCreatingApp(true)}>
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

      {editing && (
        <EditTenantModal
          tenantId={tenantId}
          initialName={tenant.data.name}
          initialStatus={tenant.data.status}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            tenant.reload();
          }}
        />
      )}
      {creatingApp && (
        <CreateApplicationModal
          tenantId={tenantId}
          onClose={() => setCreatingApp(false)}
          onCreated={() => {
            setCreatingApp(false);
            applications.reload();
            tenant.reload();
          }}
        />
      )}
    </>
  );
}

function EditTenantModal({
  tenantId,
  initialName,
  initialStatus,
  onClose,
  onSaved,
}: {
  tenantId: string;
  initialName: string;
  initialStatus: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(initialName);
  const [status, setStatus] = useState(initialStatus);
  const { mutate, pending, error } = useMutation(api.tenants.update);

  return (
    <Modal title="Edit tenant" onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          const saved = await mutate(tenantId, { name, status });
          if (saved) onSaved();
        }}
      >
        <Field label="Tenant name" required>
          <TextInput value={name} onChange={setName} disabled={pending} required />
        </Field>
        <Field
          label="Status"
          hint="Suspending a tenant immediately stops every API credential beneath it from authenticating."
        >
          <Select
            value={status}
            onChange={setStatus}
            disabled={pending}
            options={[
              { value: "active", label: "Active" },
              { value: "suspended", label: "Suspended" },
            ]}
          />
        </Field>
        {error && <InlineError message={error} />}
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={pending || !name.trim()}>
            {pending ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
