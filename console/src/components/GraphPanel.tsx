"use client";

import Link from "next/link";
import { useEffect } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { GraphBuild, GraphBuildSummary, Source } from "@/lib/types";
import { useMutation, useResource } from "@/lib/useResource";
import { DataTable } from "@/components/DataTable";
import { formatUsd } from "@/components/ExtractionPanel";
import {
  Button,
  EmptyState,
  ErrorState,
  InlineError,
  KeyValueGrid,
  LoadingState,
  Mono,
  Panel,
  StatusBadge,
  TypeTag,
} from "@/components/ui";

/**
 * The knowledge graph, as seen from one source: its latest build and the one
 * action that starts another.
 *
 * A build reads the source's latest completed extraction, so the panel follows
 * the source's status -- when an extraction started with "also build the
 * graph" finishes, the build it chained appears here and is polled until done.
 * The graph itself is drawn on the subject page, where every source of the
 * workspace contributes to it.
 */

const POLL_MS = 3000;

function formatTokens(prompt: number, completion: number): string {
  return `${prompt.toLocaleString()} in · ${completion.toLocaleString()} out`;
}

export function GraphPanel({ source }: { source: Source }) {
  const builds = useResource<GraphBuildSummary[]>(
    () => api.graph.builds(source.id),
    // A finished extraction may have chained a build: look again.
    [source.id, source.status],
  );
  const hasBuilds = (builds.data?.length ?? 0) > 0;
  const latestId = builds.data?.[0]?.id;
  const latest = useResource<GraphBuild | null>(
    () => (latestId ? api.graph.latest(source.id) : Promise.resolve(null)),
    [source.id, latestId, builds.data?.[0]?.status],
  );
  const start = useMutation(api.graph.build);

  const running = builds.data?.[0]?.status === "processing";

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(builds.reload, POLL_MS);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  const extracted = source.status === "completed";
  const partial = builds.data?.[0]?.status === "partial";

  const build = async (retryFailed = false) => {
    const created = await start.mutate(source.id, { retry_failed: retryFailed });
    if (created) builds.reload();
  };

  return (
    <Panel
      title="Knowledge graph"
      description="Entities and relationships an NVIDIA model found in this file's extracted text. The graph is drawn on the subject page, together with the other files of the workspace."
      actions={
        <>
          <Link
            href={`/subjects/${source.subject_id}#graph`}
            className="inline-flex items-center rounded-md border border-transparent px-2 h-[34px] text-sm text-ink-secondary hover:text-ink hover:underline"
          >
            View graph
          </Link>
          {partial && (
            <Button disabled={start.pending || running} onClick={() => build(true)}>
              Retry failed chunks
            </Button>
          )}
          <Button
            variant={hasBuilds ? "normal" : "primary"}
            disabled={!extracted || running || start.pending}
            onClick={() => build(false)}
          >
            {running ? "Building…" : hasBuilds ? "Rebuild graph" : "Build graph"}
          </Button>
        </>
      }
    >
      {start.error && (
        <div className="px-4 pt-3">
          <InlineError message={start.error} />
        </div>
      )}
      {builds.loading ? (
        <LoadingState label="Loading graph builds" />
      ) : builds.error ? (
        <ErrorState message={builds.error} onRetry={builds.reload} />
      ) : !hasBuilds ? (
        <EmptyState
          title="No graph yet"
          description={
            extracted
              ? "Build the graph to pull people, organizations, products, places and the relationships between them out of this file. One model call per passage of text."
              : "Extract the file first: the graph is built from the extracted text."
          }
          action={
            extracted ? (
              <Button variant="primary" disabled={start.pending} onClick={() => build(false)}>
                {start.pending ? "Starting…" : "Build graph"}
              </Button>
            ) : undefined
          }
        />
      ) : latest.error ? (
        <ErrorState message={latest.error} onRetry={latest.reload} />
      ) : latest.loading || !latest.data ? (
        <LoadingState label="Loading the latest build" />
      ) : (
        <BuildView build={latest.data} running={running} />
      )}
    </Panel>
  );
}

function BuildView({ build, running }: { build: GraphBuild; running: boolean }) {
  return (
    <div>
      <KeyValueGrid
        items={[
          {
            label: "Status",
            value: (
              <span className="flex items-center gap-2">
                <StatusBadge status={running ? "processing" : build.status} />
                {build.retry_of_id && <span className="text-xs text-ink-tertiary">retry</span>}
              </span>
            ),
          },
          { label: "Built from", value: `Extraction v${build.extraction_version}` },
          { label: "Model", value: <Mono>{build.model}</Mono> },
          { label: "Entities", value: <span className="tabular-nums">{build.entity_count}</span> },
          {
            label: "Relationships",
            value: <span className="tabular-nums">{build.relationship_count}</span>,
          },
          {
            label: "Chunks",
            value: (
              <span className="tabular-nums">
                {build.chunk_count}
                {build.failed_chunk_count > 0 && (
                  <span className="text-warn"> · {build.failed_chunk_count} failed</span>
                )}
              </span>
            ),
          },
          { label: "Tokens", value: formatTokens(build.prompt_tokens, build.completion_tokens) },
          { label: "Cost", value: formatUsd(build.cost_usd) },
          {
            label: "Finished",
            value: build.finished_at ? formatDate(build.finished_at) : "Running…",
          },
        ]}
      />

      {running && <LoadingState label="Reading the document for entities and relationships" />}

      {build.error && (
        <div className="px-4 pb-4">
          <div className="rounded-md border border-danger/30 bg-danger-soft p-3 text-sm text-ink">
            <span className="font-medium">Failed:</span> {build.error}
          </div>
        </div>
      )}

      {(build.stats.entities_created !== undefined || build.stats.entities_matched !== undefined) && (
        <Section title="What happened">
          <p className="text-sm text-ink-secondary">
            {build.stats.entities_created ?? 0} new entities,{" "}
            {build.stats.entities_matched ?? 0} matched to entities other files already
            mention
            {build.stats.relationships_skipped
              ? `, ${build.stats.relationships_skipped} relationships skipped`
              : ""}
            .
          </p>
        </Section>
      )}

      {build.failed_chunks.length > 0 && (
        <Section title={`Failed chunks (${build.failed_chunks.length})`} flush>
          <DataTable
            rows={build.failed_chunks}
            rowKey={(c) => c.chunk_id}
            empty={{ title: "No failed chunks" }}
            columns={[
              {
                header: "Chunk",
                width: "80px",
                cell: (c) => <span className="tabular-nums">#{c.index}</span>,
              },
              {
                header: "Pages",
                width: "90px",
                cell: (c) =>
                  c.page_start == null
                    ? "–"
                    : c.page_end && c.page_end !== c.page_start
                      ? `${c.page_start}–${c.page_end}`
                      : String(c.page_start),
              },
              { header: "Error", cell: (c) => <span className="text-warn">{c.error}</span> },
            ]}
          />
        </Section>
      )}

      {build.usage.length > 0 && (
        <Section title={`Model calls (${build.usage.length})`} flush>
          <DataTable
            rows={build.usage}
            rowKey={(u) => u.id}
            empty={{ title: "No model calls" }}
            columns={[
              { header: "Role", width: "90px", cell: (u) => <TypeTag value={u.role} /> },
              { header: "Model", cell: (u) => <Mono>{u.model}</Mono> },
              {
                header: "Page",
                width: "70px",
                align: "right",
                cell: (u) => <span className="tabular-nums">{u.page ?? "–"}</span>,
              },
              {
                header: "Tokens",
                align: "right",
                cell: (u) => (
                  <span className="tabular-nums text-ink-secondary">
                    {formatTokens(u.prompt_tokens, u.completion_tokens)}
                  </span>
                ),
              },
              {
                header: "Latency",
                width: "90px",
                align: "right",
                cell: (u) => <span className="tabular-nums">{(u.latency_ms / 1000).toFixed(1)}s</span>,
              },
              {
                header: "Cost",
                width: "110px",
                align: "right",
                cell: (u) => (
                  <span className="tabular-nums">
                    {u.price === null ? (
                      <span className="text-warn">unpriced</span>
                    ) : u.price.free ? (
                      "free"
                    ) : (
                      formatUsd(u.cost_usd)
                    )}
                  </span>
                ),
              },
              {
                header: "Status",
                width: "90px",
                cell: (u) => <StatusBadge status={u.status === "ok" ? "completed" : "failed"} />,
              },
            ]}
          />
        </Section>
      )}
    </div>
  );
}

function Section({
  title,
  children,
  flush,
}: {
  title: string;
  children: React.ReactNode;
  flush?: boolean;
}) {
  return (
    <div className="border-t border-line">
      <h3 className="px-4 pt-3 pb-2 text-xs font-medium uppercase tracking-wide text-ink-tertiary">
        {title}
      </h3>
      <div className={flush ? "" : "px-4 pb-4"}>{children}</div>
    </div>
  );
}
