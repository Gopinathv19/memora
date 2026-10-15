"use client";

/**
 * Benchmarks: measure the pipelines over a question set.
 *
 * Questions are JSON lines in the same shape as the eval package's datasets
 * (`query`, `expected_answer`, `supporting_chunk_ids`, `query_type`), so a set
 * produced by `evals/scripts/generate_eval_set.py` and reviewed with
 * `review_eval_set.py` loads as is. The run is stored; the page follows it live
 * and keeps past runs for the Advisor.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";

import { graphrag } from "@/lib/graphrag-api";
import { TECHNIQUES, type BenchmarkDetail, type BenchmarkQuestion, type BenchmarkRun, type Technique } from "@/lib/graphrag-types";
import { IndexBanner, SubjectSelect, fmtCost, useCompareSubject } from "@/components/compare/shared";
import { ByTypePanel, QuestionTable, RadarPanel, Scorecard, WinLoss } from "@/components/compare/BenchCharts";
import { Button, EmptyState, ErrorState, PageHeader, Panel } from "@/components/ui";

const FORMAT_HINT = `{"query": "Who owns the audit findings?", "expected_answer": "…", "supporting_chunk_ids": ["<retrieval chunk id>"], "query_type": "multi_hop"}`;

function parseJsonl(text: string): { questions: BenchmarkQuestion[]; error?: string } {
  const questions: BenchmarkQuestion[] = [];
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  for (let i = 0; i < lines.length; i++) {
    try {
      const row = JSON.parse(lines[i]);
      if (typeof row.query !== "string" || !row.query.trim()) throw new Error("missing `query`");
      questions.push({
        query: row.query,
        expected_answer: row.expected_answer ?? null,
        supporting_chunk_ids: row.supporting_chunk_ids ?? [],
        query_type: row.query_type ?? "factoid",
      });
    } catch (e) {
      return { questions: [], error: `Line ${i + 1}: ${e instanceof Error ? e.message : "invalid JSON"}` };
    }
  }
  if (questions.length > 200) return { questions: [], error: "At most 200 questions per run" };
  return { questions };
}

export default function BenchmarksPage() {
  const { subjects, subjectId, setSubjectId } = useCompareSubject();
  const [runs, setRuns] = useState<BenchmarkRun[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<BenchmarkDetail | null>(null);
  const [error, setError] = useState<string>();

  const [label, setLabel] = useState("benchmark");
  const [text, setText] = useState("");
  const [techniques, setTechniques] = useState<Technique[]>(["spreading"]);
  const [hybrid, setHybrid] = useState(true);
  const [judge, setJudge] = useState(true);
  const [starting, setStarting] = useState(false);

  const parsed = useMemo(() => parseJsonl(text), [text]);

  const loadRuns = useCallback(async () => {
    try {
      const list = await graphrag.benchmarks.list();
      setRuns(list);
      setSelectedId((cur) => cur ?? list[0]?.id ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load benchmarks");
    }
  }, []);
  useEffect(() => void loadRuns(), [loadRuns]);

  // follow the selected run; poll while it is running
  useEffect(() => {
    if (!selectedId) return setDetail(null);
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const d = await graphrag.benchmarks.get(selectedId);
        if (stop) return;
        setDetail(d);
        if (d.status === "running") timer = setTimeout(tick, 3000);
        else void loadRuns();
      } catch (e) {
        if (!stop) setError(e instanceof Error ? e.message : "Could not load the run");
      }
    };
    void tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [selectedId, loadRuns]);

  async function start() {
    if (!parsed.questions.length || !subjectId) return;
    setStarting(true);
    setError(undefined);
    try {
      const run = await graphrag.benchmarks.start({
        subjectId, label: label.trim() || "benchmark", questions: parsed.questions,
        techniques, includeHybrid: hybrid, judge,
      });
      setSelectedId(run.id);
      await loadRuns();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the benchmark");
    } finally {
      setStarting(false);
    }
  }

  const done = detail?.results.length ?? 0;
  const total = detail?.question_count ?? 0;
  const hasResults = detail && Object.keys(detail.aggregates).length > 0;

  return (
    <>
      <PageHeader
        eyebrow="Graph RAG"
        title="Benchmarks"
        description="Run the pipelines over a set of questions and see where each one wins. Results are measured against the expected answers and supporting chunks, not guessed."
      />
      <IndexBanner subjectId={subjectId} />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}

      <details open={runs.length === 0} className="mb-4 rounded-lg border border-line bg-panel">
        <summary className="cursor-pointer select-none px-4 py-2.5 text-sm font-medium text-ink">New benchmark</summary>
        <div className="grid gap-4 border-t border-line p-4 lg:grid-cols-[1fr_320px]">

          <Panel>
            <div className="space-y-3 p-4 text-sm">
              <SubjectSelect subjects={subjects} value={subjectId} onChange={setSubjectId} />
              <label className="block">
                <span className="text-xs text-ink-secondary">Label</span>
                <input value={label} onChange={(e) => setLabel(e.target.value)} className="mt-1 h-[34px] w-full rounded-md border border-line bg-panel px-2 text-ink" />
              </label>
              <fieldset>
                <legend className="text-xs text-ink-secondary">Graph techniques</legend>
                <div className="mt-1 space-y-1">
                  {TECHNIQUES.map((t) => (
                    <label key={t.id} className="flex items-center gap-2 text-ink-secondary">
                      <input
                        type="checkbox"
                        checked={techniques.includes(t.id)}
                        onChange={(e) => setTechniques((cur) => e.target.checked ? [...cur, t.id] : cur.filter((x) => x !== t.id))}
                      />
                      {t.label}
                    </label>
                  ))}
                </div>
              </fieldset>
              <label className="flex items-center gap-2 text-ink-secondary"><input type="checkbox" checked={hybrid} onChange={(e) => setHybrid(e.target.checked)} />Include hybrid</label>
              <label className="flex items-center gap-2 text-ink-secondary"><input type="checkbox" checked={judge} onChange={(e) => setJudge(e.target.checked)} />Judge answer correctness (extra LLM calls)</label>
              <label className="block">
                <span className="text-xs text-ink-secondary">Questions (JSON lines)</span>
                <textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  rows={6}
                  placeholder={FORMAT_HINT}
                  className="mt-1 w-full rounded-md border border-line bg-panel p-2 font-mono text-xs text-ink placeholder:text-ink-tertiary"
                />
              </label>
              <input
                type="file"
                accept=".jsonl,.json,.txt"
                aria-label="Load questions from a file"
                onChange={async (e) => { const f = e.target.files?.[0]; if (f) setText(await f.text()); }}
                className="text-xs text-ink-secondary"
              />
              <p className="text-xs text-ink-tertiary">
                {parsed.error ? <span className="text-danger">{parsed.error}</span> : `${parsed.questions.length} question${parsed.questions.length === 1 ? "" : "s"} ready`}
                . Generate a set with <code>evals/scripts/generate_eval_set.py</code>.
              </p>
              <Button variant="primary" disabled={starting || !parsed.questions.length || !techniques.length || !subjectId} onClick={start}>
                {starting ? "Starting…" : "Run benchmark"}
              </Button>
            </div>
          </Panel>

          <Panel title="Past runs" counter={runs.length}>
            {runs.length === 0 ? (
              <p className="p-4 text-sm text-ink-secondary">No benchmarks yet.</p>
            ) : (
              <ul className="max-h-72 overflow-y-auto">
                {runs.map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(r.id)}
                      className={`flex w-full items-center justify-between gap-2 border-b border-line-soft px-4 py-2 text-left text-sm hover:bg-surface ${r.id === selectedId ? "bg-accent-soft" : ""}`}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-ink">{r.label}</span>
                        <span className="text-xs text-ink-tertiary">{new Date(r.created_at).toLocaleString()} · {r.question_count} questions</span>
                      </span>
                      <span className={`text-xs ${r.status === "done" ? "text-ok" : r.status === "failed" ? "text-danger" : "text-warn"}`}>{r.status}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </details>

      <div className="space-y-4">
        <div className="min-w-0 space-y-4">
          {!detail && <EmptyState title="No run selected" description="Start a benchmark, or pick a past run." />}
          {detail && (
            <>
              {detail.status === "running" && (
                <div className="rounded-lg border border-line bg-panel px-4 py-3" aria-live="polite">
                  <div className="mb-1.5 flex justify-between text-sm text-ink-secondary">
                    <span>Running “{detail.label}”</span>
                    <span className="tabular-nums">{done} / {total} questions</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded bg-surface-strong">
                    <motion.div className="h-full bg-brand" animate={{ width: `${total ? (done / total) * 100 : 0}%` }} transition={{ duration: 0.5 }} />
                  </div>
                </div>
              )}
              {detail.status === "failed" && <ErrorState message={detail.error ?? "The benchmark failed"} />}
              {hasResults && (
                <>
                  <Panel title="Scorecard" description={`${detail.label} · ${detail.question_count} questions · results are for this corpus only`}>
                    <Scorecard run={detail} />
                  </Panel>
                  <div className="grid gap-4 xl:grid-cols-2">
                    <Panel title="Profile"><RadarPanel aggregates={detail.aggregates} /></Panel>
                    <Panel title="Better or worse than vector, question by question"><WinLoss aggregates={detail.aggregates} /></Panel>
                  </div>
                  <Panel title="By question type"><ByTypePanel run={detail} /></Panel>
                  <BuildCost run={detail} />
                  <Panel title="Questions" description="Select a row to compare the answers.">
                    <QuestionTable run={detail} />
                  </Panel>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

function BuildCost({ run }: { run: BenchmarkDetail }) {
  const b = run.build_cost as Record<string, unknown>;
  const judgeCost = Number((run.config as Record<string, unknown>).judge_cost_usd ?? 0);
  const llm = Number(b.llm_cost_usd ?? 0);
  if (!b.status && !judgeCost) return null;
  return (
    <Panel title="What it cost to set up" description="Graph RAG spends effort before any question is asked; vector RAG's index was built when the documents were embedded.">
      <dl className="grid gap-4 p-4 text-sm sm:grid-cols-4">
        <Cost label="Entities embedded" value={String(b.entities_embedded ?? "n/a")} />
        <Cost label="Facts embedded" value={String(b.facts_embedded ?? "n/a")} />
        <Cost label="Community summaries" value={`${b.communities ?? 0} (${fmtCost(llm)})`} />
        <Cost label="Answer judging" value={fmtCost(judgeCost)} />
      </dl>
    </Panel>
  );
}

function Cost({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-ink-tertiary">{label}</dt>
      <dd className="font-medium tabular-nums text-ink">{value}</dd>
    </div>
  );
}
