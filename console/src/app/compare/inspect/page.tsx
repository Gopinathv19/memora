"use client";

/**
 * Source inspector: how did the graph build turn this file into entities and facts?
 *
 * One source's own graph, beside the passages the model read it from. Select a
 * passage to see the entities and facts it produced (highlighted in its text and
 * outlined in the graph); select an entity to see what was said about it and which
 * passages mention it. A set of checks flags what usually means a poor extraction:
 * passages that produced nothing, entities with no relationships, low-confidence
 * facts and passages the model failed to read.
 */

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

import { api } from "@/lib/api";
import { graphrag } from "@/lib/graphrag-api";
import type { InspectChunk, InspectEntity, SourceInspection } from "@/lib/graphrag-types";
import type { GraphBuild, Source } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import { useTenant } from "@/lib/tenant-context";
import { GraphCanvas, TYPE_COLORS, type CanvasEdge, type CanvasNode } from "@/components/compare/GraphCanvas";
import { fmtCost, fmtMs } from "@/components/compare/shared";
import { EmptyState, ErrorState, PageHeader, Panel, StatusBadge } from "@/components/ui";

const LOW_CONFIDENCE = 0.5;
const colorOf = (type: string) => TYPE_COLORS[type] ?? TYPE_COLORS.CONCEPT;
const nice = (relation: string) => relation.replace(/_/g, " ").toLowerCase();

export default function InspectPage() {
  return (
    <Suspense fallback={<p className="text-sm text-ink-secondary">Loading…</p>}>
      <Inspector />
    </Suspense>
  );
}

function Inspector() {
  const params = useSearchParams();
  const { tenant } = useTenant();
  const sources = useResource<Source[]>(() => api.sources.list({ tenantId: tenant?.id }), [tenant?.id]);
  const [sourceId, setSourceId] = useState(params.get("source") ?? "");

  const list = useMemo(
    () => [...(sources.data ?? [])].sort((a, b) => (a.filename ?? "").localeCompare(b.filename ?? "")),
    [sources.data],
  );
  useEffect(() => {
    if (!sourceId && list.length) setSourceId(list[0].id);
  }, [list, sourceId]);

  return (
    <>
      <PageHeader
        eyebrow="Graph RAG"
        title="Source inspector"
        description="Check how a file's knowledge graph was built: the graph of that one file next to the passages it was read from, and what each passage produced."
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-ink-secondary">
          File
          <select
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            className="h-[34px] max-w-[420px] rounded-md border border-line bg-panel px-2 text-sm text-ink"
          >
            {list.map((s) => (
              <option key={s.id} value={s.id}>
                {s.filename ?? s.id} · {s.subject_external_id ?? "?"}
              </option>
            ))}
          </select>
        </label>
        {sources.error && <span className="text-sm text-danger">{sources.error}</span>}
      </div>
      {sourceId ? <Inspection key={sourceId} sourceId={sourceId} /> : <EmptyState title="No files" description="Upload a file to a subject first." />}
    </>
  );
}

function Inspection({ sourceId }: { sourceId: string }) {
  const data = useResource<SourceInspection>(() => graphrag.inspect(sourceId), [sourceId]);
  const build = useResource<GraphBuild | null>(() => api.graph.latest(sourceId).catch(() => null), [sourceId]);
  const [chunkId, setChunkId] = useState<string | null>(null);
  const [entityId, setEntityId] = useState<string | null>(null);
  const [onlyPassage, setOnlyPassage] = useState(false);

  const d = data.data;
  const entityById = useMemo(() => new Map((d?.entities ?? []).map((e) => [e.id, e])), [d]);
  const chunk = d?.chunks.find((c) => c.id === chunkId) ?? null;
  const entity = entityId ? entityById.get(entityId) ?? null : null;

  const passageEntities = useMemo(() => new Set(chunk?.entity_ids ?? []), [chunk]);
  const passageFacts = (c: InspectChunk) => (d?.relationships ?? []).filter((r) => r.chunk_ids.includes(c.id));

  const nodes: CanvasNode[] = useMemo(
    () =>
      (d?.entities ?? [])
        .filter((e) => !(onlyPassage && chunk) || passageEntities.has(e.id))
        .map((e) => ({ id: e.id, name: e.name, type: e.type, degree: e.chunk_ids.length })),
    [d, onlyPassage, chunk, passageEntities],
  );
  const edges: CanvasEdge[] = useMemo(
    () =>
      (d?.relationships ?? [])
        .filter((r) => !(onlyPassage && chunk) || (chunk && r.chunk_ids.includes(chunk.id)))
        .map((r) => ({ source: r.source, target: r.target, relation: r.relation, confidence: r.confidence })),
    [d, onlyPassage, chunk],
  );

  const checks = useMemo(() => {
    if (!d) return [];
    const withFacts = new Set(d.relationships.flatMap((r) => [r.source, r.target]));
    return [
      { label: "Passages that produced no entities", items: d.chunks.filter((c) => c.entity_ids.length === 0).map((c) => `Passage ${c.index + 1}`), hint: "The model found nothing in them: boilerplate, or text it could not read." },
      { label: "Entities with no relationships", items: d.entities.filter((e) => !withFacts.has(e.id)).map((e) => e.name), hint: "Found but not connected to anything. Fine for a name on a form; suspicious in prose." },
      { label: `Facts with confidence below ${LOW_CONFIDENCE}`, items: d.relationships.filter((r) => (r.confidence ?? 1) < LOW_CONFIDENCE).map((r) => `${r.source_name} ${nice(r.relation)} ${r.target_name}`), hint: "The model was unsure. Check them against the passage." },
    ];
  }, [d]);

  if (data.loading) return <p className="text-sm text-ink-secondary">Loading the graph…</p>;
  if (data.error)
    return <ErrorState message={data.error} onRetry={data.reload} />;
  if (!d || (d.chunks.length === 0 && d.entities.length === 0))
    return (
      <EmptyState
        title="No graph for this file yet"
        description="Build its knowledge graph from the file's page (Source > Knowledge graph), then come back."
      />
    );

  const b = build.data;
  const types = Array.from(new Set(d.entities.map((e) => e.type))).sort();

  return (
    <div className="space-y-4">
      {b && <BuildStrip build={b} entities={d.entities.length} facts={d.relationships.length} passages={d.chunks.length} />}

      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <Panel
          title={`Graph from ${d.filename ?? "this file"}`}
          description="Only what this file contributed. Select a passage below, or an entity here, to follow it back to the text."
          actions={
            chunk ? (
              <label className="flex items-center gap-1.5 text-xs text-ink-secondary">
                <input type="checkbox" checked={onlyPassage} onChange={(e) => setOnlyPassage(e.target.checked)} />
                Only passage {chunk.index + 1}
              </label>
            ) : undefined
          }
        >
          <div className="p-4">
            <GraphCanvas
              nodes={nodes}
              edges={edges}
              lens={chunk ? passageEntities : undefined}
              onSelect={setEntityId}
              height={460}
            />
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-secondary">
              {types.map((t) => (
                <span key={t} className="flex items-center gap-1">
                  <span className="size-2 rounded-full" style={{ background: colorOf(t) }} />
                  {t.toLowerCase()} ({d.entities.filter((e) => e.type === t).length})
                </span>
              ))}
              {chunk && <span className="flex items-center gap-1"><span className="size-2.5 rounded-full border-2 border-info" />produced by passage {chunk.index + 1}</span>}
            </div>
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel title="Checks" description="Signs of a weak extraction.">
            <ul className="space-y-3 p-4 text-sm">
              {checks.map((c) => (
                <li key={c.label}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-ink">{c.label}</span>
                    <span className={`rounded px-1.5 py-0.5 text-xs ${c.items.length ? "bg-warn-soft text-warn" : "bg-ok-soft text-ok"}`}>{c.items.length}</span>
                  </div>
                  {c.items.length > 0 && (
                    <p className="mt-1 text-xs text-ink-secondary">
                      {c.items.slice(0, 6).join(" · ")}
                      {c.items.length > 6 ? ` · +${c.items.length - 6} more` : ""}
                      <span className="block text-ink-tertiary">{c.hint}</span>
                    </p>
                  )}
                </li>
              ))}
              {b && b.failed_chunks.length > 0 && (
                <li>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-ink">Passages the model failed to read</span>
                    <span className="rounded bg-danger-soft px-1.5 py-0.5 text-xs text-danger">{b.failed_chunks.length}</span>
                  </div>
                  <ul className="mt-1 space-y-0.5 text-xs text-ink-secondary">
                    {b.failed_chunks.map((f) => (
                      <li key={f.chunk_id}>Passage {f.index + 1}: {f.error}</li>
                    ))}
                  </ul>
                </li>
              )}
            </ul>
          </Panel>

          <Panel title="Entity">
            <div className="p-4 text-sm">
              {entity ? (
                <EntityDetail entity={entity} d={d} onPassage={(id) => setChunkId(id)} onEntity={setEntityId} />
              ) : (
                <p className="text-xs text-ink-tertiary">Select an entity in the graph to see what was said about it.</p>
              )}
            </div>
          </Panel>
        </div>
      </div>

      <Panel
        title={`Passages (${d.chunks.length})`}
        description="The text units the model read, in order. Each shows what it produced."
      >
        <ul>
          {d.chunks.map((c) => {
            const open = c.id === chunkId;
            const facts = passageFacts(c);
            return (
              <li key={c.id} className="border-b border-line-soft last:border-b-0">
                <button
                  type="button"
                  onClick={() => setChunkId(open ? null : c.id)}
                  aria-expanded={open}
                  className={`flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm hover:bg-surface ${open ? "bg-accent-soft" : ""}`}
                >
                  <span className="w-20 shrink-0 font-medium text-ink">Passage {c.index + 1}</span>
                  <span className="flex-1 truncate text-ink-secondary">{c.text.replace(/\s+/g, " ")}</span>
                  {c.page_start != null && <span className="text-xs text-ink-tertiary">p.{c.page_start}{c.page_end && c.page_end !== c.page_start ? `–${c.page_end}` : ""}</span>}
                  <span className={`rounded px-1.5 py-0.5 text-xs ${c.entity_ids.length ? "bg-surface-strong text-ink-secondary" : "bg-warn-soft text-warn"}`}>
                    {c.entity_ids.length} entities · {facts.length} facts
                  </span>
                </button>
                {open && <PassageDetail chunk={c} entities={c.entity_ids.map((i) => entityById.get(i)).filter((e): e is InspectEntity => !!e)} facts={facts} onEntity={setEntityId} />}
              </li>
            );
          })}
        </ul>
      </Panel>
    </div>
  );
}

/* ------------------------------------------------------------------ pieces */

function BuildStrip({ build, entities, facts, passages }: { build: GraphBuild; entities: number; facts: number; passages: number }) {
  const elapsed = build.stats?.elapsed_ms;
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-line bg-panel px-4 py-3 text-sm">
      <div>
        <div className="text-xs uppercase tracking-wider text-ink-tertiary">Latest build</div>
        <StatusBadge status={build.status} />
      </div>
      <Stat label="Model" value={build.model.split("/").pop() ?? build.model} />
      <Stat label="Passages read" value={`${build.chunk_count - build.failed_chunk_count} of ${build.chunk_count}`} />
      <Stat label="Entities" value={`${entities}`} />
      <Stat label="Facts" value={`${facts}`} />
      <Stat label="Passages shown" value={`${passages}`} />
      <Stat label="Cost" value={fmtCost(build.cost_usd)} />
      {elapsed != null && <Stat label="Took" value={fmtMs(elapsed)} />}
      {build.error && <span className="text-danger">{build.error}</span>}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-ink-tertiary">{label}</div>
      <div className="font-medium tabular-nums text-ink">{value}</div>
    </div>
  );
}

function EntityDetail({
  entity, d, onPassage, onEntity,
}: {
  entity: InspectEntity;
  d: SourceInspection;
  onPassage: (chunkId: string) => void;
  onEntity: (id: string) => void;
}) {
  const rels = d.relationships.filter((r) => r.source === entity.id || r.target === entity.id);
  const index = new Map(d.chunks.map((c) => [c.id, c.index]));
  return (
    <div className="space-y-3">
      <div>
        <div className="flex items-center gap-2 font-medium text-ink">
          <span className="size-2.5 rounded-full" style={{ background: colorOf(entity.type) }} />
          {entity.name}
        </div>
        <div className="text-xs text-ink-tertiary">{entity.type.toLowerCase()}{entity.aliases.length ? ` · also: ${entity.aliases.join(", ")}` : ""}</div>
      </div>
      {entity.descriptions.length > 0 && (
        <ul className="space-y-1 text-xs text-ink-secondary">
          {entity.descriptions.map((t, i) => <li key={i}>“{t}”</li>)}
        </ul>
      )}
      <div className="flex flex-wrap gap-1.5 text-xs">
        <span className="text-ink-tertiary">Mentioned in</span>
        {entity.chunk_ids.map((id) => (
          <button key={id} type="button" onClick={() => onPassage(id)} className="rounded border border-line px-1.5 text-ink-secondary hover:bg-surface-strong">
            passage {(index.get(id) ?? 0) + 1}
          </button>
        ))}
      </div>
      <ul className="space-y-1 text-xs text-ink-secondary">
        {rels.map((r, i) => {
          const other = r.source === entity.id ? r.target : r.source;
          return (
            <li key={i}>
              {r.source_name} <span className="text-accent">{nice(r.relation)}</span>{" "}
              <button type="button" className="underline hover:text-ink" onClick={() => onEntity(r.target === entity.id ? r.source : r.target)} title={other}>
                {r.target_name}
              </button>
              {r.confidence != null && <span className="text-ink-tertiary"> ({Math.round(r.confidence * 100)}%)</span>}
            </li>
          );
        })}
        {rels.length === 0 && <li className="text-ink-tertiary">No relationships.</li>}
      </ul>
    </div>
  );
}

/** The passage text with the entities the model found in it marked. */
function PassageDetail({
  chunk, entities, facts, onEntity,
}: {
  chunk: InspectChunk;
  entities: InspectEntity[];
  facts: SourceInspection["relationships"];
  onEntity: (id: string) => void;
}) {
  const parts = useMemo(() => highlight(chunk.text, entities), [chunk.text, entities]);
  return (
    <div className="grid gap-4 bg-surface px-4 py-3 lg:grid-cols-[1fr_300px]">
      <p className="whitespace-pre-wrap rounded-md border border-line bg-panel p-3 text-sm leading-relaxed text-ink">
        {parts.map((p, i) =>
          p.entity ? (
            <mark
              key={i}
              onClick={() => onEntity(p.entity!.id)}
              title={`${p.entity.name} (${p.entity.type.toLowerCase()})`}
              className="cursor-pointer rounded px-0.5"
              style={{ background: `${colorOf(p.entity.type)}26`, borderBottom: `2px solid ${colorOf(p.entity.type)}`, color: "inherit" }}
            >
              {p.text}
            </mark>
          ) : (
            <span key={i}>{p.text}</span>
          ),
        )}
      </p>
      <div className="space-y-3 text-xs">
        <div>
          <h3 className="mb-1 font-medium uppercase tracking-wider text-ink-tertiary">Entities found ({entities.length})</h3>
          <ul className="space-y-0.5">
            {entities.map((e) => (
              <li key={e.id}>
                <button type="button" onClick={() => onEntity(e.id)} className="flex items-center gap-1.5 hover:underline">
                  <span className="size-2 rounded-full" style={{ background: colorOf(e.type) }} />
                  <span className="text-ink">{e.name}</span>
                  <span className="text-ink-tertiary">{e.type.toLowerCase()}</span>
                </button>
              </li>
            ))}
            {entities.length === 0 && <li className="text-ink-tertiary">Nothing was extracted from this passage.</li>}
          </ul>
        </div>
        <div>
          <h3 className="mb-1 font-medium uppercase tracking-wider text-ink-tertiary">Facts it supports ({facts.length})</h3>
          <ul className="space-y-1 text-ink-secondary">
            {facts.map((f, i) => (
              <li key={i}>
                {f.source_name} <span className="text-accent">{nice(f.relation)}</span> {f.target_name}
                {f.confidence != null && <span className="text-ink-tertiary"> ({Math.round(f.confidence * 100)}%)</span>}
                {f.description && <span className="block text-ink-tertiary">{f.description}</span>}
              </li>
            ))}
            {facts.length === 0 && <li className="text-ink-tertiary">No relationships.</li>}
          </ul>
        </div>
      </div>
    </div>
  );
}

/** Split `text` into plain and entity-marked runs: the longest names and aliases win. */
function highlight(
  text: string,
  entities: InspectEntity[],
): { text: string; entity?: InspectEntity }[] {
  const terms: { term: string; entity: InspectEntity }[] = [];
  for (const e of entities) for (const t of [e.name, ...e.aliases]) if (t && t.length >= 2) terms.push({ term: t, entity: e });
  if (!terms.length) return [{ text }];
  terms.sort((a, b) => b.term.length - a.term.length);
  const lookup = new Map(terms.map((t) => [t.term.toLowerCase(), t.entity]));
  const pattern = new RegExp(terms.map((t) => t.term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "gi");
  const out: { text: string; entity?: InspectEntity }[] = [];
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ text: text.slice(last, at) });
    out.push({ text: m[0], entity: lookup.get(m[0].toLowerCase()) });
    last = at + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}
