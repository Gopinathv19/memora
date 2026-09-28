"use client";

/**
 * The Embeddings dashboard: pipeline health at a glance.
 *
 * One GET /embedding/stats drives the health cards (total chunks, embedded,
 * pending, processing, failed, stale, coverage %); one GET /embedding/strategies
 * lists the configured strategies with their model and dimension. Actions:
 * retry failed embeddings, and force a rebuild of the active strategy.
 *
 * The numbers are scope-aware: a console user sees every tenant they own,
 * a credential sees its own application. Polling while work is in flight
 * shows the queue draining without a manual refresh.
 */

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { EmbeddingStats, EmbeddingStrategy } from "@/lib/types";
import { useResource, useMutation } from "@/lib/useResource";
import {
  Button,
  ErrorState,
  LoadingState,
  PageHeader,
  Panel,
  StatusBadge,
  Mono,
} from "@/components/ui";

/** A number card. Same quiet style as the dashboard's tiles. */
function StatCard({
  label,
  value,
  tone = "",
}: {
  label: string;
  value: string | number;
  tone?: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-panel px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-ink-tertiary">
        {label}
      </div>
      <div className={`mt-1 text-xl font-medium ${tone || "text-ink"}`}>{value}</div>
    </div>
  );
}

export default function EmbeddingsPage() {
  const stats = useResource(() => api.embeddings.stats(), []);
  const strategies = useResource(() => api.embeddings.strategies.list(), []);

  const [notice, setNotice] = useState<string | null>(null);

  const retry = useMutation(() => api.embeddings.retryFailed());
  const rebuild = useMutation(async (id: string) => {
    const r = await api.embeddings.strategies.rebuild(id);
    return r;
  });

  // While work is in flight, poll so the queue visibly drains.
  const inFlight =
    (stats.data?.pending ?? 0) > 0 || (stats.data?.processing ?? 0) > 0;
  useEffect(() => {
    if (!inFlight) return;
    const t = setInterval(() => stats.reload(), 3000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inFlight]);

  async function onRetryFailed() {
    setNotice(null);
    const result = await retry.mutate();
    if (result) {
      setNotice(
        result.reset > 0
          ? `Reset ${result.reset} failed embedding(s) to pending; the worker is re-embedding them.`
          : "No failed embeddings to retry.",
      );
      stats.reload();
    }
  }

  async function onRebuild(strategy: EmbeddingStrategy) {
    setNotice(null);
    const result = await rebuild.mutate(strategy.id);
    if (result) {
      setNotice(
        `Queued ${result.enqueued} chunk(s) for re-embedding under ${result.strategy_name}.`,
      );
      stats.reload();
    }
  }

  const s = stats.data;
  const activeStrategy = strategies.data?.find((x) => x.is_active);

  return (
    <>
      <PageHeader
        eyebrow="Processing"
        title="Embeddings"
        description="Vector coverage of active retrieval chunks, and the strategies that produce them."
        actions={
          <Button onClick={onRetryFailed} disabled={retry.pending}>
            {retry.pending ? "Retrying…" : "Retry failed"}
          </Button>
        }
      />

      {(retry.error || rebuild.error || notice) && (
        <div className="mb-4">
          {retry.error && <ErrorState message={retry.error} />}
          {rebuild.error && <ErrorState message={rebuild.error} />}
          {notice && !retry.error && !rebuild.error && (
            <div
              role="status"
              className="rounded-md border border-ok/30 bg-ok-soft px-4 py-2.5 text-sm text-ink"
            >
              {notice}
            </div>
          )}
        </div>
      )}

      {/* --------------------------------------------------------- health cards */}
      {stats.loading ? (
        <Panel>
          <LoadingState label="Loading embedding stats" />
        </Panel>
      ) : stats.error ? (
        <Panel>
          <ErrorState message={stats.error} onRetry={stats.reload} />
        </Panel>
      ) : s ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
          <StatCard label="Total chunks" value={s.total_chunks} />
          <StatCard label="Embedded" value={s.embedded} tone="text-ok" />
          <StatCard label="Pending" value={s.pending} tone="text-warn" />
          <StatCard label="Processing" value={s.processing} tone="text-info" />
          <StatCard label="Failed" value={s.failed} tone="text-danger" />
          <StatCard label="Stale" value={s.stale} tone="text-ink-tertiary" />
          <StatCard
            label="Coverage"
            value={`${s.coverage_percent}%`}
            tone={s.coverage_percent >= 100 ? "text-ok" : "text-ink"}
          />
        </div>
      ) : null}

      {/* ----------------------------------------------------------- strategies */}
      <div className="mt-6">
        {strategies.loading ? (
          <Panel title="Strategies">
            <LoadingState label="Loading strategies" />
          </Panel>
        ) : strategies.error ? (
          <Panel title="Strategies">
            <ErrorState message={strategies.error} onRetry={strategies.reload} />
          </Panel>
        ) : (
          <Panel
            title="Strategies"
            description="How Memora turns chunks into vectors. Configuration is immutable; a materially different setup is a new strategy."
            counter={strategies.data?.length}
          >
            {strategies.data && strategies.data.length > 0 ? (
              <ul className="divide-y divide-line-soft">
                {strategies.data.map((strategy) => (
                  <li
                    key={strategy.id}
                    className="flex flex-wrap items-start justify-between gap-3 px-4 py-3"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium text-ink">
                          {strategy.name}
                        </span>
                        <StatusBadge
                          status={strategy.is_active ? "active" : "suspended"}
                        />
                      </div>
                      {strategy.description && (
                        <p className="mt-0.5 max-w-2xl text-xs text-ink-secondary">
                          {strategy.description}
                        </p>
                      )}
                      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-tertiary">
                        <span>
                          Model:{" "}
                          <Mono>{strategy.model_identifier ?? "—"}</Mono>
                        </span>
                        <span>Dimension: {strategy.dimension}</span>
                        <span>Normalization: {strategy.normalization}</span>
                        <span>Metric: {strategy.similarity_metric}</span>
                        <span>Input: {strategy.input_type}</span>
                      </div>
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <Button
                        onClick={() => onRebuild(strategy)}
                        disabled={
                          rebuild.pending || strategy.id !== activeStrategy?.id
                        }
                        title={
                          strategy.id !== activeStrategy?.id
                            ? "Only the active strategy can be rebuilt"
                            : "Force re-embed every chunk in scope under this strategy"
                        }
                      >
                        {rebuild.pending ? "Rebuilding…" : "Rebuild"}
                      </Button>
                    </div>
                    {strategy.id === activeStrategy?.id && (
                      <div className="w-full text-xs text-ink-tertiary">
                        Active strategy — used by auto-embed and new enqueues.
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="px-4 py-10 text-center text-sm text-ink-secondary">
                No embedding strategies configured. The default strategy is
                seeded by the database migration.
              </div>
            )}
          </Panel>
        )}
      </div>
    </>
  );
}
