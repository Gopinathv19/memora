"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { Source, Subject } from "@/lib/types";
import { StatusBadge } from "@/components/ui";

/**
 * Lazy-loading tree of subjects for one application.
 * - Roots are fetched upfront (rootsOnly: true)
 * - Children are fetched on first expand per node
 */
export function SubjectTree({ applicationId }: { applicationId: string }) {
  const [roots, setRoots] = useState<Subject[] | null>(null);
  const [loadingRoots, setLoadingRoots] = useState(true);
  const [errorRoots, setErrorRoots] = useState<string | null>(null);

  // Per-node children cache and expansion state
  const [children, setChildren] = useState<Record<string, Subject[] | undefined>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [loadingChildren, setLoadingChildren] = useState<Record<string, boolean>>({});
  const [errorChildren, setErrorChildren] = useState<Record<string, string | undefined>>({});

  // Per-node sources (files) cache, loaded on first expand alongside children
  const [sources, setSources] = useState<Record<string, Source[] | undefined>>({});

  // Initial load: root subjects
  useEffect(() => {
    let cancelled = false;
    setLoadingRoots(true);
    setErrorRoots(null);
    api.subjects
      .list({ applicationId, rootsOnly: true })
      .then((list) => {
        if (!cancelled) setRoots(list);
      })
      .catch((err: any) => {
        if (!cancelled) setErrorRoots(err?.message ?? "Failed to load subjects");
      })
      .finally(() => {
        if (!cancelled) setLoadingRoots(false);
      });
    return () => {
      cancelled = true;
    };
  }, [applicationId]);

  async function toggle(node: Subject) {
    const id = node.id;
    const next = !expanded[id];
    setExpanded((e) => ({ ...e, [id]: next }));

    // Decide what still needs loading for this node. A folder can contain
    // child folders, files, or both — fire the missing requests in parallel.
    const needChildren = next && children[id] === undefined && node.child_count > 0;
    const needSources = next && sources[id] === undefined && node.source_count > 0;

    if (!needChildren && !needSources) return;

    try {
      setLoadingChildren((m) => ({ ...m, [id]: true }));
      setErrorChildren((m) => ({ ...m, [id]: undefined }));

      const tasks: Promise<void>[] = [];
      if (needChildren) {
        tasks.push(
          api.subjects.list({ parentSubjectId: id }).then((list) => {
            setChildren((m) => ({ ...m, [id]: list }));
          }),
        );
      }
      if (needSources) {
        tasks.push(
          api.sources.list({ subjectId: id }).then((list) => {
            setSources((m) => ({ ...m, [id]: list }));
          }),
        );
      }

      await Promise.all(tasks);
    } catch (err: any) {
      setErrorChildren((m) => ({ ...m, [id]: err?.message ?? "Failed to load" }));
    } finally {
      setLoadingChildren((m) => ({ ...m, [id]: false }));
    }
  }

  function FileRow({ source, depth }: { source: Source; depth: number }) {
    const indent = depth * 16; // px
    const label = source.filename || source.type;

    return (
      <div
        className="flex items-center gap-2 px-4 py-1.5 hover:bg-surface"
        style={{ paddingLeft: 16 + indent + 24 /* one extra level */ }}
      >
        {/* Leaf marker — no expand button, just a file icon */}
        <span className="mr-1 inline-block w-5" />

        {/* File icon (simple inline SVG) */}
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="size-[16px] text-ink-secondary"
        >
          <path d="M14 3v4a1 1 0 0 0 1 1h4" />
          <path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2Z" />
        </svg>

        <Link
          href={`/sources/${source.id}`}
          className="min-w-0 truncate font-medium text-ink hover:underline"
          title={label}
        >
          {label}
        </Link>

        <StatusBadge status={source.status} />
      </div>
    );
  }

  function Row({ node, depth }: { node: Subject; depth: number }) {
    const id = node.id;
    const hasChildren = node.child_count > 0;
    const isOpen = !!expanded[id];
    const kids = children[id] ?? [];
    const files = sources[id] ?? [];
    const indent = depth * 16; // px

    return (
      <div>
        <div
          className="flex items-center gap-2 px-4 py-1.5 hover:bg-surface"
          style={{ paddingLeft: 16 + indent }}
        >
          {hasChildren ? (
            <button
              type="button"
              aria-label={isOpen ? "Collapse" : "Expand"}
              onClick={() => toggle(node)}
              className="mr-1 grid size-5 place-items-center rounded text-ink-secondary hover:bg-topbar-hover hover:text-ink"
            >
              <span aria-hidden>{isOpen ? "▾" : "▸"}</span>
            </button>
          ) : (
            <span className="mr-1 inline-block w-5" />
          )}

          {/* Folder/workspace icon (simple inline SVG to avoid emoji) */}
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="size-[16px] text-ink-secondary"
          >
            <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.5h9A1.5 1.5 0 0 1 21 10v8.5A1.5 1.5 0 0 1 19.5 20h-15A1.5 1.5 0 0 1 3 18.5Z" />
          </svg>

          <Link
            href={`/subjects/${id}`}
            className="min-w-0 truncate font-medium text-ink hover:underline"
            title={node.external_id}
          >
            {node.external_id}
          </Link>

          <div className="ml-auto flex items-center gap-3 text-xs text-ink-secondary">
            <span title="Folders">
              📁 <span className="tabular-nums">{node.child_count}</span>
            </span>
            <span title="Sources">
              📄 <span className="tabular-nums">{node.source_count}</span>
            </span>
          </div>
        </div>

        {isOpen && (
          <div>
            {loadingChildren[id] && (
              <div className="px-4 py-2 text-sm text-ink-secondary" style={{ paddingLeft: 48 + indent }}>
                Loading…
              </div>
            )}
            {errorChildren[id] && (
              <div className="px-4 py-2 text-sm text-danger" style={{ paddingLeft: 48 + indent }}>
                {errorChildren[id]}
              </div>
            )}
            {kids.map((child) => (
              <Row key={child.id} node={child} depth={depth + 1} />
            ))}
            {files.map((file) => (
              <FileRow key={`file-${file.id}`} source={file} depth={depth + 1} />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-b-lg">
      {loadingRoots && (
        <div className="px-4 py-14 text-sm text-ink-secondary">Loading…</div>
      )}
      {errorRoots && (
        <div className="m-4 rounded-md border border-danger/30 bg-danger-soft p-4 text-sm text-danger">
          {errorRoots}
        </div>
      )}
      {roots && roots.length === 0 && (
        <div className="px-4 py-14 text-center text-sm">
          <p className="font-medium text-ink">No subjects</p>
          <p className="mx-auto mt-1 max-w-md text-ink-secondary">
            A subject is the workspace that holds sources.
          </p>
        </div>
      )}
      {roots && roots.length > 0 && (
        <div className="divide-y divide-line-soft">
          {roots.map((root) => (
            <Row key={root.id} node={root} depth={0} />
          ))}
        </div>
      )}
    </div>
  );
}
