"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import { useResource } from "@/lib/useResource";
import { formatBytes, formatDate, formatRelative } from "@/lib/format";
import { ActivityCard, MetricTile } from "@/components/charts";
import {
  Button,
  ErrorState,
  LoadingState,
  PageHeader,
  Panel,
} from "@/components/ui";

/*
 * The dashboard is a metrics page, not a diagram.
 *
 * It answers two questions: how much is in the system (the tile strip), and
 * how fast it is arriving compared with the period before (the activity
 * cards). The status/type breakdowns and the recent-sources table lived here
 * too and were removed until there is enough data to make them worth reading;
 * `BreakdownBars` is still in charts.tsx for when they come back.
 *
 * Every number comes from GET /api/v1/stats and GET /api/v1/stats/metrics.
 * Nothing is derived from a downloaded list, and nothing is hard-coded.
 */

const WINDOWS = [7, 30, 90] as const;

/** 17px stroked icons for the tile strip, on the same 24-unit grid as the rail. */
const TILE_ICONS = {
  tenants: "M4 21V5.5L12 3l8 2.5V21M4 21h16M9 21v-4h6v4",
  applications: "M4 5.5h6v6H4zM14 5.5h6v6h-6zM4 14.5h6v6H4zM14 14.5h6v6h-6z",
  actors:
    "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM3 20c0-3 2.7-5 6-5s6 2 6 5",
  subjects:
    "M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.5h9A1.5 1.5 0 0 1 21 10v8.5A1.5 1.5 0 0 1 19.5 20h-15A1.5 1.5 0 0 1 3 18.5Z",
  sources:
    "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3ZM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  credentials:
    "M14.5 3a6.5 6.5 0 0 1 0 13 6.6 6.6 0 0 1-2.4-.45L10 18H8v2H6v2H3v-3l6.6-6.6A6.5 6.5 0 0 1 14.5 3Z",
  storage: "M4 7.5h16v4H4zM4 12.5h16v4H4zM7.5 9.5h.01M7.5 14.5h.01",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 7v5l3.5 2",
} as const;

function TileIcon({ name }: { name: keyof typeof TILE_ICONS }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-[17px]"
    >
      <path d={TILE_ICONS[name]} />
    </svg>
  );
}

export default function DashboardPage() {
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);

  const stats = useResource(() => api.stats(), []);
  const metrics = useResource(() => api.metrics(days), [days]);

  const busy = stats.refreshing || metrics.refreshing;

  function reloadAll() {
    stats.reload();
    metrics.reload();
  }

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Live counts, and how fast they are moving."
        actions={
          <div className="flex items-center gap-2">
            <WindowPicker value={days} onChange={setDays} />
            <Button onClick={reloadAll} disabled={busy}>
              {busy ? "Refreshing…" : "Refresh"}
            </Button>
          </div>
        }
      />

      {/* --------------------------------------------------- the current state */}
      {stats.loading ? (
        <Panel>
          <LoadingState label="Loading counts" />
        </Panel>
      ) : stats.error ? (
        <Panel>
          <ErrorState message={stats.error} onRetry={stats.reload} />
        </Panel>
      ) : (
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <MetricTile
            icon={<TileIcon name="tenants" />}
            label="Tenants"
            value={stats.data!.tenants}
            caption="Organizations"
            href="/tenants"
          />
          <MetricTile
            icon={<TileIcon name="applications" />}
            label="Applications"
            value={stats.data!.applications}
            caption="Consuming applications"
            href="/applications"
          />
          <MetricTile
            icon={<TileIcon name="actors" />}
            label="Actors"
            value={stats.data!.actors}
            caption="Users, services and agents"
            href="/actors"
          />
          <MetricTile
            icon={<TileIcon name="subjects" />}
            label="Subjects"
            value={stats.data!.subjects}
            caption="Workspaces"
            href="/subjects"
          />
          <MetricTile
            icon={<TileIcon name="sources" />}
            label="Sources"
            value={stats.data!.sources}
            caption="Registered knowledge"
            href="/sources"
          />
          <MetricTile
            icon={<TileIcon name="credentials" />}
            label="Active credentials"
            value={stats.data!.active_credentials}
            caption="Usable bearer tokens"
            href="/credentials"
          />
          <MetricTile
            icon={<TileIcon name="storage" />}
            label="Storage used"
            value={formatBytes(metrics.data?.storage_bytes)}
            caption={
              metrics.data
                ? `Largest ${formatBytes(metrics.data.largest_source_bytes)}`
                : "—"
            }
          />
          <MetricTile
            icon={<TileIcon name="clock" />}
            label="Last source"
            value={
              metrics.data?.last_source_at
                ? formatRelative(metrics.data.last_source_at)
                : "Never"
            }
            caption={
              metrics.data?.last_source_at
                ? formatDate(metrics.data.last_source_at)
                : "Nothing registered yet"
            }
          />
        </div>
      )}

      {/* -------------------------------------------------------- the movement */}
      {metrics.loading ? (
        <Panel>
          <LoadingState label="Loading activity" />
        </Panel>
      ) : metrics.error ? (
        <Panel>
          <ErrorState message={metrics.error} onRetry={metrics.reload} />
        </Panel>
      ) : (
        <div className="mb-4 grid gap-3 lg:grid-cols-3">
          <ActivityCard
            label="Sources registered"
            series={metrics.data!.sources}
            days={metrics.data!.days}
            unit="sources"
          />
          <ActivityCard
            label="Subjects opened"
            series={metrics.data!.subjects}
            days={metrics.data!.days}
            unit="subjects"
          />
          <ActivityCard
            label="Actors created"
            series={metrics.data!.actors}
            days={metrics.data!.days}
            unit="actors"
          />
        </div>
      )}
    </>
  );
}

/** The reporting window. Every chart on the page reads from it. */
function WindowPicker({
  value,
  onChange,
}: {
  value: number;
  onChange: (days: (typeof WINDOWS)[number]) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Reporting window"
      className="flex gap-1 rounded-md border border-line bg-surface p-1"
    >
      {WINDOWS.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onChange(option)}
          aria-pressed={value === option}
          className={`rounded-[4px] px-2.5 py-1 text-xs font-medium transition-colors ${
            value === option
              ? "bg-panel text-ink shadow-sm"
              : "text-ink-secondary hover:text-ink"
          }`}
        >
          {option}d
        </button>
      ))}
    </div>
  );
}
