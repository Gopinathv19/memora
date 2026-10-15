"use client";

/**
 * Arena: one question, run through vector RAG and Graph RAG side by side.
 *
 * Each pipeline's answer appears the moment it finishes (server-sent events),
 * with where its time went, what it cost and the evidence it cited, and the
 * graph panel shows what Graph RAG found: activation by size, the hops it
 * travelled (playable), paths, and -- with the lens on -- which of those
 * entities the vector pipeline's chunks also touched.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

import { graphrag } from "@/lib/graphrag-api";
import type {
  ExpandResult,
  GraphTrace,
  OverlapPair,
  Pipeline,
  PipelineResult,
  Technique,
} from "@/lib/graphrag-types";
import { GraphCanvas, TYPE_COLORS, type CanvasEdge, type CanvasNode } from "@/components/compare/GraphCanvas";
import {
  IndexBanner,
  PIPELINE_COLOR,
  PipelineCard,
  SubjectSelect,
  TechniquePicker,
  fmtCost,
  fmtMs,
  useCompareSubject,
} from "@/components/compare/shared";
import { Button, ErrorState, PageHeader, Panel } from "@/components/ui";

type Results = Partial<Record<Pipeline, PipelineResult>>;

const isGraph = (r?: PipelineResult): r is PipelineResult & { trace: GraphTrace } =>
  !!r && r.pipeline !== "vector" && r.status === "ok" && "nodes" in r.trace;

export default function ArenaPage() {
  const { subjects, subjectId, setSubjectId, loading } = useCompareSubject();
  const [query, setQuery] = useState("");
  const [technique, setTechnique] = useState<Technique>("spreading");
  const [withHybrid, setWithHybrid] = useState(false);
  const [hubDamping, setHubDamping] = useState(true);
  const [maxHops, setMaxHops] = useState(2);
  const [running, setRunning] = useState(false);
  const [expected, setExpected] = useState<Pipeline[]>([]);
  const [results, setResults] = useState<Results>({});
  const [overlap, setOverlap] = useState<OverlapPair | null>(null);
  const [lens, setLens] = useState<string[]>([]);
  const [error, setError] = useState<string>();
  const abort = useRef<AbortController | null>(null);

  const submit = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();
      const q = query.trim();
      if (!q || !subjectId || running) return;
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      const pipelines: Pipeline[] = ["vector", "graph", ...(withHybrid ? (["hybrid"] as const) : [])];
      setRunning(true);
      setError(undefined);
      setResults({});
      setOverlap(null);
      setLens([]);
      setExpected(pipelines);
      try {
        await graphrag.compareStream(
          subjectId,
          q,
          { technique, hub_damping: hubDamping, max_hops: maxHops },
          pipelines,
          {
            onResult: (pipeline, result) => setResults((r) => ({ ...r, [pipeline]: result })),
            onDone: (done) => {
              setOverlap(done.overlap.pairs.find((p) => p.a === "vector" && p.b === "graph") ?? null);
              setLens(done.lens?.entity_ids ?? []);
            },
          },
          controller.signal,
        );
      } catch (err) {
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          setError(err instanceof Error ? err.message : "The comparison failed");
        }
      } finally {
        setRunning(false);
      }
    },
    [query, subjectId, running, technique, withHybrid, hubDamping, maxHops],
  );

  useEffect(() => () => abort.current?.abort(), []);

  const vector = results.vector;
  const graph = results.graph;
  const hybrid = results.hybrid;
  const idsOf = (r?: PipelineResult) => new Set(r?.chunks.map((c) => c.chunk_id) ?? []);
  const graphTrace = isGraph(graph) ? graph.trace : isGraph(hybrid) ? hybrid.trace : null;

  return (
    <>
      <PageHeader
        eyebrow="Graph RAG"
        title="Arena"
        description="Ask one question and watch vector RAG and Graph RAG answer it side by side: what each found, how long it took, what it cost, and where their evidence differs."
      />
      <IndexBanner subjectId={subjectId} />

      <Panel>
        <form onSubmit={submit} className="space-y-4 p-4">
          <div className="flex flex-wrap items-center gap-4">
            <SubjectSelect subjects={subjects} value={subjectId} onChange={setSubjectId} />
            <label className="flex items-center gap-2 text-sm text-ink-secondary">
              <input type="checkbox" checked={withHybrid} onChange={(e) => setWithHybrid(e.target.checked)} />
              Also run hybrid (vector + graph fused)
            </label>
            <details className="text-sm text-ink-secondary">
              <summary className="cursor-pointer select-none">Tune</summary>
              <div className="mt-2 flex flex-wrap items-center gap-4 rounded-md border border-line bg-surface px-3 py-2">
                <label className="flex items-center gap-2">
                  Max hops
                  <input type="number" min={1} max={4} value={maxHops} onChange={(e) => setMaxHops(Number(e.target.value))}
                    className="h-8 w-14 rounded border border-line bg-panel px-2 text-ink" />
                </label>
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={hubDamping} onChange={(e) => setHubDamping(e.target.checked)} />
                  Damp hub entities
                </label>
              </div>
            </details>
          </div>
          <TechniquePicker value={technique} onChange={setTechnique} />
          <div className="flex gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ask a question about this subject's documents…"
              className="h-[38px] min-w-0 flex-1 rounded-md border border-line bg-panel px-3 text-sm text-ink placeholder:text-ink-tertiary focus:border-brand focus:outline-none"
              aria-label="Question"
            />
            <Button type="submit" variant="primary" disabled={running || !query.trim() || !subjectId}>
              {running ? "Comparing…" : "Compare"}
            </Button>
          </div>
        </form>
      </Panel>

      {error && <div className="mt-4"><ErrorState message={error} /></div>}
      {loading && !subjects.length && <p className="mt-4 text-sm text-ink-secondary">Loading subjects…</p>}

      <AnimatePresence>
        {(running || expected.length > 0) && (Object.keys(results).length > 0 || running) && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-5 space-y-4">
            <Verdict vector={vector} graph={graph} running={running} />
            <div className={`grid gap-4 ${withHybrid ? "xl:grid-cols-3" : "lg:grid-cols-2"} lg:grid-cols-2`}>
              <PipelineCard
                title="Vector RAG"
                subtitle="Dense + lexical search, RRF, MMR, reranker"
                color={PIPELINE_COLOR.vector}
                result={vector}
                pending={running && !vector}
                citedBy={idsOf(graph)}
              />
              <PipelineCard
                title={`Graph RAG · ${technique.replace("_", "-")}`}
                subtitle="Evidence found in the knowledge graph, then the same reranker and answer model"
                color={PIPELINE_COLOR.graph}
                result={graph}
                pending={running && !graph}
                citedBy={idsOf(vector)}
              >
                {isGraph(graph) && <GraphFacts trace={graph.trace} />}
              </PipelineCard>
              {withHybrid && (
                <PipelineCard
                  title="Hybrid"
                  subtitle="Vector and graph candidates fused by RRF"
                  color={PIPELINE_COLOR.hybrid}
                  result={hybrid}
                  pending={running && !hybrid}
                  citedBy={idsOf(vector)}
                />
              )}
            </div>
            {overlap && <OverlapBar overlap={overlap} />}
            {graphTrace && (
              <GraphPanel
                key={graph?.query + String(graph?.total_ms)}
                subjectId={subjectId}
                trace={graphTrace}
                lens={lens}
              />
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

/* ----------------------------------------------------------------- verdict */

function Verdict({ vector, graph, running }: { vector?: PipelineResult; graph?: PipelineResult; running: boolean }) {
  if (!vector || !graph || vector.status !== "ok" || graph.status !== "ok") {
    return running ? (
      <p className="text-sm text-ink-secondary" aria-live="polite">Running both pipelines…</p>
    ) : null;
  }
  const slower = graph.total_ms / Math.max(1, vector.total_ms);
  const dearer = graph.cost_usd / Math.max(1e-9, vector.cost_usd);
  return (
    <motion.p
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-md border border-line bg-surface px-4 py-2 text-sm text-ink-secondary"
      aria-live="polite"
    >
      Graph RAG took <b className="text-ink">{fmtMs(graph.total_ms)}</b> against{" "}
      <b className="text-ink">{fmtMs(vector.total_ms)}</b> for vector RAG (
      {slower >= 1 ? `${slower.toFixed(1)}x slower` : `${(1 / slower).toFixed(1)}x faster`}) and cost{" "}
      <b className="text-ink">{fmtCost(graph.cost_usd)}</b> against{" "}
      <b className="text-ink">{fmtCost(vector.cost_usd)}</b>
      {isFinite(dearer) && vector.cost_usd > 0 ? ` (${dearer.toFixed(1)}x)` : ""}. Whether the extra work paid off
      is in the answers and the evidence below; run a benchmark to measure it over many questions.
    </motion.p>
  );
}

/* ------------------------------------------------------------ graph details */

function GraphFacts({ trace }: { trace: GraphTrace }) {
  return (
    <div className="space-y-3">
      {trace.paths.length > 0 && (
        <div>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-ink-tertiary">Reasoning paths</h3>
          <ul className="space-y-1">
            {trace.paths.slice(0, 3).map((p, i) => (
              <li key={i} className="flex flex-wrap items-center gap-1 rounded-md bg-surface px-2 py-1.5 text-xs">
                <span className="font-medium text-ink">{p.from}</span>
                {p.steps.map((s, j) => (
                  <span key={j} className="flex items-center gap-1">
                    <span className="rounded bg-accent-soft px-1 text-accent">{s.relation.replace(/_/g, " ").toLowerCase()}</span>
                    <span className="text-ink-tertiary">→</span>
                    <span className="text-ink">{s.to}</span>
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </div>
      )}
      {trace.communities.length > 0 && (
        <div>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-ink-tertiary">Communities used</h3>
          <ul className="space-y-1">
            {trace.communities.map((c) => (
              <li key={c.id} className="rounded-md bg-surface px-2 py-1.5 text-xs text-ink-secondary">
                <span className="font-medium text-ink">{c.title}</span> ({c.size} entities): {c.summary}
              </li>
            ))}
          </ul>
        </div>
      )}
      {trace.facts.length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wider text-ink-tertiary">
            Facts given to the model ({trace.facts.length})
          </summary>
          <ul className="mt-1.5 space-y-0.5 text-xs text-ink-secondary">
            {trace.facts.map((f, i) => (
              <li key={i}>• {f.text}</li>
            ))}
          </ul>
        </details>
      )}
      {trace.notes.length > 0 && <p className="text-[11px] text-ink-tertiary">{trace.notes.join(" · ")}</p>}
    </div>
  );
}

function OverlapBar({ overlap }: { overlap: OverlapPair }) {
  const a = overlap.only_a.length;
  const both = overlap.both.length;
  const b = overlap.only_b.length;
  const total = Math.max(1, a + both + b);
  const parts = [
    { label: "Only vector", n: a, color: PIPELINE_COLOR.vector },
    { label: "Both", n: both, color: "#8f8f8f" },
    { label: "Only graph", n: b, color: PIPELINE_COLOR.graph },
  ];
  return (
    <Panel title="Evidence overlap" description="Which final chunks each pipeline cited. Chunks only the graph found are where it added something the vector search missed.">
      <div className="p-4">
        <div className="flex h-3 overflow-hidden rounded bg-surface-strong">
          {parts.map((p) => (
            <motion.div
              key={p.label}
              initial={{ width: 0 }}
              animate={{ width: `${(p.n / total) * 100}%` }}
              transition={{ duration: 0.6 }}
              style={{ background: p.color }}
              title={`${p.label}: ${p.n}`}
            />
          ))}
        </div>
        <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-secondary">
          {parts.map((p) => (
            <span key={p.label} className="flex items-center gap-1.5">
              <span className="size-2 rounded-full" style={{ background: p.color }} />
              {p.label}: <b className="text-ink">{p.n}</b>
            </span>
          ))}
          <span>Jaccard {overlap.jaccard.toFixed(2)}</span>
        </div>
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------- graph panel */

function GraphPanel({ subjectId, trace, lens }: { subjectId: string; trace: GraphTrace; lens: string[] }) {
  const [extra, setExtra] = useState<ExpandResult>({ nodes: [], edges: [] });
  const [selected, setSelected] = useState<string | null>(null);
  const [showLens, setShowLens] = useState(true);
  const [hop, setHop] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [expandError, setExpandError] = useState<string>();

  const maxHop = useMemo(() => Math.max(1, ...trace.nodes.map((n) => n.hop ?? 0)), [trace]);

  const nodes: CanvasNode[] = useMemo(() => {
    const base = new Map<string, CanvasNode>(
      trace.nodes.map((n) => [n.id, { id: n.id, name: n.name, type: n.type, score: n.score, hop: n.hop, seed: n.seed }]),
    );
    for (const n of extra.nodes) if (!base.has(n.id)) base.set(n.id, { id: n.id, name: n.name, type: n.type, score: 0.12, hop: null, degree: n.degree });
    return Array.from(base.values());
  }, [trace, extra]);
  const edges: CanvasEdge[] = useMemo(
    () => [
      ...trace.edges.map((e) => ({ source: e.source, target: e.target, relation: e.relation, score: e.score })),
      ...extra.edges.map((e) => ({ source: e.source, target: e.target, relation: e.relation, confidence: e.confidence })),
    ],
    [trace, extra],
  );
  const lensSet = useMemo(() => (showLens ? new Set(lens) : undefined), [lens, showLens]);

  useEffect(() => {
    if (!playing) return;
    let h = hop === null || hop >= maxHop ? 0 : hop;
    setHop(h);
    const t = setInterval(() => {
      h += 1;
      if (h > maxHop) {
        clearInterval(t);
        setPlaying(false);
        setHop(null);
        return;
      }
      setHop(h);
    }, 900);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  async function expand(id: string) {
    setExpandError(undefined);
    try {
      const out = await graphrag.expand(subjectId, [id], 40);
      setExtra((cur) => ({
        nodes: [...cur.nodes, ...out.nodes.filter((n) => !cur.nodes.some((m) => m.id === n.id))],
        edges: [...cur.edges, ...out.edges],
      }));
    } catch (e) {
      setExpandError(e instanceof Error ? e.message : "Could not expand this entity");
    }
  }

  const sel = trace.nodes.find((n) => n.id === selected);
  const selEdges = selected ? trace.edges.filter((e) => e.source === selected || e.target === selected) : [];
  const nameOf = (id: string) => nodes.find((n) => n.id === id)?.name ?? id;
  const types = Array.from(new Set(nodes.map((n) => n.type))).sort();

  return (
    <Panel
      title="What Graph RAG found"
      description="Size shows how relevant the technique judged each entity. Double-click an entity to pull in its neighbours."
      actions={
        <>
          <Button onClick={() => setPlaying((p) => !p)}>{playing ? "Stop" : "Play spread"}</Button>
          {lens.length > 0 && (
            <label className="flex items-center gap-1.5 text-xs text-ink-secondary">
              <input type="checkbox" checked={showLens} onChange={(e) => setShowLens(e.target.checked)} />
              Vector lens
            </label>
          )}
        </>
      }
    >
      <div className="grid gap-4 p-4 lg:grid-cols-[1fr_280px]">
        <div>
          <GraphCanvas
            nodes={nodes}
            edges={edges}
            activation
            paths={trace.paths}
            lens={lensSet}
            playbackHop={hop}
            onSelect={setSelected}
            onExpand={expand}
            height={460}
          />
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-secondary">
            <label className="flex items-center gap-2">
              Hops shown
              <input
                type="range" min={0} max={maxHop} value={hop ?? maxHop}
                onChange={(e) => { setPlaying(false); setHop(Number(e.target.value) >= maxHop ? null : Number(e.target.value)); }}
              />
              <span className="tabular-nums text-ink">{hop ?? "all"}</span>
            </label>
            {types.map((t) => (
              <span key={t} className="flex items-center gap-1">
                <span className="size-2 rounded-full" style={{ background: TYPE_COLORS[t] ?? TYPE_COLORS.CONCEPT }} />
                {t.toLowerCase()}
              </span>
            ))}
            <span className="flex items-center gap-1"><span className="size-2.5 rounded-full border-2 border-ink" />seed</span>
            {lens.length > 0 && showLens && (
              <span className="flex items-center gap-1"><span className="size-2.5 rounded-full border-2 border-info" />also in vector evidence</span>
            )}
          </div>
          {expandError && <p className="mt-1 text-xs text-danger">{expandError}</p>}
        </div>

        <aside className="text-sm" aria-live="polite">
          {sel ? (
            <div className="space-y-2">
              <div>
                <div className="font-medium text-ink">{sel.name}</div>
                <div className="text-xs text-ink-tertiary">
                  {sel.type.toLowerCase()} · score {sel.score.toFixed(2)} · {sel.seed ? "seed" : `hop ${sel.hop ?? "?"}`}
                  {lens.includes(sel.id) ? " · seen by vector" : ""}
                </div>
              </div>
              <ul className="space-y-1 text-xs text-ink-secondary">
                {selEdges.slice(0, 12).map((e, i) => (
                  <li key={i}>
                    {nameOf(e.source)} <span className="text-accent">{e.relation.replace(/_/g, " ").toLowerCase()}</span> {nameOf(e.target)}
                  </li>
                ))}
              </ul>
              <Button onClick={() => expand(sel.id)}>Show neighbours</Button>
            </div>
          ) : (
            <p className="text-xs text-ink-tertiary">
              Select an entity to see its relationships. Seeds are the entities the question matched; the rest were
              reached by following facts.
            </p>
          )}
          <div className="mt-4">
            <h3 className="mb-1 text-xs font-medium uppercase tracking-wider text-ink-tertiary">Top entities</h3>
            <ol className="space-y-0.5 text-xs">
              {trace.nodes.slice(0, 8).map((n) => (
                <li key={n.id}>
                  <button type="button" onClick={() => setSelected(n.id)} className="flex w-full items-center justify-between gap-2 text-left hover:text-ink">
                    <span className="truncate text-ink-secondary">{n.name}</span>
                    <span className="tabular-nums text-ink-tertiary">{n.score.toFixed(2)}</span>
                  </button>
                </li>
              ))}
            </ol>
          </div>
        </aside>
      </div>
    </Panel>
  );
}
