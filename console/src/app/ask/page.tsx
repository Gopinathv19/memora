"use client";

/**
 * Ask Memora: the retrieval interface.
 *
 * One question in, one grounded answer out, with the supporting chunks
 * underneath. The pipeline runs server-side (query embedding -> HNSW top 50
 * -> MMR top 10 -> reranker top 5 -> LLM); this page only submits the query
 * and renders the response: the answer, the per-stage candidate counts, and
 * each evidence chunk with its similarity and rerank scores.
 */

import { useState } from "react";

import { api } from "@/lib/api";
import type { QueryResponse, RetrievedChunk } from "@/lib/types";
import {
  Button,
  ErrorState,
  Mono,
  PageHeader,
  Panel,
  StatusBadge,
} from "@/components/ui";

export default function AskPage() {
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const [result, setResult] = useState<QueryResponse | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q || pending) return;
    setPending(true);
    setError(undefined);
    try {
      setResult(await api.retrieval.ask(q));
    } catch (err) {
      setError(err instanceof Error ? err.message : "The query failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Retrieval"
        title="Ask Memora"
        description="Ask a question about your documents. Answers are grounded in the retrieved chunks shown below, and the pipeline says so when the knowledge is insufficient."
      />

      <Panel
        title="Question"
        description="Answered from your embedded chunks: HNSW candidates → MMR → reranker → LLM."
      >
        <form onSubmit={submit} className="flex flex-col gap-3 px-4 py-4">
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. What is the total amount on the invoice?"
            rows={3}
            maxLength={4000}
            className="w-full resize-y rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-tertiary focus:border-brand focus:outline-none"
          />
          <div className="flex items-center justify-between">
            <span className="text-xs text-ink-tertiary">
              {query.length}/4000
            </span>
            <Button type="submit" variant="primary" disabled={pending || !query.trim()}>
              {pending ? "Thinking…" : "Ask"}
            </Button>
          </div>
        </form>
      </Panel>

      {error && (
        <div className="mt-5">
          <Panel>
            <ErrorState message={error} />
          </Panel>
        </div>
      )}

      {result && (
        <div className="mt-5 space-y-5">
          {/* ------------------------------------------------------- answer */}
          <Panel
            title="Answer"
            description={`Strategy: ${result.strategy_name}`}
            actions={
              <span className="text-xs text-ink-tertiary">
                {result.usage.prompt_tokens.toLocaleString()} in ·{" "}
                {result.usage.completion_tokens.toLocaleString()} out ·{" "}
                {result.usage.latency_ms.toLocaleString()} ms
              </span>
            }
          >
            <div className="px-4 py-4">
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink">
                {result.answer}
              </p>
            </div>
          </Panel>

          {/* --------------------------------------------------- pipeline trace */}
          <Panel title="Pipeline" description="Candidates at each stage.">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 text-sm">
              <Trace label="HNSW" value={result.retrieval.hnsw_candidates} />
              <span aria-hidden className="text-ink-tertiary">→</span>
              <Trace label="MMR" value={result.retrieval.mmr_candidates} />
              <span aria-hidden className="text-ink-tertiary">→</span>
              <Trace label="Reranker" value={result.retrieval.final_chunks} />
              <span aria-hidden className="text-ink-tertiary">→</span>
              <Trace label="LLM" value="answer" />
              {!result.retrieval.reranker_used && (
                <StatusBadge status="suspended" />
              )}
              {!result.retrieval.reranker_used && (
                <span className="text-xs text-ink-tertiary">
                  reranker unavailable — MMR order used
                </span>
              )}
            </div>
          </Panel>

          {/* ------------------------------------------------------ evidence */}
          <Panel
            title="Supporting chunks"
            description="The evidence the answer was generated from."
            counter={result.chunks.length}
          >
            {result.chunks.length === 0 ? (
              <div className="px-4 py-10 text-center text-sm text-ink-secondary">
                No chunks were retrieved. Embed some sources first.
              </div>
            ) : (
              <ul className="divide-y divide-line-soft">
                {result.chunks.map((chunk, i) => (
                  <EvidenceChunk key={chunk.chunk_id} index={i} chunk={chunk} />
                ))}
              </ul>
            )}
          </Panel>
        </div>
      )}
    </>
  );
}

function Trace({ label, value }: { label: string; value: number | string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-xs font-medium uppercase tracking-wide text-ink-tertiary">
        {label}
      </span>
      <span className="font-medium text-ink tabular-nums">{value}</span>
    </span>
  );
}

function EvidenceChunk({ index, chunk }: { index: number; chunk: RetrievedChunk }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <li className="px-4 py-3">
      <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span className="font-mono text-ink-tertiary">#{index + 1}</span>
        <span className="rounded bg-surface-strong px-1.5 py-0.5 font-medium text-ink-secondary">
          {chunk.content_type}
        </span>
        <span className="text-ink-secondary">
          similarity {chunk.similarity.toFixed(3)}
        </span>
        {chunk.rerank_score !== null && (
          <span className="text-ink-secondary">
            rerank {chunk.rerank_score.toFixed(3)}
          </span>
        )}
        {chunk.page_start !== null && (
          <span className="text-ink-tertiary">
            page {chunk.page_start}
            {chunk.page_end && chunk.page_end !== chunk.page_start
              ? `–${chunk.page_end}`
              : ""}
          </span>
        )}
        <a
          href={`/sources/${chunk.source_id}`}
          className="text-ink-tertiary underline-offset-2 hover:text-ink hover:underline"
        >
          source <Mono>{chunk.source_id.split("-")[0]}</Mono>
        </a>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="ml-auto text-ink-tertiary underline-offset-2 hover:text-ink hover:underline"
        >
          {expanded ? "Collapse" : "Expand"}
        </button>
      </div>

      {chunk.section_path.length > 0 && (
        <div className="mb-1.5 text-xs text-ink-tertiary">
          {chunk.section_path.map((part, i) => (
            <span key={i}>
              {i > 0 && <span className="mx-1">›</span>}
              <span
                className={
                  i === chunk.section_path.length - 1
                    ? "font-medium text-ink-secondary"
                    : ""
                }
              >
                {part}
              </span>
            </span>
          ))}
        </div>
      )}

      <pre
        className={`overflow-auto rounded-md border border-line-soft bg-surface p-3 text-xs leading-relaxed whitespace-pre-wrap break-words text-ink ${
          expanded ? "max-h-none" : "max-h-40"
        }`}
      >
        {chunk.content}
      </pre>
    </li>
  );
}
