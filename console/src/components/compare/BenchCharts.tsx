"use client";

import { Fragment, useState } from "react";
import { motion } from "framer-motion";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { pipelineLabel } from "@/lib/advisor";
import type { BenchmarkDetail, PipelineAggregate } from "@/lib/graphrag-types";
import { colorOfKey, fmtCost, fmtMs, fmtPct } from "./shared";

const AXIS = { fontSize: 11, fill: "var(--color-ink-secondary)" };
const GRID = "var(--color-line)";
const TOOLTIP = {
  contentStyle: {
    background: "var(--color-panel)",
    border: "1px solid var(--color-line)",
    borderRadius: 6,
    fontSize: 12,
    color: "var(--color-ink)",
  },
};

/** A shade per technique so graph:spreading and graph:ppr are distinguishable. */
const SHADES = ["#0a7c42", "#2f8f86", "#6b4fbb", "#b0562a", "#b0407a"];
export function seriesColor(key: string, keys: string[]): string {
  if (key === "vector") return colorOfKey("vector");
  if (key.startsWith("hybrid")) return colorOfKey("hybrid");
  const graphKeys = keys.filter((k) => k.startsWith("graph"));
  return SHADES[graphKeys.indexOf(key) % SHADES.length];
}

/* ---------------------------------------------------------------- scorecard */

export function Scorecard({ run }: { run: BenchmarkDetail | { aggregates: Record<string, PipelineAggregate> } }) {
  const keys = Object.keys(run.aggregates);
  const ci = (v: [number, number] | null) => (v ? `${fmtPct(v[0])}–${fmtPct(v[1])}` : "");
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-tertiary">
            <th className="px-4 py-2 font-medium">Pipeline</th>
            <th className="px-2 py-2 font-medium">Correct</th>
            <th className="px-2 py-2 font-medium">Recall@5</th>
            <th className="px-2 py-2 font-medium">MRR</th>
            <th className="px-2 py-2 font-medium">nDCG@10</th>
            <th className="px-2 py-2 font-medium">Latency p50 / p95</th>
            <th className="px-2 py-2 font-medium">Cost / question</th>
            <th className="px-2 py-2 font-medium">vs vector (W/T/L)</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const a = run.aggregates[k];
            const v = a.vs_vector;
            return (
              <tr key={k} className="border-b border-line-soft">
                <td className="px-4 py-2.5">
                  <span className="flex items-center gap-2 font-medium text-ink">
                    <span className="size-2.5 rounded-full" style={{ background: seriesColor(k, keys) }} />
                    {pipelineLabel(k)}
                  </span>
                  {a.errors > 0 && <span className="text-xs text-danger">{a.errors} failed</span>}
                </td>
                <td className="px-2 py-2.5 tabular-nums">
                  {fmtPct(a.correctness)}
                  <div className="text-[11px] text-ink-tertiary">{ci(a.correctness_ci)}</div>
                </td>
                <td className="px-2 py-2.5 tabular-nums">
                  {fmtPct(a.recall_at_5)}
                  <div className="text-[11px] text-ink-tertiary">{ci(a.recall_at_5_ci)}</div>
                </td>
                <td className="px-2 py-2.5 tabular-nums">{a.mrr?.toFixed(2) ?? "n/a"}</td>
                <td className="px-2 py-2.5 tabular-nums">{a.ndcg_at_10?.toFixed(2) ?? "n/a"}</td>
                <td className="px-2 py-2.5 tabular-nums">
                  {fmtMs(a.latency_p50_ms)} / {fmtMs(a.latency_p95_ms)}
                </td>
                <td className="px-2 py-2.5 tabular-nums">{fmtCost(a.cost_usd_per_question)}</td>
                <td className="px-2 py-2.5 tabular-nums">
                  {v ? (
                    <span>
                      <b className="text-ok">{v.wins}</b> / {v.ties} / <b className="text-danger">{v.losses}</b>
                    </span>
                  ) : (
                    <span className="text-ink-tertiary">baseline</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="px-4 py-2 text-[11px] text-ink-tertiary">
        Ranges are 95% bootstrap confidence intervals. With few questions they are wide, and overlapping ranges mean
        the difference may be noise. Correctness is judged by an LLM against the expected answer.
      </p>
    </div>
  );
}

/* -------------------------------------------------------------------- radar */

export function RadarPanel({ aggregates }: { aggregates: Record<string, PipelineAggregate> }) {
  const keys = Object.keys(aggregates);
  const inv = (vals: (number | null)[]) => {
    const v = vals.filter((x): x is number => x != null);
    const lo = Math.min(...v), hi = Math.max(...v);
    return (x: number | null) => (x == null ? 0 : hi === lo ? 1 : (hi - x) / (hi - lo));
  };
  const speed = inv(keys.map((k) => aggregates[k].latency_mean_ms));
  const economy = inv(keys.map((k) => aggregates[k].cost_usd_per_question));
  const axes = [
    { axis: "Correctness", get: (a: PipelineAggregate) => a.correctness ?? 0 },
    { axis: "Recall@5", get: (a: PipelineAggregate) => a.recall_at_5 ?? 0 },
    { axis: "MRR", get: (a: PipelineAggregate) => a.mrr ?? 0 },
    { axis: "nDCG@10", get: (a: PipelineAggregate) => a.ndcg_at_10 ?? 0 },
    { axis: "Speed", get: (a: PipelineAggregate) => speed(a.latency_mean_ms) },
    { axis: "Economy", get: (a: PipelineAggregate) => economy(a.cost_usd_per_question) },
  ];
  const data = axes.map(({ axis, get }) => ({
    axis,
    ...Object.fromEntries(keys.map((k) => [k, Math.round(get(aggregates[k]) * 100) / 100])),
  }));
  return (
    <div className="h-[340px] p-2">
      <ResponsiveContainer>
        <RadarChart data={data} outerRadius="72%">
          <PolarGrid stroke={GRID} />
          <PolarAngleAxis dataKey="axis" tick={AXIS} />
          <PolarRadiusAxis domain={[0, 1]} tick={false} axisLine={false} />
          {keys.map((k) => (
            <Radar
              key={k}
              name={pipelineLabel(k)}
              dataKey={k}
              stroke={seriesColor(k, keys)}
              fill={seriesColor(k, keys)}
              fillOpacity={0.12}
              strokeWidth={2}
              isAnimationActive
            />
          ))}
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Tooltip {...TOOLTIP} />
        </RadarChart>
      </ResponsiveContainer>
      <p className="text-center text-[11px] text-ink-tertiary">Speed and economy: 1 is the best pipeline in this run.</p>
    </div>
  );
}

/* ------------------------------------------------------------ per query type */

const METRICS = [
  { id: "correctness", label: "Correctness" },
  { id: "recall_at_5", label: "Recall@5" },
  { id: "latency_mean_ms", label: "Latency (ms)" },
] as const;

export function ByTypePanel({ run }: { run: BenchmarkDetail }) {
  const [metric, setMetric] = useState<(typeof METRICS)[number]["id"]>("correctness");
  const keys = Object.keys(run.aggregates);
  const types = Array.from(new Set(keys.flatMap((k) => Object.keys(run.by_type[k] ?? {})))).sort();
  const data = types.map((t) => ({
    type: `${t.replace(/_/g, " ")} (n=${Math.max(...keys.map((k) => run.by_type[k]?.[t]?.n ?? 0))})`,
    ...Object.fromEntries(keys.map((k) => [k, run.by_type[k]?.[t]?.[metric] ?? null])),
  }));
  const pct = metric !== "latency_mean_ms";
  return (
    <div className="p-4">
      <div className="mb-3 flex gap-1.5" role="radiogroup" aria-label="Metric">
        {METRICS.map((m) => (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={metric === m.id}
            onClick={() => setMetric(m.id)}
            className={`rounded-md border px-2.5 py-1 text-xs ${
              metric === m.id ? "border-brand bg-accent-soft text-accent" : "border-line text-ink-secondary hover:bg-surface-strong"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div className="h-[300px]">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ left: 0, right: 8 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="type" tick={AXIS} interval={0} />
            <YAxis tick={AXIS} domain={pct ? [0, 1] : undefined} tickFormatter={(v) => (pct ? `${Math.round(v * 100)}%` : fmtMs(v))} width={52} />
            <Tooltip {...TOOLTIP} formatter={(v: number) => (pct ? fmtPct(v) : fmtMs(v))} cursor={{ fill: "var(--color-surface)" }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {keys.map((k) => (
              <Bar key={k} name={pipelineLabel(k)} dataKey={k} fill={seriesColor(k, keys)} radius={[3, 3, 0, 0]} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-1 text-xs text-ink-secondary">
        This is the chart that decides which pipeline to use: look for the question types where the bars separate.
      </p>
    </div>
  );
}

/* ----------------------------------------------------------- win / tie / loss */

export function WinLoss({ aggregates }: { aggregates: Record<string, PipelineAggregate> }) {
  const rows = Object.entries(aggregates).filter(([, a]) => a.vs_vector);
  if (!rows.length) return <p className="p-4 text-sm text-ink-secondary">Run a graph pipeline to compare against vector.</p>;
  return (
    <ul className="space-y-3 p-4">
      {rows.map(([k, a]) => {
        const v = a.vs_vector!;
        const total = Math.max(1, v.wins + v.ties + v.losses);
        return (
          <li key={k}>
            <div className="mb-1 flex justify-between text-xs text-ink-secondary">
              <span className="font-medium text-ink">{pipelineLabel(k)} vs Vector RAG</span>
              <span>
                {v.wins} better · {v.ties} same · {v.losses} worse
              </span>
            </div>
            <div className="flex h-3 overflow-hidden rounded bg-surface-strong">
              {[
                { n: v.wins, c: "var(--color-ok)" },
                { n: v.ties, c: "var(--color-muted)" },
                { n: v.losses, c: "var(--color-danger)" },
              ].map((s, i) => (
                <motion.div key={i} initial={{ width: 0 }} animate={{ width: `${(s.n / total) * 100}%` }} transition={{ duration: 0.6 }} style={{ background: s.c }} />
              ))}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/* ---------------------------------------------------------- per question table */

export function QuestionTable({ run }: { run: BenchmarkDetail }) {
  const [open, setOpen] = useState<number | null>(null);
  const keys = Object.keys(run.aggregates).length
    ? Object.keys(run.aggregates)
    : Array.from(new Set(run.results.flatMap((r) => Object.keys(r.pipelines))));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-tertiary">
            <th className="px-4 py-2 font-medium">Question</th>
            <th className="px-2 py-2 font-medium">Type</th>
            {keys.map((k) => (
              <th key={k} className="px-2 py-2 font-medium">{pipelineLabel(k)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {run.results.map((q) => (
            <Fragment key={q.index}>
              <tr className="cursor-pointer border-b border-line-soft hover:bg-surface" onClick={() => setOpen(open === q.index ? null : q.index)}>
                <td className="max-w-[360px] truncate px-4 py-2 text-ink">{q.query}</td>
                <td className="px-2 py-2 text-xs text-ink-secondary">{q.query_type.replace(/_/g, " ")}</td>
                {keys.map((k) => {
                  const p = q.pipelines[k];
                  const score = p?.correctness ?? p?.recall_at_5;
                  return (
                    <td key={k} className="px-2 py-2 tabular-nums">
                      {!p ? "–" : p.status !== "ok" ? <span className="text-danger">error</span> : (
                        <span className={score == null ? "text-ink-tertiary" : score >= 1 ? "text-ok" : score <= 0 ? "text-danger" : "text-warn"}>
                          {score == null ? "n/a" : score.toFixed(1)}
                        </span>
                      )}
                      {p && <span className="ml-1.5 text-[11px] text-ink-tertiary">{fmtMs(p.total_ms)}</span>}
                    </td>
                  );
                })}
              </tr>
              {open === q.index && (
                <tr className="border-b border-line-soft bg-surface">
                  <td colSpan={keys.length + 2} className="px-4 py-3">
                    {q.expected_answer && <p className="mb-2 text-xs text-ink-secondary"><b className="text-ink">Expected:</b> {q.expected_answer}</p>}
                    <div className="grid gap-2 md:grid-cols-2">
                      {keys.map((k) => (
                        <div key={k} className="rounded-md border border-line bg-panel p-2 text-xs">
                          <div className="mb-1 font-medium text-ink">{pipelineLabel(k)}</div>
                          <p className="whitespace-pre-wrap text-ink-secondary">{q.pipelines[k]?.answer || q.pipelines[k]?.error || "no answer"}</p>
                        </div>
                      ))}
                    </div>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
