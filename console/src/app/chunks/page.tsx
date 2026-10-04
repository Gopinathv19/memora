"use client";

/**
 * The Chunks Viewer: a hierarchical folder/file tree on the left, and the
 * retrieval chunks of the selected source on the right.
 *
 * The tree mirrors the ownership chain the console already teaches: subjects
 * nest (folders), sources sit inside them. Selecting a source loads its
 * active-version chunks via GET /sources/{id}/chunks; each chunk shows its
 * section path, token count, pages, type and full content.
 */

import { useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import type { ChunkEmbeddingDebug, RetrievalChunk, Source, Subject } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import {
  Button,
  ErrorState,
  Mono,
  PageHeader,
  Panel,
  StatusBadge,
} from "@/components/ui";

/* ------------------------------------------------------------------ tree */

interface TreeNode {
  subject: Subject;
  children: TreeNode[];
  sources: Source[];
}

/** Build a nested tree from the flat subject + source lists. */
function buildTree(subjects: Subject[], sources: Source[]): TreeNode[] {
  const byId = new Map<string, TreeNode>();
  for (const s of subjects) byId.set(s.id, { subject: s, children: [], sources: [] });

  for (const src of sources) {
    const parent = byId.get(src.subject_id);
    if (parent) parent.sources.push(src);
  }

  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    const parentId = node.subject.parent_subject_id;
    const parent = parentId ? byId.get(parentId) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }

  const sortTree = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => a.subject.external_id.localeCompare(b.subject.external_id));
    for (const n of nodes) {
      n.sources.sort((a, b) =>
        (a.filename ?? a.id).localeCompare(b.filename ?? b.id),
      );
      sortTree(n.children);
    }
  };
  sortTree(roots);
  return roots;
}

function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-[16px] shrink-0 text-ink-secondary"
    >
      <path
        d={
          open
            ? "M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.5h9A1.5 1.5 0 0 1 21 10v8.5A1.5 1.5 0 0 1 19.5 20h-15A1.5 1.5 0 0 1 3 18.5Z"
            : "M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.5h9A1.5 1.5 0 0 1 21 10v8.5A1.5 1.5 0 0 1 19.5 20h-15A1.5 1.5 0 0 1 3 18.5Z"
        }
      />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-[16px] shrink-0 text-ink-tertiary"
    >
      <path d="M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6" />
    </svg>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`size-3 shrink-0 text-ink-tertiary transition-transform ${open ? "rotate-90" : ""}`}
    >
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

function TreeRow({
  node,
  depth,
  selectedSourceId,
  onSelectSource,
  expanded,
  onToggleExpand,
}: {
  node: TreeNode;
  depth: number;
  selectedSourceId: string;
  onSelectSource: (id: string) => void;
  expanded: Set<string>;
  onToggleExpand: (id: string) => void;
}) {
  const isExpanded = expanded.has(node.subject.id);
  const hasContents = node.children.length > 0 || node.sources.length > 0;

  return (
    <li>
      <div
        className={`flex items-center gap-1 rounded-md ${
          hasContents ? "cursor-pointer hover:bg-topbar-hover" : ""
        }`}
        style={{ paddingLeft: `${depth * 14 + 6}px` }}
        onClick={() => hasContents && onToggleExpand(node.subject.id)}
        role={hasContents ? "button" : undefined}
      >
        {hasContents ? <Chevron open={isExpanded} /> : <span className="w-3" aria-hidden />}
        <FolderIcon open={isExpanded} />
        <span className="truncate py-1 pr-2 text-sm text-ink-secondary">
          {node.subject.external_id}
        </span>
        <span className="ml-auto pr-2 text-xs text-ink-tertiary tabular-nums">
          {node.children.length + node.sources.length > 0
            ? node.children.length + node.sources.length
            : ""}
        </span>
      </div>

      {isExpanded && (
        <ul>
          {node.sources.map((src) => {
            const active = src.id === selectedSourceId;
            return (
              <li key={src.id}>
                <button
                  type="button"
                  onClick={() => onSelectSource(src.id)}
                  className={`flex w-full items-center gap-1.5 rounded-md py-1 pr-2 text-left ${
                    active
                      ? "bg-accent-soft text-accent font-medium"
                      : "hover:bg-topbar-hover text-ink-secondary"
                  }`}
                  style={{ paddingLeft: `${depth * 14 + 24}px` }}
                >
                  <FileIcon />
                  <span className="truncate text-sm">
                    {src.filename ?? src.storage_uri ?? src.id.split("-")[0]}
                  </span>
                </button>
              </li>
            );
          })}
          {node.children.map((child) => (
            <TreeRow
              key={child.subject.id}
              node={child}
              depth={depth + 1}
              selectedSourceId={selectedSourceId}
              onSelectSource={onSelectSource}
              expanded={expanded}
              onToggleExpand={onToggleExpand}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

/* ------------------------------------------------------------------ page */

export default function ChunksPage() {
  const subjects = useResource<Subject[]>(() => api.subjects.list(), []);
  const sources = useResource<Source[]>(() => api.sources.list(), []);

  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showInactive, setShowInactive] = useState(false);
  const [rechunking, setRechunking] = useState(false);
  const [rechunkError, setRechunkError] = useState<string>();
  const [rechunkMessage, setRechunkMessage] = useState<string>();

  const chunks = useResource<RetrievalChunk[] | null>(
    () =>
      selectedSourceId
        ? api.chunks.list(selectedSourceId, !showInactive)
        : Promise.resolve(null),
    [selectedSourceId, showInactive],
  );

  const selectedSource = useMemo(
    () => sources.data?.find((s) => s.id === selectedSourceId),
    [sources.data, selectedSourceId],
  );

  // Build the folder tree once both lists arrive.
  const tree = useMemo(
    () => buildTree(subjects.data ?? [], sources.data ?? []),
    [subjects.data, sources.data],
  );

  // Auto-expand everything on first load so files are visible immediately.
  useEffect(() => {
    if (subjects.data && expanded.size === 0 && subjects.data.length > 0) {
      setExpanded(new Set(subjects.data.map((s) => s.id)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjects.data]);

  const treeLoading = subjects.loading || sources.loading;
  const treeError = subjects.error || sources.error;

  function toggleExpand(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function rechunk() {
    if (!selectedSourceId) return;
    setRechunking(true);
    setRechunkError(undefined);
    setRechunkMessage(undefined);
    try {
      const result = await api.chunks.rechunk(selectedSourceId);
      setRechunkMessage(
        `Re-chunked from extraction v${result.version}: ${result.chunk_count} chunks.`,
      );
      chunks.reload();
    } catch (err) {
      setRechunkError(
        err instanceof Error ? err.message : "Re-chunking failed",
      );
    } finally {
      setRechunking(false);
    }
  }

  const chunkList = chunks.data;
  const totalTokens = chunkList?.reduce((sum, c) => sum + c.token_count, 0) ?? 0;

  return (
    <>
      <PageHeader
        title="Chunks"
        description="Every retrieval chunk Memora produced for each source, grouped by the subject folders they live in. Select a file to inspect its chunks: section path, token count, page range and content."
      />

      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        {/* ------------------------------------------------- folder tree */}
        <Panel title="Files" description="Folders and sources.">
          {treeError && (
            <div className="px-4 pt-3">
              <ErrorState message={treeError} onRetry={() => { subjects.reload(); sources.reload(); }} />
            </div>
          )}
          {treeLoading && (
            <div className="px-4 py-8 text-sm text-ink-secondary">
              Loading…
            </div>
          )}
          {!treeLoading && !treeError && tree.length === 0 && (
            <div className="px-4 py-8 text-sm text-ink-secondary">
              No subjects yet.
            </div>
          )}
          <ul className="py-2">
            {tree.map((node) => (
              <TreeRow
                key={node.subject.id}
                node={node}
                depth={0}
                selectedSourceId={selectedSourceId}
                onSelectSource={setSelectedSourceId}
                expanded={expanded}
                onToggleExpand={toggleExpand}
              />
            ))}
          </ul>
        </Panel>

        {/* ------------------------------------------------ chunk listing */}
        <div className="min-w-0">
          {!selectedSourceId && (
            <Panel title="Chunks" description="Select a file from the tree to view its chunks.">
              <div className="px-4 py-12 text-center text-sm text-ink-secondary">
                No file selected.
              </div>
            </Panel>
          )}

          {selectedSourceId && (
            <Panel
              title={
                selectedSource
                  ? selectedSource.filename ?? selectedSource.id.split("-")[0]
                  : "Chunks"
              }
              counter={chunkList?.length}
              description={
                selectedSource
                  ? `Subject: ${selectedSource.subject_external_id ?? "–"} · Source status: ${selectedSource.status}`
                  : undefined
              }
              actions={
                <div className="flex items-center gap-2">
                  <label className="flex cursor-pointer items-center gap-1.5 text-sm text-ink-secondary">
                    <input
                      type="checkbox"
                      checked={showInactive}
                      onChange={(e) => setShowInactive(e.target.checked)}
                      className="size-3.5"
                    />
                    Show old versions
                  </label>
                  <Button onClick={rechunk} disabled={rechunking}>
                    {rechunking ? "Re-chunking…" : "Re-chunk"}
                  </Button>
                </div>
              }
            >
              {rechunkError && (
                <div className="px-4 pt-3">
                  <ErrorState message={rechunkError} />
                </div>
              )}
              {rechunkMessage && (
                <div className="px-4 pt-3 text-sm text-accent">{rechunkMessage}</div>
              )}

              {chunks.loading && (
                <div className="px-4 py-12 text-center text-sm text-ink-secondary">
                  Loading chunks…
                </div>
              )}
              {chunks.error && (
                <div className="px-4 pt-3">
                  <ErrorState message={chunks.error} onRetry={chunks.reload} />
                </div>
              )}

              {chunkList && chunkList.length === 0 && (
                <div className="px-4 py-12 text-center text-sm text-ink-secondary">
                  No chunks for this source yet. Extract and re-chunk it first.
                </div>
              )}

              {chunkList && chunkList.length > 0 && (
                <div className="px-4 pb-2 pt-1 text-xs text-ink-tertiary">
                  {chunkList.length} chunks · {totalTokens.toLocaleString()} tokens total
                  {chunkList[0] && ` · source version v${chunkList[0].source_version}`}
                </div>
              )}

              <div className="divide-y divide-line-soft">
                {chunkList?.map((chunk) => (
                  <ChunkCard key={chunk.id} chunk={chunk} />
                ))}
              </div>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}

/* ------------------------------------------------------------- chunk card */

const TYPE_COLORS: Record<string, string> = {
  text: "bg-surface-strong text-ink-secondary",
  table: "bg-accent-soft text-accent",
  figure: "bg-amber-100 text-amber-800",
  mixed: "bg-surface-strong text-ink-secondary",
  key_value: "bg-emerald-100 text-emerald-800",
};

function ChunkCard({ chunk }: { chunk: RetrievalChunk }) {
  const [showEmbedding, setShowEmbedding] = useState(false);
  const [showVector, setShowVector] = useState(false);
  const [vectorInfo, setVectorInfo] = useState<ChunkEmbeddingDebug[] | null>(null);
  const [vectorError, setVectorError] = useState<string>();

  // The embedding debug data is fetched on first expand, not on render: a
  // source can have hundreds of chunks and the vector head is only
  // interesting when someone asks for it.
  async function toggleVector() {
    const next = !showVector;
    setShowVector(next);
    if (next && vectorInfo === null && !vectorError) {
      try {
        setVectorInfo(await api.embeddings.chunkDebug(chunk.id));
      } catch (err) {
        setVectorError(err instanceof Error ? err.message : "Failed to load embeddings");
      }
    }
  }

  return (
    <div className="px-4 py-3">
      {/* metadata row */}
      <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span className="font-mono text-ink-tertiary">#{chunk.chunk_index}</span>
        <span
          className={`rounded px-1.5 py-0.5 font-medium ${
            TYPE_COLORS[chunk.content_type] ?? "bg-surface-strong text-ink-secondary"
          }`}
        >
          {chunk.content_type}
        </span>
        <span className="text-ink-secondary">
          {chunk.token_count.toLocaleString()} tokens
        </span>
        <span className="text-ink-tertiary">
          pages {chunk.page_start ?? "?"}–{chunk.page_end ?? "?"}
        </span>
        {!chunk.is_active && (
          <span className="rounded bg-surface-strong px-1.5 py-0.5 text-ink-tertiary">
            v{chunk.source_version} · inactive
          </span>
        )}
        <span className="ml-auto flex items-center gap-3">
          <button
            type="button"
            onClick={toggleVector}
            className="text-ink-tertiary underline-offset-2 hover:text-ink hover:underline"
          >
            {showVector ? "Hide embedding" : "Embedding"}
          </button>
          <button
            type="button"
            onClick={() => setShowEmbedding((v) => !v)}
            className="text-ink-tertiary underline-offset-2 hover:text-ink hover:underline"
          >
            {showEmbedding ? "Show content" : "Show embedding text"}
          </button>
        </span>
      </div>

      {/* section path */}
      {chunk.section_path.length > 0 && (
        <div className="mb-1.5 text-xs text-ink-tertiary">
          {chunk.section_path.map((part, i) => (
            <span key={i}>
              {i > 0 && <span className="mx-1">›</span>}
              <span className={i === chunk.section_path.length - 1 ? "text-ink-secondary font-medium" : ""}>
                {part}
              </span>
            </span>
          ))}
        </div>
      )}

      {/* content / embedding text */}
      <pre className="max-h-96 overflow-auto rounded-md border border-line-soft bg-surface p-3 text-xs leading-relaxed whitespace-pre-wrap break-words text-ink">
        {showEmbedding ? chunk.embedding_text : chunk.content}
      </pre>

      {/* embedding debug: status, hash, vector head */}
      {showVector && (
        <div className="mt-2 rounded-md border border-line-soft bg-surface p-3 text-xs">
          {vectorError && <p className="text-danger">{vectorError}</p>}
          {!vectorError && vectorInfo === null && (
            <p className="text-ink-secondary">Loading embeddings…</p>
          )}
          {vectorInfo && vectorInfo.length === 0 && (
            <p className="text-ink-secondary">
              No embedding yet. Generate one from the source page or the
              Embeddings dashboard.
            </p>
          )}
          {vectorInfo && vectorInfo.length > 0 && (
            <ul className="space-y-2">
              {vectorInfo.map((e) => (
                <li
                  key={e.id}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1"
                >
                  <StatusBadge status={e.status} />
                  <span className="text-ink-secondary">{e.strategy_name}</span>
                  <span className="text-ink-tertiary">{e.dimension}d</span>
                  <span className="text-ink-tertiary">
                    attempts: {e.attempt_count}
                  </span>
                  <span className="min-w-0 truncate text-ink-tertiary">
                    hash <Mono>{e.input_hash.slice(0, 16)}…</Mono>
                  </span>
                  {e.vector_preview.length > 0 && (
                    <span className="truncate font-mono text-ink-tertiary">
                      [{e.vector_preview.join(", ")}]
                    </span>
                  )}
                  {e.error_message && (
                    <span className="text-danger">{e.error_message}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
