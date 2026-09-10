"use client";

import Link from "next/link";
import { useState } from "react";

import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import { useMutation, useResource } from "@/lib/useResource";
import { DataTable } from "@/components/DataTable";
import { Field, Select } from "@/components/form";
import { CreateCredentialModal } from "@/components/modals";
import {
  Button,
  ButtonLink,
  ErrorState,
  Mono,
  PageHeader,
  Panel,
  StatusBadge,
} from "@/components/ui";

/** True when a credential has an expiry that has already passed. */
function isExpired(expiresAt: string | null): boolean {
  return !!expiresAt && new Date(expiresAt).getTime() <= Date.now();
}

export default function CredentialsPage() {
  const [applicationFilter, setApplicationFilter] = useState("");
  const applications = useResource(() => api.applications.list(), []);
  const credentials = useResource(
    () => api.credentials.list(applicationFilter || undefined),
    [applicationFilter],
  );
  const [creating, setCreating] = useState(false);
  const revoke = useMutation(api.credentials.revoke);

  const applicationName = (id: string) =>
    applications.data?.find((application) => application.id === id)?.name ?? id.split("-")[0];

  return (
    <>
      <PageHeader
        title="API credentials"
        description="Bearer tokens that applications use to authenticate. Memora stores only a keyed hash of each token — the token itself is shown once, at creation, and cannot be recovered afterwards."
        actions={
          applicationFilter ? (
            <Button variant="primary" onClick={() => setCreating(true)}>
              Create credential
            </Button>
          ) : (
            <ButtonLink href="/applications" variant="normal">
              Choose an application to create in
            </ButtonLink>
          )
        }
      />

      <Panel
        title="All credentials"
        counter={credentials.data?.length}
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
        {revoke.error && (
          <div className="px-4 pt-3">
            <ErrorState message={revoke.error} />
          </div>
        )}
        <DataTable
          rows={credentials.data}
          loading={credentials.loading}
          error={credentials.error}
          onRetry={credentials.reload}
          rowKey={(credential) => credential.id}
          empty={{
            title: "No API credentials",
            description:
              "Credentials are created inside an application. Open one to issue a token.",
            action: <ButtonLink href="/applications">Go to applications</ButtonLink>,
          }}
          columns={[
            { header: "Name", cell: (credential) => <span className="font-bold">{credential.name}</span> },
            {
              header: "Application",
              cell: (credential) => (
                <Link
                  href={`/applications/${credential.application_id}`}
                  className="text-accent hover:underline"
                >
                  {applicationName(credential.application_id)}
                </Link>
              ),
            },
            {
              header: "Token",
              cell: (credential) => <Mono>{credential.token_preview}</Mono>,
            },
            {
              header: "Status",
              width: "110px",
              cell: (credential) => (
                <StatusBadge
                  status={
                    isExpired(credential.expires_at) && credential.status === "active"
                      ? "expired"
                      : credential.status
                  }
                />
              ),
            },
            { header: "Created", cell: (credential) => formatDate(credential.created_at) },
            {
              header: "Last used",
              cell: (credential) => (
                <span className="text-ink-secondary">
                  {formatRelative(credential.last_used_at)}
                </span>
              ),
            },
            {
              header: "Expires",
              cell: (credential) =>
                credential.expires_at ? formatDate(credential.expires_at) : "Never",
            },
            {
              header: "",
              align: "right",
              width: "90px",
              cell: (credential) => (
                <Button
                  variant="danger"
                  disabled={revoke.pending}
                  onClick={async () => {
                    if (
                      !window.confirm(
                        `Revoke "${credential.name}"? Any client still using this token will immediately start failing authentication.`,
                      )
                    )
                      return;
                    await revoke.mutate(credential.id);
                    credentials.reload();
                  }}
                >
                  Revoke
                </Button>
              ),
            },
          ]}
        />
      </Panel>

      {creating && applicationFilter && (
        <CreateCredentialModal
          applicationId={applicationFilter}
          onClose={() => setCreating(false)}
          onCreated={() => credentials.reload()}
        />
      )}
    </>
  );
}
