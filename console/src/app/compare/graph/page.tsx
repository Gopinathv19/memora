"use client";

/**
 * Graph explorer: browse a subject's knowledge graph and grow it by hand.
 *
 * The most-mentioned entities and the facts between them are loaded first
 * (size = how many text passages mention an entity); double-click an entity to
 * pull in its neighbours through `/graph-rag/expand`. Filter by entity type,
 * search by name, and select an entity to read its relationships.
 */

import { useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import { graphrag } from "@/lib/graphrag-api";
import type { GraphResult } from "@/lib/types";
import { GraphCanvas, TYPE_COLORS, type CanvasEdge, type CanvasNode } from "@/components/compare/GraphCanvas";
import { IndexBanner, SubjectSelect, useCompareSubject } from "@/components/compare/shared";
import { Button, EmptyState, ErrorState, PageHeader, Panel } from "@/components/ui";

export default function ExplorerPage() {
  const { subjects, subjectId, setSubjectId } = useCompareSubject();
  const [graph, setGraph] = useState<GraphResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [extra, setExtra] = useState<{ nodes: CanvasNode[]; edges: CanvasEdge[] }>({ nodes: [], edges: [] });
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (!subjectId) return;
    let stop = false;
    setLoading(true);
    setError(undefined);
    setExtra({ nodes: [], edges: [] });
    setSelected(null);
    api.graph
      .view(subjectId)
      .then((g) => !stop && setGraph(g))
      .catch((e) => !stop && setError(e instanceof Error ? e.message : "Could not load the graph"))
      .finally(() => !stop && setLoading(false));
    return () => { stop = true; };
  }, [subjectId]);

  const allNodes: CanvasNode[] = useMemo(() => {
    const base = new Map<string, CanvasNode>(
      (graph?.entities ?? []).map((e) => [e.entity_id, { id: e.entity_id, name: e.name, type: e.entity_type, degree: e.mention_count }]),
    );
    for (const n of extra.nodes) if (!base.has(n.id)) base.set(n.id, n);
    return Array.from(base.values());
  }, [graph, extra]);
  const allEdges: CanvasEdge[] = useMemo(
    () => [
      ...(graph?.relationships ?? []).map((r) => ({ source: r.source_entity_id, target: r.target_entity_id, relation: r.relation, confidence: r.confidence })),
      ...extra.edges,
    ],
    [graph, extra],
  );

  const types = useMemo(() => Array.from(new Set(allNodes.map((n) => n.type))).sort(), [allNodes]);
  const needle = search.trim().toLowerCase();
  const nodes = useMemo(
    () => allNodes.filter((n) => !hidden.has(n.type) && (!needle || n.name.toLowerCase().includes(needle))),
    [allNodes, hidden, needle],
  );
  const edges = allEdges;

  async function expand(id: string) {
    try {
      const out = await graphrag.expand(subjectId, [id], 40);
      setExtra((cur) => ({
        nodes: [...cur.nodes, ...out.nodes.filter((n) => !cur.nodes.some((m) => m.id === n.id)).map((n) => ({ id: n.id, name: n.name, type: n.type, degree: n.degree }))],
        edges: [...cur.edges, ...out.edges.map((e) => ({ source: e.source, target: e.target, relation: e.relation, confidence: e.confidence }))],
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not expand this entity");
    }
  }

  const sel = allNodes.find((n) => n.id === selected);
  const rels = selected ? allEdges.filter((e) => e.source === selected || e.target === selected) : [];
  const nameOf = (id: string) => allNodes.find((n) => n.id === id)?.name ?? id;
  const empty = !loading && graph && graph.entities.length === 0;

  return (
    <>
      <PageHeader
        eyebrow="Graph RAG"
        title="Graph explorer"
        description="Browse the knowledge graph Graph RAG retrieves from. Double-click an entity to bring in what it is connected to."
      />
      <IndexBanner subjectId={subjectId} />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      <Panel
        actions={
          <>
            <SubjectSelect subjects={subjects} value={subjectId} onChange={setSubjectId} />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Find an entity…" aria-label="Find an entity" className="h-[34px] w-44 rounded-md border border-line bg-panel px-2 text-sm text-ink placeholder:text-ink-tertiary" />
          </>
        }
      >
        {loading && <p className="p-4 text-sm text-ink-secondary">Loading the graph…</p>}
        {empty && (
          <div className="p-4"><EmptyState title="This subject has no knowledge graph yet" description="Build the graph for its sources from the Sources page, then come back." /></div>
        )}
        {!empty && graph && (
          <div className="grid gap-4 p-4 lg:grid-cols-[1fr_280px]">
            <div>
              <GraphCanvas nodes={nodes} edges={edges} onSelect={setSelected} onExpand={expand} height={560} />
              <div className="mt-2 flex flex-wrap gap-2 text-xs">
                {types.map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setHidden((h) => { const n = new Set(h); n.has(t) ? n.delete(t) : n.add(t); return n; })}
                    aria-pressed={!hidden.has(t)}
                    className={`flex items-center gap-1.5 rounded-md border px-2 py-0.5 ${hidden.has(t) ? "border-line text-ink-tertiary line-through" : "border-line text-ink-secondary"}`}
                  >
                    <span className="size-2 rounded-full" style={{ background: TYPE_COLORS[t] ?? TYPE_COLORS.CONCEPT }} />
                    {t.toLowerCase()}
                  </button>
                ))}
                <span className="ml-auto text-ink-tertiary">{nodes.length} entities · {edges.length} relationships</span>
              </div>
            </div>
            <aside className="text-sm" aria-live="polite">
              {sel ? (
                <div className="space-y-2">
                  <div className="font-medium text-ink">{sel.name}</div>
                  <div className="text-xs text-ink-tertiary">{sel.type.toLowerCase()}{sel.degree ? ` · ${sel.degree} mention${sel.degree === 1 ? "" : "s"}` : ""}</div>
                  <ul className="space-y-1 text-xs text-ink-secondary">
                    {rels.slice(0, 20).map((e, i) => (
                      <li key={i}>{nameOf(e.source)} <span className="text-accent">{e.relation.replace(/_/g, " ").toLowerCase()}</span> {nameOf(e.target)}</li>
                    ))}
                    {rels.length === 0 && <li className="text-ink-tertiary">No relationships loaded for this entity.</li>}
                  </ul>
                  <Button onClick={() => expand(sel.id)}>Show neighbours</Button>
                </div>
              ) : (
                <p className="text-xs text-ink-tertiary">Select an entity to see how it connects. Larger circles are mentioned in more passages.</p>
              )}
            </aside>
          </div>
        )}
      </Panel>
    </>
  );
}
