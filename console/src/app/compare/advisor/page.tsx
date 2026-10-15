"use client";

/**
 * Advisor: which pipeline should this workload use?
 *
 * Everything shown is computed from a benchmark's measured numbers by
 * `lib/advisor.ts`; nothing is a model's opinion. The user sets what matters
 * (accuracy, speed, cost, explainability) and what their questions are like
 * (lookups, relational, broad); the recommendation, the ranking and the
 * per-question-type verdicts update as the sliders move.
 */

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

import { advise, pipelineLabel, type Mix, type Priorities } from "@/lib/advisor";
import { graphrag } from "@/lib/graphrag-api";
import type { BenchmarkDetail, BenchmarkRun } from "@/lib/graphrag-types";
import { colorOfKey, fmtPct } from "@/components/compare/shared";
import { seriesColor } from "@/components/compare/BenchCharts";
import { EmptyState, ErrorState, PageHeader, Panel } from "@/components/ui";

const PRIORITY_LABELS: { key: keyof Priorities; label: string; hint: string }[] = [
  { key: "accuracy", label: "Accuracy", hint: "Right answers, measured on this corpus" },
  { key: "speed", label: "Speed", hint: "Lower mean latency" },
  { key: "economy", label: "Low cost", hint: "Fewer tokens and LLM calls per question" },
  { key: "explainability", label: "Explainability", hint: "Facts and paths you can show a user (a property of the method, not measured)" },
];

const MIX_LABELS: { key: keyof Mix; label: string; hint: string }[] = [
  { key: "lookup", label: "Lookups", hint: "One fact in one place" },
  { key: "relational", label: "Relational", hint: "Multi-hop or across documents" },
  { key: "broad", label: "Broad", hint: "Themes and summaries" },
];

export default function AdvisorPage() {
  const [runs, setRuns] = useState<BenchmarkRun[]>([]);
  const [runId, setRunId] = useState<string>("");
  const [run, setRun] = useState<BenchmarkDetail | null>(null);
  const [error, setError] = useState<string>();
  const [priorities, setPriorities] = useState<Priorities>({ accuracy: 8, speed: 4, economy: 3, explainability: 3 });
  const [mix, setMix] = useState<Mix>({ lookup: 5, relational: 4, broad: 1 });

  useEffect(() => {
    graphrag.benchmarks
      .list()
      .then((list) => {
        const finished = list.filter((r) => r.status === "done");
        setRuns(finished);
        if (finished[0]) setRunId(finished[0].id);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load benchmarks"));
  }, []);

  useEffect(() => {
    if (!runId) return setRun(null);
    graphrag.benchmarks.get(runId).then(setRun).catch((e) => setError(e instanceof Error ? e.message : "Could not load the run"));
  }, [runId]);

  const advice = useMemo(() => (run && Object.keys(run.aggregates).length ? advise(run, priorities, mix) : null), [run, priorities, mix]);

  // what-if: who wins as the weight on accuracy goes from 0 to 10
  const sweep = useMemo(
    () => run && Object.keys(run.aggregates).length
      ? Array.from({ length: 11 }, (_, w) => ({ w, winner: advise(run, { ...priorities, accuracy: w }, mix).winner?.key ?? "" }))
      : [],
    [run, priorities, mix],
  );

  return (
    <>
      <PageHeader
        eyebrow="Graph RAG"
        title="Advisor"
        description="Tell it what matters and what your questions look like. The recommendation comes from the numbers a benchmark measured, and it says when the numbers are too close to call."
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      {runs.length === 0 ? (
        <EmptyState title="No finished benchmark yet" description="Run a benchmark first; the Advisor reads its results." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
          <div className="space-y-4">
            <Panel title="Benchmark">
              <div className="p-4">
                <select value={runId} onChange={(e) => setRunId(e.target.value)} className="h-[34px] w-full rounded-md border border-line bg-panel px-2 text-sm text-ink" aria-label="Benchmark run">
                  {runs.map((r) => (
                    <option key={r.id} value={r.id}>{r.label} · {new Date(r.created_at).toLocaleDateString()} · {r.question_count}q</option>
                  ))}
                </select>
              </div>
            </Panel>
            <Panel title="What matters to you">
              <Sliders items={PRIORITY_LABELS} values={priorities} onChange={(k, v) => setPriorities((p) => ({ ...p, [k]: v }))} />
            </Panel>
            <Panel title="What your questions look like" description="Relative shares; they need not add up to 100.">
              <Sliders items={MIX_LABELS} values={mix} onChange={(k, v) => setMix((m) => ({ ...m, [k]: v }))} />
            </Panel>
          </div>

          <div className="min-w-0 space-y-4">
            {advice?.winner && (
              <>
                <AnimatePresence mode="wait">
                  <motion.div
                    key={advice.winner.key}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    className="rounded-lg border border-line bg-panel p-5"
                    style={{ borderLeftColor: colorOfKey(advice.winner.key), borderLeftWidth: 4 }}
                    aria-live="polite"
                  >
                    <div className="text-xs font-medium uppercase tracking-wider text-ink-tertiary">Recommended for these priorities</div>
                    <div className="mt-1 text-2xl font-medium text-ink">{advice.winner.label}</div>
                    <ul className="mt-3 space-y-1 text-sm text-ink-secondary">
                      {advice.reasons.map((r, i) => <li key={i}>• {r}</li>)}
                    </ul>
                  </motion.div>
                </AnimatePresence>

                <Panel title="Ranking" description="Each bar is the weighted average of four scores between 0 and 1.">
                  <ul className="space-y-3 p-4">
                    {advice.ranking.map((r, i) => (
                      <motion.li key={r.key} layout>
                        <div className="mb-1 flex justify-between text-sm">
                          <span className="font-medium text-ink">{i + 1}. {r.label}</span>
                          <span className="tabular-nums text-ink">{Math.round(r.total * 100)}</span>
                        </div>
                        <div className="h-2.5 overflow-hidden rounded bg-surface-strong">
                          <motion.div className="h-full rounded" style={{ background: seriesColor(r.key, advice.ranking.map((x) => x.key)) }} animate={{ width: `${Math.max(2, r.total * 100)}%` }} transition={{ duration: 0.5 }} />
                        </div>
                        <div className="mt-1 flex flex-wrap gap-x-4 text-[11px] text-ink-tertiary">
                          <span>accuracy {fmtPct(r.accuracy)}</span>
                          <span>speed {fmtPct(r.speed)}</span>
                          <span>economy {fmtPct(r.economy)}</span>
                          <span>explainability {fmtPct(r.explainability)}</span>
                        </div>
                      </motion.li>
                    ))}
                  </ul>
                </Panel>

                <Panel title="By question type" description="Vector RAG against the best graph-based pipeline for each kind of question, and whether the lead is bigger than the noise for that many questions.">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-tertiary">
                        <th className="px-4 py-2 font-medium">Type</th><th className="px-2 py-2 font-medium">n</th><th className="px-2 py-2 font-medium">Ahead</th><th className="px-2 py-2 font-medium">Lead</th><th className="px-2 py-2 font-medium">Verdict</th>
                      </tr>
                    </thead>
                    <tbody>
                      {advice.perType.map((t) => (
                        <tr key={t.queryType} className="border-b border-line-soft">
                          <td className="px-4 py-2 text-ink">{t.queryType.replace(/_/g, " ")}</td>
                          <td className="px-2 py-2 tabular-nums text-ink-secondary">{t.n}</td>
                          <td className="px-2 py-2 text-ink">{t.winner ? pipelineLabel(t.winner) : "tie"}</td>
                          <td className="px-2 py-2 tabular-nums text-ink-secondary">{(t.margin * 100).toFixed(0)} pts <span className="text-ink-tertiary">({t.metric})</span></td>
                          <td className="px-2 py-2">
                            <span className={`rounded px-1.5 py-0.5 text-xs ${t.confidence === "clear" ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn"}`}>{t.confidence === "clear" ? "clear" : "too close to call"}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Panel>

                <Panel title="What if accuracy mattered more or less?" description="The recommendation as the weight on accuracy goes from 0 to 10, other sliders unchanged.">
                  <div className="p-4">
                    <div className="flex h-8 overflow-hidden rounded">
                      {sweep.map((s) => (
                        <div key={s.w} className="flex-1" style={{ background: colorOfKey(s.winner) }} title={`Accuracy weight ${s.w}: ${pipelineLabel(s.winner)}`} />
                      ))}
                    </div>
                    <div className="mt-1 flex justify-between text-[11px] text-ink-tertiary"><span>0</span><span>accuracy weight</span><span>10</span></div>
                  </div>
                </Panel>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function Sliders<K extends string>({
  items, values, onChange,
}: {
  items: { key: K; label: string; hint: string }[];
  values: Record<K, number>;
  onChange: (key: K, v: number) => void;
}) {
  return (
    <div className="space-y-4 p-4">
      {items.map((it) => (
        <div key={it.key}>
          <div className="flex justify-between text-sm">
            <label htmlFor={`s-${it.key}`} className="text-ink">{it.label}</label>
            <span className="tabular-nums text-ink-secondary">{values[it.key]}</span>
          </div>
          <input id={`s-${it.key}`} type="range" min={0} max={10} value={values[it.key]} onChange={(e) => onChange(it.key, Number(e.target.value))} className="w-full accent-[var(--color-brand)]" />
          <p className="text-[11px] text-ink-tertiary">{it.hint}</p>
        </div>
      ))}
    </div>
  );
}
