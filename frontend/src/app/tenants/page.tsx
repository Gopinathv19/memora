"use client";

import Link from "next/link";
import { useState } from "react";

import { api } from "@/lib/api";
import { formatDate, shortId } from "@/lib/format";
import { useMutation, useResource } from "@/lib/useResource";
import { DataTable } from "@/components/DataTable";
import { Field, Modal, TextInput } from "@/components/form";
import {
  Button,
  InlineError,
  Mono,
  PageHeader,
  Panel,
  StatusBadge,
} from "@/components/ui";

export default function TenantsPage() {
  const tenants = useResource(() => api.tenants.list(), []);
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Tenants"
        description="A tenant is the organization that owns data inside Memora. Everything else hangs beneath one."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            Create tenant
          </Button>
        }
      />

      <Panel title="All tenants" counter={tenants.data?.length}>
        <DataTable
          rows={tenants.data}
          loading={tenants.loading}
          error={tenants.error}
          onRetry={tenants.reload}
          rowKey={(tenant) => tenant.id}
          empty={{
            title: "No tenants yet",
            description: "Create your first tenant to start using Memora.",
            action: (
              <Button variant="primary" onClick={() => setCreating(true)}>
                Create tenant
              </Button>
            ),
          }}
          columns={[
            {
              header: "Name",
              cell: (tenant) => (
                <Link
                  href={`/tenants/${tenant.id}`}
                  className="font-medium text-ink hover:underline"
                >
                  {tenant.name}
                </Link>
              ),
            },
            {
              header: "Tenant ID",
              cell: (tenant) => <Mono>{shortId(tenant.id)}</Mono>,
            },
            {
              header: "Status",
              width: "110px",
              cell: (tenant) => <StatusBadge status={tenant.status} />,
            },
            {
              header: "Applications",
              align: "right",
              width: "110px",
              cell: (tenant) => <span className="tabular-nums">{tenant.application_count}</span>,
            },
            {
              header: "Subjects",
              align: "right",
              width: "90px",
              cell: (tenant) => <span className="tabular-nums">{tenant.subject_count}</span>,
            },
            {
              header: "Sources",
              align: "right",
              width: "90px",
              cell: (tenant) => <span className="tabular-nums">{tenant.source_count}</span>,
            },
            {
              header: "Created",
              align: "right",
              cell: (tenant) => (
                <span className="text-ink-secondary">{formatDate(tenant.created_at)}</span>
              ),
            },
          ]}
        />
      </Panel>

      {creating && (
        <CreateTenantModal
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            tenants.reload();
          }}
        />
      )}
    </>
  );
}

function CreateTenantModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const { mutate, pending, error } = useMutation(api.tenants.create);

  return (
    <Modal
      title="Create tenant"
      description="The organization that will own applications, workspaces and sources."
      onClose={onClose}
    >
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          const created = await mutate({ name });
          if (created) onCreated();
        }}
      >
        <Field label="Tenant name" required hint="For example, Acme Corporation.">
          <TextInput
            value={name}
            onChange={setName}
            placeholder="Acme Corporation"
            disabled={pending}
            required
          />
        </Field>
        {error && <InlineError message={error} />}
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={pending || !name.trim()}>
            {pending ? "Creating…" : "Create tenant"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
