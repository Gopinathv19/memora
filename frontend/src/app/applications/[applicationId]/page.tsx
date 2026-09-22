"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import { useMutation, useResource } from "@/lib/useResource";
import { Breadcrumbs } from "@/components/Shell";
import { DataTable } from "@/components/DataTable";
import {
  CreateActorModal,
  CreateCredentialModal,
  CreateSubjectModal,
} from "@/components/modals";
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

type Dialog = "credential" | "actor" | "subject" | undefined;

export default function ApplicationDetailPage() {
  const { applicationId } = useParams<{ applicationId: string }>();
  const application = useResource(
    () => api.applications.get(applicationId),
    [applicationId],
  );
  const credentials = useResource(
    () => api.credentials.list(applicationId),
    [applicationId],
  );
  const actors = useResource(
    () => api.actors.list({ applicationId }),
    [applicationId],
  );
  const subjects = useResource(
    () => api.subjects.list({ applicationId }),
    [applicationId],
  );
  const [dialog, setDialog] = useState<Dialog>();

  const revoke = useMutation(api.credentials.revoke);

  if (application.loading) {
    return (
      <Panel>
        <LoadingState label="Loading application" />
      </Panel>
    );
  }
  if (application.error || !application.data) {
    return (
      <Panel>
        <ErrorState
          message={application.error ?? "Application not found"}
          onRetry={application.reload}
        />
      </Panel>
    );
  }

  const app = application.data;

  return (
    <>
      <Breadcrumbs
        items={[
          { label: "Tenants", href: "/tenants" },
          { label: app.tenant_name ?? "Tenant", href: `/tenants/${app.tenant_id}` },
          { label: app.name },
        ]}
      />
      <PageHeader
        eyebrow="Application"
        title={app.name}
        actions={
          <>
            <Button onClick={() => setDialog("credential")}>Create credential</Button>
            <Button onClick={() => setDialog("actor")}>Create actor</Button>
            <Button variant="primary" onClick={() => setDialog("subject")}>
              Create subject
            </Button>
          </>
        }
      />

      <div className="mb-5">
        <Panel title="Application information">
          <KeyValueGrid
            items={[
              { label: "Application ID", value: <Mono>{app.id}</Mono> },
              { label: "Slug", value: <Mono>{app.slug}</Mono> },
              { label: "Status", value: <StatusBadge status={app.status} /> },
              {
                label: "Tenant",
                value: (
                  <Link href={`/tenants/${app.tenant_id}`} className="text-ink-secondary hover:text-ink hover:underline">
                    {app.tenant_name}
                  </Link>
                ),
              },
              { label: "Created", value: formatDate(app.created_at) },
              { label: "Sources", value: app.source_count },
            ]}
          />
        </Panel>
      </div>

      <div className="mb-5">
        <Panel
          title="API credentials"
          counter={credentials.data?.length}
          description="Bearer tokens this application uses to authenticate. Only a hash of each token is stored."
          actions={<Button onClick={() => setDialog("credential")}>Create credential</Button>}
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
                "This application cannot call the Memora API until it has a credential.",
              action: (
                <Button variant="primary" onClick={() => setDialog("credential")}>
                  Create credential
                </Button>
              ),
            }}
            columns={[
              { header: "Name", cell: (credential) => <span className="font-medium">{credential.name}</span> },
              { header: "Token", cell: (credential) => <Mono>{credential.token_preview}</Mono> },
              {
                header: "Status",
                width: "110px",
                cell: (credential) => <StatusBadge status={credential.status} />,
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
                      application.reload();
                    }}
                  >
                    Revoke
                  </Button>
                ),
              },
            ]}
          />
        </Panel>
      </div>

      <div className="mb-5">
        <Panel
          title="Actors"
          counter={actors.data?.length}
          description="Users, services and agents that operate on this application's workspaces."
          actions={<Button onClick={() => setDialog("actor")}>Create actor</Button>}
        >
          <DataTable
            rows={actors.data}
            loading={actors.loading}
            error={actors.error}
            onRetry={actors.reload}
            rowKey={(actor) => actor.id}
            empty={{
              title: "No actors",
              description:
                "An actor records who caused a workspace to exist. Subjects can also be created without one.",
              action: (
                <Button variant="primary" onClick={() => setDialog("actor")}>
                  Create actor
                </Button>
              ),
            }}
            columns={[
              {
                header: "External ID",
                cell: (actor) => (
                  <Link href={`/actors?application_id=${applicationId}`} className="font-medium text-ink hover:underline">
                    {actor.external_id}
                  </Link>
                ),
              },
              { header: "Name", cell: (actor) => actor.name ?? "–" },
              { header: "Type", width: "100px", cell: (actor) => <TypeTag value={actor.type} /> },
              { header: "Status", width: "110px", cell: (actor) => <StatusBadge status={actor.status} /> },
              {
                header: "Subjects",
                align: "right",
                width: "90px",
                cell: (actor) => <span className="tabular-nums">{actor.subject_count}</span>,
              },
              { header: "Created", align: "right", cell: (actor) => formatDate(actor.created_at) },
            ]}
          />
        </Panel>
      </div>

      <Panel
        title="Subjects"
        counter={subjects.data?.length}
        description="Workspaces inside this application. Sources are always scoped to one."
        actions={
          <Button variant="primary" onClick={() => setDialog("subject")}>
            Create subject
          </Button>
        }
      >
        <DataTable
          rows={subjects.data}
          loading={subjects.loading}
          error={subjects.error}
          onRetry={subjects.reload}
          rowKey={(subject) => subject.id}
          empty={{
            title: "No subjects",
            description: "A subject is the workspace that holds sources.",
            action: (
              <Button variant="primary" onClick={() => setDialog("subject")}>
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
                  className="font-medium text-ink hover:underline"
                >
                  {subject.external_id}
                </Link>
              ),
            },
            {
              header: "Opened by",
              cell: (subject) => subject.actor_external_id ?? <span className="text-ink-tertiary">–</span>,
            },
            { header: "Status", width: "110px", cell: (subject) => <StatusBadge status={subject.status} /> },
            {
              header: "Sources",
              align: "right",
              width: "90px",
              cell: (subject) => <span className="tabular-nums">{subject.source_count}</span>,
            },
            { header: "Created", align: "right", cell: (subject) => formatDate(subject.created_at) },
          ]}
        />
      </Panel>

      {dialog === "credential" && (
        <CreateCredentialModal
          applicationId={applicationId}
          onClose={() => setDialog(undefined)}
          onCreated={() => {
            credentials.reload();
            application.reload();
          }}
        />
      )}
      {dialog === "actor" && (
        <CreateActorModal
          applicationId={applicationId}
          onClose={() => setDialog(undefined)}
          onCreated={() => {
            setDialog(undefined);
            actors.reload();
            application.reload();
          }}
        />
      )}
      {dialog === "subject" && (
        <CreateSubjectModal
          applicationId={applicationId}
          onClose={() => setDialog(undefined)}
          onCreated={() => {
            setDialog(undefined);
            subjects.reload();
            application.reload();
          }}
        />
      )}
    </>
  );
}
