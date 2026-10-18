"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { api } from "@/lib/api";
import { graphrag } from "@/lib/graphrag-api";
import {
  TECHNIQUES,
  type IndexStatus,
  type PipelineResult,
  type Technique,
} from "@/lib/graphrag-types";
import { useResource } from "@/lib/useResource";
import { useTenant } from "@/lib/tenant-context";
import { Button, Panel } from "@/components/ui";

/* ------------------------------------------------------------------ format */

export const fmtMs = (ms: number | null | undefined) =>
  ms == null ? "n/a" : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;

export const fmtCost = (usd: number | null | undefined) =>
  usd == null ? "n/a" : usd === 0 ? "$0" : usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(3)}`;

export const fmtPct = (v: number | null | undefined) =>
  v == null ? "n/a" : `${Math.round(v * 100)}%`;

export const PIPELINE_COLOR: Record<string, string> = {
  vector: "#2a6fb0",
  graph: "#0a7c42",
  hybrid: "#a0721a",
};

export const colorOfKey = (key: string) => PIPELINE_COLOR[key.split(":")[0]] ?? "#8f8f8f";

/* -------------------------------------------------------------------- tabs */

const TABS = [
  { href: "/compare", label: "Arena" },
  { href: "/compare/benchmarks", label: "Benchmarks" },
  { href: "/compare/graph", label: "Graph explorer" },
  { href: "/compare/inspect", label: "Source inspector" },
];

export function CompareTabs() {
  const path = usePathname();
  return (
    <nav className="mb-5 flex gap-1 border-b border-line" aria-label="Graph RAG sections">
      {TABS.map((t) => {
        const active = path === t.href;
        return (
          <Link
            key={t.href}
            href={t.href}
            className={`relative px-3 py-2 text-sm transition-colors ${
              active ? "text-ink" : "text-ink-secondary hover:text-ink"
            }`}
          >
            {t.label}
            {active && (
              <motion.span
                layoutId="compare-tab"
                className="absolute inset-x-2 -bottom-px h-0.5 rounded bg-brand"
              />
            )}
          </Link>
        );
      })}
    </nav>
  );
}

/* ----------------------------------------------------------- subject picker */

const SUBJECT_KEY = "memora.compare.subject";

/** The subject every Graph RAG page works on, remembered across pages. */
export function useCompareSubject() {
  const { tenant } = useTenant();
  const subjects = useResource(
    () => api.subjects.list({ tenantId: tenant?.id, rootsOnly: false }),
    [tenant?.id],
  );
  const [subjectId, setSubjectIdState] = useState<string>("");

  useEffect(() => {
    try {
      const saved = localStorage.getItem(SUBJECT_KEY);
      if (saved) setSubjectIdState(saved);
    } catch {
      /* storage unavailable: start empty */
    }
  }, []);

  const list = subjects.data ?? [];
  useEffect(() => {
    if (!list.length) return;
    if (!subjectId || !list.some((s) => s.id === subjectId)) {
      const best = [...list].sort((a, b) => b.source_count - a.source_count)[0];
      setSubjectIdState(best.id);
    }
  }, [list, subjectId]);

  const setSubjectId = useCallback((id: string) => {
    setSubjectIdState(id);
    try {
      localStorage.setItem(SUBJECT_KEY, id);
    } catch {
      /* ignore */
    }
  }, []);

  return { subjects: list, loading: subjects.loading, subjectId, setSubjectId };
}

export function SubjectSelect({
  subjects,
  value,
  onChange,
}: {
  subjects: { id: string; external_id: string; source_count: number }[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-ink-secondary">
      Subject
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-[34px] rounded-md border border-line bg-panel px-2 text-sm text-ink"
      >
        {subjects.map((s) => (
          <option key={s.id} value={s.id}>
            {s.external_id} ({s.source_count} source{s.source_count === 1 ? "" : "s"})
          </option>
        ))}
      </select>
    </label>
  );
}

/* ------------------------------------------------------------- index banner */

/** What Graph RAG can use for this subject, and a button to build what is missing. */
export function IndexBanner({
  subjectId,
  onStatus,
}: {
  subjectId: string;
  onStatus?: (s: IndexStatus | null) => void;
}) {
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [error, setError] = useState<string>();
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    if (!subjectId) return;
    try {
      const s = await graphrag.status(subjectId);
      setStatus(s);
      setError(undefined);
      onStatus?.(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the index status");
      onStatus?.(null);
    }
  }, [subjectId, onStatus]);

  useEffect(() => {
    setStatus(null);
    void load();
  }, [load]);

  const running = status?.build?.status === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => void load(), 2500);
    return () => clearInterval(t);
  }, [running, load]);

  async function build() {
    setStarting(true);
    try {
      await graphrag.buildIndex(subjectId, true);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the build");
    } finally {
      setStarting(false);
    }
  }

  if (error)
    return (
      <div className="mb-4 rounded-lg border border-warn/40 bg-warn-soft px-4 py-3 text-sm text-warn">
        Graph RAG is unavailable for this subject: {error}
      </div>
    );
  if (!status) return <div className="mb-4 h-14 animate-pulse rounded-lg bg-surface" />;

  const c = status.coverage;
  const pct = c.entities ? Math.round((c.entities_embedded / c.entities) * 100) : 0;
  const missing = status.files_without_graph ?? [];
  return (
    <div className="mb-4">
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-line bg-panel px-4 py-3">
      <div className="min-w-[170px]">
        <div className="text-xs font-medium uppercase tracking-wider text-ink-tertiary">
          Graph index
        </div>
        <div className="mt-1 flex items-center gap-2">
          <div className="h-1.5 w-28 overflow-hidden rounded bg-surface-strong">
            <motion.div
              className="h-full bg-brand"
              initial={false}
              animate={{ width: `${running ? Math.max(pct, 8) : pct}%` }}
              transition={{ duration: 0.6 }}
            />
          </div>
          <span className="text-sm text-ink">
            {status.ready ? "Ready" : running ? "Building…" : c.entities ? `${pct}% embedded` : "Not built"}
          </span>
        </div>
      </div>
      <Stat label="Entities" value={c.entities} />
      <Stat label="Facts" value={c.facts} />
      <Stat label="Communities" value={c.communities} />
      {status.build?.status === "failed" && (
        <span className="text-sm text-danger">Build failed: {status.build.error}</span>
      )}
      {status.build?.status === "done" && (
        <span className="text-xs text-ink-tertiary">
          Last build: {status.build.entities_embedded} entities, {status.build.facts_embedded} facts
          {status.build.llm_cost_usd ? `, ${fmtCost(status.build.llm_cost_usd)} of LLM calls` : ""}
        </span>
      )}
      <div className="ml-auto">
        <Button
          variant={status.ready && c.communities > 0 ? "normal" : "primary"}
          disabled={starting || running || c.entities === 0}
          onClick={build}
          title={c.entities === 0 ? "Build the knowledge graph for this subject's sources first" : undefined}
        >
          {running ? "Building…" : status.ready ? "Rebuild index" : "Build graph index"}
        </Button>
      </div>
    </div>
    {missing.length > 0 && (
      <div className="mt-2 rounded-lg border border-warn/40 bg-warn-soft px-4 py-2.5 text-sm text-warn" role="status">
        <b>{missing.length} of {status.sources} files have no knowledge graph</b>, so Graph RAG cannot see them and
        vector RAG may answer questions about them that Graph RAG cannot:{" "}
        {missing.slice(0, 5).map((f, i) => (
          <span key={f.id}>
            {i > 0 && ", "}
            <Link href={`/sources/${f.id}`} className="underline">{f.filename ?? f.id.slice(0, 8)}</Link>
            {!f.extracted && " (not extracted yet)"}
          </span>
        ))}
        {missing.length > 5 && `, and ${missing.length - 5} more`}. Build a file&apos;s graph from its page.
      </div>
    )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-xs text-ink-tertiary">{label}</div>
      <div className="text-sm font-medium text-ink">{value.toLocaleString()}</div>
    </div>
  );
}

/* -------------------------------------------------------- technique picker */

export function TechniquePicker({
  value,
  onChange,
}: {
  value: Technique;
  onChange: (t: Technique) => void;
}) {
  const current = TECHNIQUES.find((t) => t.id === value)!;
  return (
    <div>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Graph RAG technique">
        {TECHNIQUES.map((t) => (
          <button
            key={t.id}
            type="button"
            role="radio"
            aria-checked={t.id === value}
            onClick={() => onChange(t.id)}
            className={`rounded-md border px-2.5 py-1 text-sm transition-colors ${
              t.id === value
                ? "border-brand bg-accent-soft text-accent"
                : "border-line bg-panel text-ink-secondary hover:bg-surface-strong"
            }`}
          >
            {t.short}
          </button>
        ))}
      </div>
      <AnimatePresence mode="wait">
        <motion.p
          key={current.id}
          initial={{ opacity: 0, y: 3 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          className="mt-2 max-w-2xl text-xs text-ink-secondary"
        >
          <span className="font-medium text-ink">{current.label}.</span> {current.blurb}{" "}
          <span className="text-ink-tertiary">Best for: {current.bestFor.toLowerCase()}.</span>
        </motion.p>
      </AnimatePresence>
    </div>
  );
}

/* -------------------------------------------------------------- timing bars */

const STAGE_LABEL: Record<string, string> = {
  embed_ms: "Embed question",
  embed_and_lexical_ms: "Embed + lexical",
  search_ms: "Search",
  graph_ms: "Graph traversal",
  bridge_ms: "Map to chunks",
  vector_search_ms: "Vector search",
  mmr_ms: "MMR",
  rerank_ms: "Rerank",
  llm_ms: "Answer (LLM)",
};
const STAGE_ORDER = Object.keys(STAGE_LABEL);

/** Where the time went, one animated bar per stage. */
export function TimingBars({ timings, color }: { timings: Record<string, number>; color: string }) {
  // Only stages this pipeline actually ran. `lexical_ms` runs inside
  // `embed_and_lexical_ms`, so only the parent is shown.
  const rows = useMemo(
    () => STAGE_ORDER.filter((k) => timings[k] != null).map((k) => ({ key: k, ms: timings[k] })),
    [timings],
  );
  const max = Math.max(1, ...rows.map((r) => r.ms));
  return (
    <ul className="space-y-1.5">
      {rows.map((r, i) => (
        <li key={r.key} className="grid grid-cols-[110px_1fr_56px] items-center gap-2 text-xs">
          <span className="truncate text-ink-secondary">{STAGE_LABEL[r.key]}</span>
          <span className="h-2 overflow-hidden rounded bg-surface-strong">
            <motion.span
              className="block h-full rounded"
              style={{ background: color }}
              initial={{ width: 0 }}
              animate={{ width: `${Math.max(2, (r.ms / max) * 100)}%` }}
              transition={{ duration: 0.6, delay: i * 0.05, ease: "easeOut" }}
            />
          </span>
          <span className="text-right tabular-nums text-ink">{fmtMs(r.ms)}</span>
        </li>
      ))}
    </ul>
  );
}

/* ---------------------------------------------------------- pipeline result */

export function PipelineCard({
  title,
  subtitle,
  color,
  result,
  pending,
  citedBy,
  children,
}: {
  title: string;
  subtitle?: string;
  color: string;
  result?: PipelineResult;
  pending: boolean;
  /** chunk ids the *other* pipeline also cited */
  citedBy?: Set<string>;
  children?: React.ReactNode;
}) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <motion.section
      layout
      className="flex flex-col rounded-lg border border-line bg-panel"
      style={{ borderTopColor: color, borderTopWidth: 3 }}
    >
      <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div>
          <h2 className="text-sm font-medium text-ink">{title}</h2>
          {subtitle && <p className="text-xs text-ink-tertiary">{subtitle}</p>}
        </div>
        {result && result.status === "ok" && (
          <div className="flex gap-4 text-right">
            <Metric label="Time" value={fmtMs(result.total_ms)} />
            <Metric label="Cost" value={fmtCost(result.cost_usd)} />
            <Metric label="Tokens" value={(result.prompt_tokens + result.completion_tokens).toLocaleString()} />
          </div>
        )}
      </header>

      <div className="space-y-4 p-4">
        {pending && !result && (
          <div className="space-y-2" aria-busy="true" aria-label={`${title} is working`}>
            <div className="h-3 w-5/6 animate-pulse rounded bg-surface-strong" />
            <div className="h-3 w-full animate-pulse rounded bg-surface-strong" />
            <div className="h-3 w-2/3 animate-pulse rounded bg-surface-strong" />
          </div>
        )}
        <AnimatePresence>
          {result?.status === "error" && (
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="rounded-md border border-danger/40 bg-danger-soft px-3 py-2 text-sm text-danger"
            >
              {result.error}
            </motion.p>
          )}
          {result?.status === "ok" && (
            <motion.div
              key="ok"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.25 }}
              className="space-y-4"
            >
              {result.evidence?.weak && (
                <p className="rounded-md border border-warn/40 bg-warn-soft px-3 py-2 text-xs text-warn" role="status">
                  <b>Weak evidence.</b>{" "}
                  {result.evidence.best_rerank == null
                    ? "Nothing relevant was retrieved."
                    : `The best passage scored ${result.evidence.best_rerank.toFixed(1)} on the reranker (passages that answer a question usually score above -3).`}{" "}
                  The documents may not contain the answer, or this pipeline did not find it.
                </p>
              )}
              {result.evidence?.anchored === false && result.technique !== "community" && (
                <p className="rounded-md border border-line bg-surface px-3 py-2 text-xs text-ink-secondary">
                  No name in the question matched an entity in the graph, so the starting points were guesses by
                  similarity. If the file you expect is not in the graph, build it.
                </p>
              )}
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink">{result.answer}</p>
              <TimingBars timings={result.timings_ms} color={color} />
              {children}
              <div>
                <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-ink-tertiary">
                  Evidence ({result.chunks.length})
                </h3>
                <ul className="space-y-1.5">
                  {result.chunks.map((c, i) => {
                    const shared = citedBy?.has(c.chunk_id);
                    const expanded = open === c.chunk_id;
                    return (
                      <li key={c.chunk_id} className="rounded-md border border-line-soft bg-surface">
                        <button
                          type="button"
                          onClick={() => setOpen(expanded ? null : c.chunk_id)}
                          className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs"
                        >
                          <span className="font-medium text-ink">#{i + 1}</span>
                          <span className="flex-1 truncate text-ink-secondary">{c.content.replace(/\s+/g, " ")}</span>
                          {c.page_start != null && <span className="text-ink-tertiary">p.{c.page_start}</span>}
                          {c.rerank_score != null && (
                            <span className="tabular-nums text-ink-tertiary">r {c.rerank_score.toFixed(2)}</span>
                          )}
                          <span
                            className={`rounded px-1.5 py-0.5 ${shared ? "bg-info-soft text-info" : "bg-warn-soft text-warn"}`}
                            title={shared ? "Cited by both pipelines" : "Only this pipeline cited it"}
                          >
                            {shared ? "shared" : "only here"}
                          </span>
                        </button>
                        <AnimatePresence>
                          {expanded && (
                            <motion.div
                              initial={{ height: 0, opacity: 0 }}
                              animate={{ height: "auto", opacity: 1 }}
                              exit={{ height: 0, opacity: 0 }}
                              className="overflow-hidden"
                            >
                              <p className="whitespace-pre-wrap border-t border-line-soft px-2.5 py-2 text-xs text-ink-secondary">
                                {c.content}
                              </p>
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider text-ink-tertiary">{label}</div>
      <div className="text-sm font-medium tabular-nums text-ink">{value}</div>
    </div>
  );
}

export { Panel };
