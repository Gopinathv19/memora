"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type {
  Extraction,
  ExtractionMode,
  ExtractionSummary,
  ExtractionUsageCall,
  PageProvenance,
  Source,
} from "@/lib/types";
import { useMutation, useResource } from "@/lib/useResource";
import { DataTable } from "@/components/DataTable";
import { Field, Modal } from "@/components/form";
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
 * The Extraction Agent, as seen from a source.
 *
 * Every run is a version (v1, v2, ...) that is never overwritten, so this
 * panel is a version picker over the source's extraction history plus the
 * one action that adds to it. While a run is processing the panel polls, and
 * tells the page when it finishes so the source's own status badge updates.
 */

const POLL_MS = 3000;
const MAX_INSTRUCTIONS = 2000;

const TEXTAREA_CLASS =
  "w-full rounded-md border border-line bg-surface px-3 py-[7px] text-sm text-ink placeholder:text-ink-tertiary transition-colors focus:border-brand focus:bg-panel focus:outline-none focus:ring-2 focus:ring-brand/25 disabled:bg-surface-strong";

export function formatUsd(value: number): string {
  if (!value) return "$0";
  return value < 0.01 ? `$${value.toFixed(6)}` : `$${value.toFixed(4)}`;
}

/** Hover text: which operator rate produced a call's cost. */
function priceTitle(price: ExtractionUsageCall["price"]): string {
  if (price === null) return "This model has no price in the operator's price list";
  if (price.free) return `Free (price list of ${price.effective_from})`;
  const parts = [
    price.input_per_1m && `$${price.input_per_1m}/1M in`,
    price.output_per_1m && `$${price.output_per_1m}/1M out`,
    price.per_image && `$${price.per_image}/image`,
    price.per_call && `$${price.per_call}/call`,
  ].filter(Boolean);
  return `${parts.join(" + ")} (price list of ${price.effective_from})`;
}

function formatTokens(prompt: number, completion: number): string {
  return `${prompt.toLocaleString()} in · ${completion.toLocaleString()} out`;
}

export function ExtractionPanel({
  source,
  onStatusChange,
}: {
  source: Source;
  onStatusChange: () => void;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);

  const versions = useResource<ExtractionSummary[]>(
    () => api.extractions.list(source.id),
    [source.id],
  );
  const shown = selected ?? versions.data?.[0]?.version ?? null;
  const detail = useResource<Extraction | null>(
    () => (shown ? api.extractions.get(source.id, shown) : Promise.resolve(null)),
    [source.id, shown],
  );

  const running = versions.data?.some((v) => v.status === "processing") ?? false;

  // Poll while anything is processing. When it stops, refresh the source too.
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      versions.reload();
      detail.reload();
    }, POLL_MS);
    return () => {
      clearInterval(timer);
      onStatusChange();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  const canExtract = !!source.storage_uri?.startsWith("file://");
  const hasVersions = (versions.data?.length ?? 0) > 0;

  return (
    <Panel
      title="Extraction"
      description="The Extraction Agent reads the stored file with NVIDIA models and returns structured information. Each run is kept as a version."
      actions={
        canExtract ? (
          <>
            {hasVersions && (
              <VersionPicker
                versions={versions.data ?? []}
                value={shown}
                onChange={setSelected}
              />
            )}
            <Button
              variant={hasVersions ? "normal" : "primary"}
              disabled={running}
              onClick={() => setStarting(true)}
            >
              {running ? "Extracting…" : hasVersions ? "Re-extract" : "Extract"}
            </Button>
          </>
        ) : undefined
      }
    >
      {!canExtract ? (
        <EmptyState
          title="Nothing to extract"
          description="Only files Memora stored itself can be extracted. This source was registered as metadata."
        />
      ) : versions.loading ? (
        <LoadingState label="Loading extractions" />
      ) : versions.error ? (
        <ErrorState message={versions.error} onRetry={versions.reload} />
      ) : !hasVersions ? (
        <EmptyState
          title="Not extracted yet"
          description="Run the agent to pull fields, tables and a summary out of this file. Plain-text pages are read locally for free; tables, images and scans go to NVIDIA models."
          action={
            <Button variant="primary" onClick={() => setStarting(true)}>
              Extract
            </Button>
          }
        />
      ) : detail.error ? (
        <ErrorState message={detail.error} onRetry={detail.reload} />
      ) : detail.loading || !detail.data ? (
        <LoadingState label="Loading result" />
      ) : (
        <ExtractionView extraction={detail.data} />
      )}

      {starting && (
        <StartExtractionModal
          sourceId={source.id}
          reextract={hasVersions}
          onClose={() => setStarting(false)}
          onStarted={(extraction) => {
            setStarting(false);
            setSelected(extraction.version);
            versions.reload();
            onStatusChange();
          }}
        />
      )}
    </Panel>
  );
}

function VersionPicker({
  versions,
  value,
  onChange,
}: {
  versions: ExtractionSummary[];
  value: number | null;
  onChange: (version: number) => void;
}) {
  return (
    <select
      aria-label="Extraction version"
      value={value ?? ""}
      onChange={(event) => onChange(Number(event.target.value))}
      className="h-[34px] rounded-md border border-line bg-panel px-2 text-sm text-ink"
    >
      {versions.map((v) => (
        <option key={v.version} value={v.version}>
          v{v.version} · {v.status} · {v.mode}
        </option>
      ))}
    </select>
  );
}

function StartExtractionModal({
  sourceId,
  reextract,
  onClose,
  onStarted,
}: {
  sourceId: string;
  reextract: boolean;
  onClose: () => void;
  onStarted: (extraction: Extraction) => void;
}) {
  const [mode, setMode] = useState<ExtractionMode>(reextract ? "deep" : "standard");
  const [instructions, setInstructions] = useState("");
  const [buildGraph, setBuildGraph] = useState(true);
  const start = useMutation(api.extractions.start);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const created = await start.mutate(sourceId, {
      mode,
      instructions: instructions.trim() || null,
      build_graph: buildGraph,
    });
    if (created) onStarted(created);
  };

  return (
    <Modal
      title={reextract ? "Re-extract" : "Extract"}
      description={
        reextract
          ? "Runs the agent again as a new version. Earlier versions are kept."
          : "Runs the Extraction Agent over this file."
      }
      onClose={onClose}
    >
      <form className="space-y-4" onSubmit={submit}>
        <Field label="Mode">
          <div className="space-y-2">
            <ModeOption
              checked={mode === "standard"}
              onSelect={() => setMode("standard")}
              title="Standard"
              detail="Each page is triaged: plain text is read locally, pictures go to the vision model, tables and scans to the layout model."
            />
            <ModeOption
              checked={mode === "deep"}
              onSelect={() => setMode("deep")}
              title="Deep"
              detail="Every PDF page goes to the layout model. More accurate on awkward documents; uses more credits."
            />
          </div>
        </Field>
        <Field
          label="Instructions"
          hint={`Optional. Tell the agent what it missed, e.g. "capture the premium table on page 3 and the policy number". ${instructions.length}/${MAX_INSTRUCTIONS}`}
        >
          <textarea
            value={instructions}
            maxLength={MAX_INSTRUCTIONS}
            rows={4}
            onChange={(event) => setInstructions(event.target.value)}
            placeholder="What should the agent look for?"
            className={TEXTAREA_CLASS}
            disabled={start.pending}
          />
        </Field>
        <label className="flex items-start gap-2 rounded-md border border-line px-3 py-2">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={buildGraph}
            onChange={(event) => setBuildGraph(event.target.checked)}
            disabled={start.pending}
          />
          <span>
            <span className="text-sm font-medium text-ink">Also build the knowledge graph</span>
            <span className="mt-0.5 block text-xs text-ink-secondary">
              When extraction succeeds, find the entities and relationships in the text. One
              model call per passage; replaces this file&apos;s previous graph.
            </span>
          </span>
        </label>
        {start.error && <InlineError message={start.error} />}
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose} disabled={start.pending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={start.pending}>
            {start.pending ? "Starting…" : "Start extraction"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function ModeOption({
  checked,
  onSelect,
  title,
  detail,
}: {
  checked: boolean;
  onSelect: () => void;
  title: string;
  detail: string;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={checked}
      className={`block w-full rounded-md border px-3 py-2 text-left transition-colors ${
        checked ? "border-brand bg-brand/5" : "border-line hover:bg-surface-strong"
      }`}
    >
      <span className="text-sm font-medium text-ink">{title}</span>
      <span className="mt-0.5 block text-xs text-ink-secondary">{detail}</span>
    </button>
  );
}

function ExtractionView({ extraction }: { extraction: Extraction }) {
  const result = extraction.result;
  return (
    <div>
      <KeyValueGrid
        items={[
          {
            label: "Version",
            value: (
              <span className="flex items-center gap-2">
                v{extraction.version} <StatusBadge status={extraction.status} />
              </span>
            ),
          },
          { label: "Mode", value: <TypeTag value={extraction.mode} /> },
          { label: "Provider", value: <TypeTag value={extraction.provider} /> },
          { label: "Document type", value: result?.document_type ?? "–" },
          { label: "Language", value: result?.language ?? "–" },
          {
            label: "Tokens",
            value: formatTokens(extraction.prompt_tokens, extraction.completion_tokens),
          },
          { label: "Cost", value: formatUsd(extraction.cost_usd) },
          {
            label: "Triggered by",
            value: extraction.triggered_by_kind === "user" ? "Console user" : "API credential",
          },
          {
            label: "Finished",
            value: extraction.finished_at ? formatDate(extraction.finished_at) : "Running…",
          },
        ]}
      />

      {extraction.instructions && (
        <Section title="Instructions for this version">
          <p className="text-sm text-ink-secondary whitespace-pre-wrap">{extraction.instructions}</p>
        </Section>
      )}

      {extraction.status === "processing" && (
        <LoadingState label="The agent is reading the document" />
      )}

      {extraction.error && (
        <div className="px-4 pb-4">
          <div className="rounded-md border border-danger/30 bg-danger-soft p-3 text-sm text-ink">
            <span className="font-medium">Failed:</span> {extraction.error}
          </div>
        </div>
      )}

      {result && (
        <>
          {(result.title || result.summary) && (
            <Section title={result.title ?? "Summary"}>
              <p className="text-sm text-ink">{result.summary}</p>
            </Section>
          )}

          {result.warnings.length > 0 && (
            <Section title="Warnings">
              <ul className="list-disc pl-5 text-sm text-warn">
                {result.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </Section>
          )}

          <Section title={`Fields (${result.fields.length})`} flush>
            <DataTable
              rows={result.fields.map((f, i) => ({ ...f, i }))}
              rowKey={(row) => String(row.i)}
              empty={{ title: "No fields extracted" }}
              columns={[
                { header: "Key", cell: (f) => <Mono>{f.key}</Mono> },
                { header: "Value", cell: (f) => <span className="text-ink">{f.value}</span> },
                {
                  header: "Page",
                  width: "70px",
                  align: "right",
                  cell: (f) => <span className="tabular-nums">{f.page ?? "–"}</span>,
                },
                {
                  header: "Confidence",
                  width: "110px",
                  align: "right",
                  cell: (f) =>
                    f.confidence == null ? "–" : (
                      <span className="tabular-nums">{Math.round(f.confidence * 100)}%</span>
                    ),
                },
              ]}
            />
          </Section>

          {result.tables.map((table, i) => (
            <Section
              key={i}
              title={`${table.title ?? `Table ${i + 1}`}${table.page ? ` · page ${table.page}` : ""}`}
              flush
            >
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-sm">
                  {table.columns.length > 0 && (
                    <thead>
                      <tr className="border-b border-line bg-surface">
                        {table.columns.map((c, j) => (
                          <th key={j} className="px-4 py-2 text-left text-xs font-medium text-ink-tertiary">
                            {c}
                          </th>
                        ))}
                      </tr>
                    </thead>
                  )}
                  <tbody>
                    {table.rows.map((row, r) => (
                      <tr key={r} className="border-b border-line last:border-0">
                        {row.map((cell, c) => (
                          <td key={c} className="px-4 py-2 text-ink">
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>
          ))}

          <Section title="How each page was read" flush>
            <DataTable
              rows={result.pages}
              rowKey={(p: PageProvenance) => `${p.kind}-${p.page}`}
              empty={{ title: "No pages" }}
              columns={[
                {
                  header: "Unit",
                  width: "110px",
                  cell: (p) => (
                    <span className="capitalize">
                      {p.kind} {p.page}
                    </span>
                  ),
                },
                { header: "Difficulty", width: "110px", cell: (p) => <TypeTag value={p.difficulty} /> },
                { header: "Route", width: "100px", cell: (p) => <TypeTag value={p.route} /> },
                {
                  header: "Model",
                  cell: (p) =>
                    p.model ? <Mono>{p.model}</Mono> : <span className="text-ink-tertiary">local, no model</span>,
                },
                {
                  header: "Result",
                  cell: (p) => (
                    <span className={p.status === "ok" ? "text-ink-secondary" : "text-warn"}>
                      {p.status}
                      {p.note ? ` — ${p.note}` : ""}
                    </span>
                  ),
                },
              ]}
            />
          </Section>
        </>
      )}

      {extraction.usage.length > 0 && (
        <Section title={`Model calls (${extraction.usage.length})`} flush>
          <DataTable
            rows={extraction.usage}
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
                width: "120px",
                align: "right",
                cell: (u) => (
                  <span className="tabular-nums" title={priceTitle(u.price)}>
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
