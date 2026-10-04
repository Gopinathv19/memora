"use client";

/**
 * The source detail page's embedding section.
 *
 * Shows this source's embedding health under the active strategy (coverage,
 * per-status counts), with two actions: Generate embeddings (idempotent --
 * only chunks without a current embedding are queued) and Re-embed all
 * (force). While work is in flight the panel polls, so the coverage number
 * climbs without a manual refresh.
 */

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { Source } from "@/lib/types";
import { useMutation, useResource } from "@/lib/useResource";
import {
  Button,
  ErrorState,
  LoadingState,
  Mono,
  Panel,
} from "@/components/ui";

export function EmbeddingPanel({
  source,
  onStatusChange,
}: {
  source: Source;
  onStatusChange: () => void;
}) {
  const status = useResource(
    () => api.embeddings.sourceStatus(source.id),
    [source.id],
  );

  const [notice, setNotice] = useState<string | null>(null);

  const embed = useMutation((force: boolean) =>
    api.embeddings.embedSource(source.id, force),
  );

  // Poll while the worker drains this source's queue.
  const s = status.data;
  const inFlight = (s?.pending ?? 0) > 0 || (s?.processing ?? 0) > 0;
  useEffect(() => {
    if (!inFlight) return;
    const t = setInterval(() => status.reload(), 3000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inFlight]);

  async function run(force: boolean) {
    setNotice(null);
    const result = await embed.mutate(force);
    if (result) {
      setNotice(
        result.enqueued > 0
          ? `Queued ${result.enqueued} chunk(s) under ${result.strategy_name}; the worker is embedding them.`
          : "Every chunk already has a current embedding under the active strategy.",
      );
      status.reload();
      onStatusChange();
    }
  }

  return (
    <div className="mt-5">
      <Panel
        title="Embeddings"
        description="Vector coverage of this source's active chunks, under the active strategy."
        actions={
          <>
            <Button onClick={() => run(false)} disabled={embed.pending}>
              {embed.pending ? "Queueing…" : "Generate embeddings"}
            </Button>
            <Button
              onClick={() => run(true)}
              disabled={embed.pending || (s?.total_chunks ?? 0) === 0}
              title="Re-embed every chunk, even ones with a current embedding"
            >
              Re-embed all
            </Button>
          </>
        }
      >
        {status.loading ? (
          <LoadingState label="Loading embedding status" />
        ) : status.error ? (
          <ErrorState message={status.error} onRetry={status.reload} />
        ) : s ? (
          <div className="px-4 py-4">
            <div className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4 lg:grid-cols-7">
              <Field label="Total chunks" value={s.total_chunks} />
              <Field label="Embedded" value={s.embedded} tone="text-ok" />
              <Field label="Pending" value={s.pending} tone="text-warn" />
              <Field label="Processing" value={s.processing} tone="text-info" />
              <Field label="Failed" value={s.failed} tone="text-danger" />
              <Field label="Stale" value={s.stale} tone="text-ink-tertiary" />
              <Field
                label="Coverage"
                value={`${s.coverage_percent}%`}
                tone={s.coverage_percent >= 100 ? "text-ok" : "text-ink"}
              />
            </div>
            <p className="mt-4 text-xs text-ink-tertiary">
              Strategy: <Mono>{s.strategy_name}</Mono>
              {s.failed > 0 && (
                <>
                  {" · "}
                  <a href="/embeddings" className="underline hover:text-ink">
                    {s.failed} failed — retry from the Embeddings page
                  </a>
                </>
              )}
            </p>
            {notice && (
              <p role="status" className="mt-3 text-sm text-ink-secondary">
                {notice}
              </p>
            )}
            {embed.error && (
              <div className="mt-3">
                <ErrorState message={embed.error} />
              </div>
            )}
          </div>
        ) : null}
      </Panel>
    </div>
  );
}

function Field({
  label,
  value,
  tone = "text-ink",
}: {
  label: string;
  value: string | number;
  tone?: string;
}) {
  return (
    <div className="min-w-0">
      <div className="text-xs font-medium uppercase tracking-wide text-ink-tertiary">
        {label}
      </div>
      <div className={`mt-1 text-lg font-medium ${tone}`}>{value}</div>
    </div>
  );
}
