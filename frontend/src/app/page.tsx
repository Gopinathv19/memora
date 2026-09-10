"use client";

import Link from "next/link";

import { api } from "@/lib/api";
import { useResource } from "@/lib/useResource";
import { formatDate } from "@/lib/format";
import { DataTable } from "@/components/DataTable";
import {
  Button,
  ErrorState,
  LoadingState,
  PageHeader,
  Panel,
  StatusBadge,
  TypeTag,
} from "@/components/ui";

/** Every number on this page comes from GET /api/v1/stats. Nothing is hard-coded. */
export default function DashboardPage() {
  const stats = useResource(() => api.stats(), []);
  const recent = useResource(() => api.sources.list(), []);

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Memora tracks knowledge through one ownership chain: a tenant owns applications, an application knows its actors, an actor opens subjects, and subjects hold sources."
        actions={
          <Button onClick={() => { stats.reload(); recent.reload(); }} disabled={stats.refreshing}>
            {stats.refreshing ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      {stats.loading ? (
        <Panel>
          <LoadingState label="Loading counts" />
        </Panel>
      ) : stats.error ? (
        <Panel>
          <ErrorState message={stats.error} onRetry={stats.reload} />
        </Panel>
      ) : (
        <>
          <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <StatTile label="Tenants" value={stats.data!.tenants} href="/tenants" caption="Organizations" />
            <StatTile label="Applications" value={stats.data!.applications} href="/applications" caption="Consuming applications" />
            <StatTile label="Actors" value={stats.data!.actors} href="/actors" caption="Users, services and agents" />
            <StatTile label="Subjects" value={stats.data!.subjects} href="/subjects" caption="Workspaces" />
            <StatTile label="Sources" value={stats.data!.sources} href="/sources" caption="Registered knowledge" />
            <StatTile
              label="Active API credentials"
              value={stats.data!.active_credentials}
              href="/credentials"
              caption="Usable bearer tokens"
            />
          </div>

          <div className="mb-5 grid gap-5 lg:grid-cols-2">
            <Panel
              title="Sources by status"
              description="Nothing advances a source past pending yet — extraction is the next phase."
            >
              <SourceStatusBreakdown
                counts={stats.data!.sources_by_status}
                total={stats.data!.sources}
              />
            </Panel>
            <Panel title="Ownership chain">
              <ChainDiagram stats={stats.data!} />
            </Panel>
          </div>
        </>
      )}

      <Panel
        title="Recently registered sources"
        counter={recent.data?.length}
        actions={<Link href="/sources" className="text-sm font-bold text-accent hover:underline">View all</Link>}
      >
        <DataTable
          rows={recent.data?.slice(0, 8)}
          loading={recent.loading}
          error={recent.error}
          onRetry={recent.reload}
          rowKey={(source) => source.id}
          empty={{
            title: "No sources registered yet",
            description:
              "Create a tenant, an application and a subject, then register a source against it.",
          }}
          columns={[
            {
              header: "Source",
              cell: (source) => (
                <Link href={`/sources/${source.id}`} className="font-bold text-accent hover:underline">
                  {source.filename ?? source.storage_uri ?? source.id.split("-")[0]}
                </Link>
              ),
            },
            { header: "Type", width: "90px", cell: (source) => <TypeTag value={source.type} /> },
            {
              header: "Subject",
              cell: (source) => (
                <Link href={`/subjects/${source.subject_id}`} className="text-accent hover:underline">
                  {source.subject_external_id}
                </Link>
              ),
            },
            { header: "Application", cell: (source) => source.application_name ?? "–" },
            { header: "Status", width: "110px", cell: (source) => <StatusBadge status={source.status} /> },
            {
              header: "Registered",
              align: "right",
              cell: (source) => <span className="text-ink-secondary">{formatDate(source.created_at)}</span>,
            },
          ]}
        />
      </Panel>
    </>
  );
}

function StatTile({
  label,
  value,
  href,
  caption,
}: {
  label: string;
  value: number;
  href: string;
  caption: string;
}) {
  return (
    <Link
      href={href}
      className="group rounded-lg border border-line bg-panel p-4 shadow-sm transition-colors hover:border-accent"
    >
      <div className="text-xs font-bold uppercase tracking-wide text-ink-tertiary">
        {label}
      </div>
      <div className="mt-1 text-3xl font-bold text-ink tabular-nums group-hover:text-accent">
        {value}
      </div>
      <div className="mt-0.5 text-xs text-ink-secondary">{caption}</div>
    </Link>
  );
}

const STATUS_ORDER = ["pending", "processing", "completed", "failed"] as const;

const STATUS_BAR: Record<string, string> = {
  pending: "bg-warn",
  processing: "bg-accent",
  completed: "bg-ok",
  failed: "bg-danger",
};

function SourceStatusBreakdown({
  counts,
  total,
}: {
  counts: Record<string, number>;
  total: number;
}) {
  if (total === 0) {
    return (
      <p className="px-4 py-8 text-center text-sm text-ink-secondary">
        No sources registered yet.
      </p>
    );
  }
  return (
    <div className="space-y-3 p-4">
      {STATUS_ORDER.map((status) => {
        const count = counts[status] ?? 0;
        const percent = total ? Math.round((count / total) * 100) : 0;
        return (
          <div key={status}>
            <div className="mb-1 flex items-baseline justify-between text-sm">
              <StatusBadge status={status} />
              <span className="tabular-nums text-ink-secondary">
                {count} <span className="text-ink-tertiary">({percent}%)</span>
              </span>
            </div>
            <div
              className="h-1.5 overflow-hidden rounded-full bg-muted-soft"
              role="img"
              aria-label={`${count} of ${total} sources are ${status}`}
            >
              <div
                className={`h-full rounded-full ${STATUS_BAR[status]}`}
                style={{ width: `${percent}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The chain, rendered with the live count at each level. Reading it top to
 * bottom is the fastest way to understand what Memora stores.
 */
function ChainDiagram({
  stats,
}: {
  stats: {
    tenants: number;
    applications: number;
    actors: number;
    subjects: number;
    sources: number;
  };
}) {
  const levels = [
    { label: "Tenant", sub: "Organization that owns the data", count: stats.tenants, href: "/tenants" },
    { label: "Application", sub: "Consuming application", count: stats.applications, href: "/applications" },
    { label: "Actor", sub: "Who or what operates on it", count: stats.actors, href: "/actors" },
    { label: "Subject", sub: "The workspace / data boundary", count: stats.subjects, href: "/subjects" },
    { label: "Source", sub: "Registered knowledge", count: stats.sources, href: "/sources" },
  ];
  return (
    <ol className="p-4">
      {levels.map((level, index) => (
        <li key={level.label}>
          <Link
            href={level.href}
            className="flex items-center gap-3 rounded border border-line px-3 py-2 hover:border-accent hover:bg-accent-soft"
            style={{ marginLeft: `${index * 14}px` }}
          >
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-bold text-ink">{level.label}</span>
              <span className="block text-xs text-ink-secondary">{level.sub}</span>
            </span>
            <span className="tabular-nums text-sm font-bold text-ink">{level.count}</span>
          </Link>
          {index < levels.length - 1 && (
            <span
              aria-hidden
              className="block h-3 border-l border-line"
              style={{ marginLeft: `${index * 14 + 16}px` }}
            />
          )}
        </li>
      ))}
    </ol>
  );
}
