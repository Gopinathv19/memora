"use client";

import { useMemo, useState } from "react";

import { api } from "@/lib/api";
import type { GraphEntity, GraphRelationship, GraphResult } from "@/lib/types";
import { useMutation, useResource } from "@/lib/useResource";
import {
  Button,
  EmptyState,
  ErrorState,
  InlineError,
  LoadingState,
  Panel,
  TypeTag,
} from "@/components/ui";

/**
 * A subject's knowledge graph: the entities its files mention (and, by
 * default, those of every folder below it) and the relationships between them.
 *
 * Drawn as plain SVG with a small force-directed layout computed once per
 * result -- no charting dependency. Click an entity to see what it connects
 * to; ask a question to highlight the part of the graph that answers it, with
 * the passages the facts came from.
 */

const WIDTH = 1000;
const PAD = 36;
const MIN_HEIGHT = 300;
/** Preferred edge length: long enough for a label on each end. */
const EDGE = 110;
const GROUP_GAP = 56;
const LABEL_FONT = 11;
const LABEL_MAX = 24;

const TYPE_COLORS: Record<string, string> = {
  PERSON: "#2a6fb0",
  COMPANY: "#0a7c42",
  ORGANIZATION: "#2f8f86",
  PRODUCT: "#a0721a",
  LOCATION: "#c33d34",
  TECHNOLOGY: "#6b4fbb",
  PROJECT: "#b0562a",
  DOCUMENT: "#5f6b7a",
  EVENT: "#b0407a",
  CONCEPT: "#8f8f8f",
};

function colorOf(type: string): string {
  return TYPE_COLORS[type] ?? "#8f8f8f";
}

function radiusOf(entity: GraphEntity): number {
  return Math.min(16, 7 + Math.sqrt(Math.max(1, entity.mention_count)) * 2.5);
}

function truncate(text: string, max = LABEL_MAX): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

type Point = { x: number; y: number };

/** The box a node occupies: its circle plus the label under it. */
function boxOf(entity: GraphEntity): { w: number; h: number } {
  const r = radiusOf(entity);
  const label = truncate(entity.name).length * LABEL_FONT * 0.58;
  return { w: Math.max(2 * r, label) + 14, h: 2 * r + LABEL_FONT + 12 };
}

/** Connected groups of node indexes, biggest first. */
function groupsOf(n: number, links: [number, number][]): number[][] {
  const parent = Array.from({ length: n }, (_, i) => i);
  const find = (i: number): number => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  for (const [a, b] of links) parent[find(a)] = find(b);
  const groups = new Map<number, number[]>();
  for (let i = 0; i < n; i++) {
    const root = find(i);
    groups.set(root, [...(groups.get(root) ?? []), i]);
  }
  return [...groups.values()].sort((a, b) => b.length - a.length);
}

/**
 * Lay out one connected group around (0, 0): Fruchterman–Reingold with a
 * fixed edge length, then push apart any two nodes whose boxes (circle +
 * label) overlap. Deterministic: the same graph always looks the same.
 */
function layoutGroup(
  members: number[],
  links: [number, number][],
  boxes: { w: number; h: number }[],
  xs: Float64Array,
  ys: Float64Array,
) {
  const m = members.length;
  if (m === 1) {
    xs[members[0]] = 0;
    ys[members[0]] = 0;
    return;
  }
  const local = new Map(members.map((g, i) => [g, i]));
  const inner = links
    .filter(([a, b]) => local.has(a) && local.has(b))
    .map(([a, b]) => [local.get(a)!, local.get(b)!] as [number, number]);
  const x = new Float64Array(m);
  const y = new Float64Array(m);
  for (let i = 0; i < m; i++) {
    const r = EDGE * 0.5 * Math.sqrt(i + 1);
    x[i] = r * Math.cos(i * 2.39996);
    y[i] = r * Math.sin(i * 2.39996);
  }
  const k = EDGE;
  const iterations = 300;
  const dx = new Float64Array(m);
  const dy = new Float64Array(m);
  for (let it = 0; it < iterations; it++) {
    const temperature = EDGE * (1 - it / iterations) + 0.5;
    dx.fill(0);
    dy.fill(0);
    for (let i = 0; i < m; i++) {
      for (let j = i + 1; j < m; j++) {
        let ex = x[i] - x[j];
        let ey = y[i] - y[j];
        let d2 = ex * ex + ey * ey;
        if (d2 < 0.01) {
          ex = 0.1 * (i - j);
          ey = 0.1;
          d2 = ex * ex + ey * ey;
        }
        const f = (k * k) / d2;
        dx[i] += ex * f;
        dy[i] += ey * f;
        dx[j] -= ex * f;
        dy[j] -= ey * f;
      }
    }
    for (const [a, b] of inner) {
      const ex = x[a] - x[b];
      const ey = y[a] - y[b];
      const d = Math.sqrt(ex * ex + ey * ey) || 0.01;
      const f = d / k;
      dx[a] -= ex * f;
      dy[a] -= ey * f;
      dx[b] += ex * f;
      dy[b] += ey * f;
    }
    for (let i = 0; i < m; i++) {
      dx[i] -= x[i] * 0.03; // a pull to the centre curls chains instead of stringing them out
      dy[i] -= y[i] * 0.03;
      const d = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) || 1;
      const step = Math.min(d, temperature);
      x[i] += (dx[i] / d) * step;
      y[i] += (dy[i] / d) * step;
    }
  }
  // Turn the group so its long side runs horizontally: the canvas is wide.
  let mx = 0, my = 0;
  for (let i = 0; i < m; i++) {
    mx += x[i] / m;
    my += y[i] / m;
  }
  let sxx = 0, syy = 0, sxy = 0;
  for (let i = 0; i < m; i++) {
    sxx += (x[i] - mx) ** 2;
    syy += (y[i] - my) ** 2;
    sxy += (x[i] - mx) * (y[i] - my);
  }
  const angle = 0.5 * Math.atan2(2 * sxy, sxx - syy);
  const cos = Math.cos(-angle);
  const sin = Math.sin(-angle);
  for (let i = 0; i < m; i++) {
    const px = x[i] - mx;
    const py = y[i] - my;
    x[i] = px * cos - py * sin;
    y[i] = px * sin + py * cos;
  }
  members.forEach((g, i) => {
    xs[g] = x[i];
    ys[g] = y[i];
  });
  separate(members, boxes, xs, ys, false);
}

/**
 * Push apart any two nodes whose boxes (circle + label) overlap, along the
 * axis that overlaps least -- or, with `vertical`, always up/down, which is
 * how a group squeezed to the canvas width folds into a zigzag.
 */
function separate(
  members: number[],
  boxes: { w: number; h: number }[],
  xs: Float64Array,
  ys: Float64Array,
  vertical: boolean,
) {
  for (let pass = 0; pass < 120; pass++) {
    let moved = false;
    for (let a = 0; a < members.length; a++) {
      const i = members[a];
      for (let b = a + 1; b < members.length; b++) {
        const j = members[b];
        const ox = (boxes[i].w + boxes[j].w) / 2 - Math.abs(xs[j] - xs[i]);
        const oy = (boxes[i].h + boxes[j].h) / 2 - Math.abs(ys[j] - ys[i]);
        if (ox <= 0 || oy <= 0) continue;
        moved = true;
        if (!vertical && ox < oy) {
          const sx = xs[j] >= xs[i] ? 1 : -1;
          xs[i] -= (sx * ox) / 2;
          xs[j] += (sx * ox) / 2;
        } else {
          // Ties (same height) alternate by index so a row splits both ways.
          const sy = ys[j] > ys[i] || (ys[j] === ys[i] && b % 2 === 0) ? 1 : -1;
          ys[i] -= (sy * oy) / 2;
          ys[j] += (sy * oy) / 2;
        }
      }
    }
    if (!moved) break;
  }
}

function bounds(members: number[], boxes: { w: number; h: number }[], xs: Float64Array, ys: Float64Array) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const i of members) {
    minX = Math.min(minX, xs[i] - boxes[i].w / 2);
    maxX = Math.max(maxX, xs[i] + boxes[i].w / 2);
    minY = Math.min(minY, ys[i] - boxes[i].h / 2);
    maxY = Math.max(maxY, ys[i] + boxes[i].h / 2);
  }
  return { minX, maxX, minY, maxY };
}

/**
 * Every group laid out on its own, then packed left to right in rows --
 * so separate clusters sit side by side instead of being flung into the
 * corners, and the drawing is only as tall as its content.
 */
function layout(
  entities: GraphEntity[],
  edges: GraphRelationship[],
): { positions: Map<string, Point>; height: number } {
  const n = entities.length;
  const positions = new Map<string, Point>();
  if (n === 0) return { positions, height: MIN_HEIGHT };
  const index = new Map(entities.map((e, i) => [e.entity_id, i]));
  const links = edges
    .map((e) => [index.get(e.source_entity_id), index.get(e.target_entity_id)])
    .filter((l): l is [number, number] => l[0] !== undefined && l[1] !== undefined);
  const boxes = entities.map(boxOf);
  const xs = new Float64Array(n);
  const ys = new Float64Array(n);

  const usable = WIDTH - 2 * PAD;
  let cursorX = 0;
  let cursorY = 0;
  let rowHeight = 0;
  const placed: { members: number[]; ox: number; oy: number; scale: number; minX: number; minY: number }[] = [];
  for (const members of groupsOf(n, links)) {
    layoutGroup(members, links, boxes, xs, ys);
    let box = bounds(members, boxes, xs, ys);
    if (box.maxX - box.minX > usable) {
      // Too wide: squeeze it to the canvas width, then let labels that now
      // collide step up or down. Text is never shrunk.
      const mid = (box.minX + box.maxX) / 2;
      const widest = Math.max(...members.map((i) => boxes[i].w));
      const factor = (usable - widest) / (box.maxX - box.minX - widest);
      for (const i of members) xs[i] = mid + (xs[i] - mid) * factor;
      separate(members, boxes, xs, ys, true);
      box = bounds(members, boxes, xs, ys);
    }
    const { minX, maxX, minY, maxY } = box;
    // Last resort (a single label wider than the canvas): shrink to fit.
    const scale = Math.min(1, usable / (maxX - minX));
    const w = (maxX - minX) * scale;
    const h = (maxY - minY) * scale;
    if (cursorX > 0 && cursorX + w > usable) {
      cursorX = 0;
      cursorY += rowHeight + GROUP_GAP;
      rowHeight = 0;
    }
    placed.push({ members, ox: cursorX, oy: cursorY, scale, minX, minY });
    cursorX += w + GROUP_GAP;
    rowHeight = Math.max(rowHeight, h);
  }
  const contentHeight = cursorY + rowHeight;
  const height = Math.max(MIN_HEIGHT, contentHeight + 2 * PAD);
  const topOffset = (height - contentHeight) / 2;
  // Centre each row horizontally.
  const rows = new Map<number, typeof placed>();
  for (const g of placed) rows.set(g.oy, [...(rows.get(g.oy) ?? []), g]);
  for (const row of rows.values()) {
    const last = row[row.length - 1];
    let lastMaxX = -Infinity;
    for (const i of last.members) lastMaxX = Math.max(lastMaxX, xs[i] + boxes[i].w / 2);
    const rowWidth = last.ox + (lastMaxX - last.minX) * last.scale;
    const shift = PAD + (usable - rowWidth) / 2;
    for (const g of row) {
      for (const i of g.members) {
        positions.set(entities[i].entity_id, {
          x: shift + g.ox + (xs[i] - g.minX) * g.scale,
          // Box centre sits below the circle centre (the label hangs under it).
          y: topOffset + g.oy + (ys[i] - g.minY) * g.scale - (boxes[i].h / 2 - radiusOf(entities[i]) - 6) * g.scale,
        });
      }
    }
  }
  return { positions, height };
}

export function SubjectGraph({ subjectId }: { subjectId: string }) {
  const [includeSubfolders, setIncludeSubfolders] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<GraphResult | null>(null);

  const graph = useResource<GraphResult>(
    () => api.graph.view(subjectId, includeSubfolders),
    [subjectId, includeSubfolders],
  );
  const ask = useMutation(api.graph.query);

  const data = graph.data;
  const { positions, height } = useMemo(
    () =>
      data
        ? layout(data.entities, data.relationships)
        : { positions: new Map<string, Point>(), height: MIN_HEIGHT },
    [data],
  );
  const byId = useMemo(
    () => new Map((data?.entities ?? []).map((e) => [e.entity_id, e])),
    [data],
  );
  const types = useMemo(
    () => Array.from(new Set((data?.entities ?? []).map((e) => e.entity_type))).sort(),
    [data],
  );

  // What to emphasise: the selected entity and its neighbours, or the answer.
  const focus = useMemo(() => {
    if (answer) {
      return {
        nodes: new Set(answer.entities.map((e) => e.entity_id)),
        edges: new Set(answer.relationships.map(edgeKey)),
      };
    }
    if (selected && data) {
      const nodes = new Set([selected]);
      const edges = new Set<string>();
      for (const r of data.relationships) {
        if (r.source_entity_id === selected || r.target_entity_id === selected) {
          nodes.add(r.source_entity_id);
          nodes.add(r.target_entity_id);
          edges.add(edgeKey(r));
        }
      }
      return { nodes, edges };
    }
    return null;
  }, [answer, selected, data]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!question.trim()) return;
    setSelected(null);
    const result = await ask.mutate(subjectId, {
      query: question.trim(),
      max_hops: 2,
      include_subfolders: includeSubfolders,
    });
    if (result) setAnswer(result);
  };

  const selectedEntity = selected ? byId.get(selected) : undefined;

  return (
    <Panel
      title="Knowledge graph"
      description="Entities and relationships found in this subject's files. Build a file's graph from its page; every built file of the workspace appears here."
      counter={data?.entities.length}
      actions={
        <>
          <label className="inline-flex h-[34px] items-center gap-2 px-1 text-sm text-ink-secondary">
            <input
              type="checkbox"
              checked={includeSubfolders}
              onChange={(event) => {
                setIncludeSubfolders(event.target.checked);
                setSelected(null);
                setAnswer(null);
              }}
            />
            Include subfolders
          </label>
          <Button onClick={graph.reload} disabled={graph.refreshing}>
            {graph.refreshing ? "Refreshing…" : "Refresh"}
          </Button>
        </>
      }
    >
      <div id="graph" />
      {graph.loading ? (
        <LoadingState label="Loading the graph" />
      ) : graph.error ? (
        <ErrorState message={graph.error} onRetry={graph.reload} />
      ) : !data || data.entities.length === 0 ? (
        <EmptyState
          title="No graph yet"
          description="Open a file in this subject and choose Build graph (or tick “Also build the knowledge graph” when extracting). Its entities and relationships appear here."
        />
      ) : (
        <>
          <form onSubmit={submit} className="flex flex-wrap gap-2 border-b border-line px-4 py-3">
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Ask the graph, e.g. “Who does Dell supply?”"
              maxLength={1000}
              className="h-[34px] min-w-[220px] flex-1 rounded-md border border-line bg-surface px-3 text-sm text-ink placeholder:text-ink-tertiary focus:border-brand focus:bg-panel focus:outline-none focus:ring-2 focus:ring-brand/25"
            />
            <Button type="submit" variant="primary" disabled={ask.pending || !question.trim()}>
              {ask.pending ? "Asking…" : "Ask"}
            </Button>
            {answer && (
              <Button
                onClick={() => {
                  setAnswer(null);
                  setQuestion("");
                }}
              >
                Clear
              </Button>
            )}
          </form>
          {ask.error && (
            <div className="px-4 pt-3">
              <InlineError message={ask.error} />
            </div>
          )}

          <div className="relative bg-surface">
            <div className="max-h-[680px] overflow-y-auto">
            <svg
              viewBox={`0 0 ${WIDTH} ${height}`}
              className="block h-auto w-full select-none"
              role="img"
              aria-label="Knowledge graph of this subject"
              onClick={() => setSelected(null)}
            >
              <defs>
                <marker
                  id="graph-arrow"
                  viewBox="0 0 10 10"
                  refX="10"
                  refY="5"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#9aa1a9" />
                </marker>
              </defs>

              {data.relationships.map((r) => {
                const a = positions.get(r.source_entity_id);
                const b = positions.get(r.target_entity_id);
                const target = byId.get(r.target_entity_id);
                if (!a || !b || !target) return null;
                const key = edgeKey(r);
                const lit = focus?.edges.has(key) ?? false;
                const dim = focus !== null && !lit;
                // Stop the line at the target's rim so the arrow shows.
                const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
                const pull = radiusOf(target) + 2;
                const ex = b.x - ((b.x - a.x) / len) * pull;
                const ey = b.y - ((b.y - a.y) / len) * pull;
                return (
                  <g key={key} opacity={dim ? 0.12 : 1}>
                    <line
                      x1={a.x}
                      y1={a.y}
                      x2={ex}
                      y2={ey}
                      stroke={lit ? "#4b5563" : "#c3c8ce"}
                      strokeWidth={lit ? 1.8 : 1.1}
                      markerEnd="url(#graph-arrow)"
                    >
                      <title>{`${r.source_name} ${r.relation} ${r.target_name}`}</title>
                    </line>
                    {lit && (
                      <text
                        x={(a.x + b.x) / 2}
                        y={(a.y + b.y) / 2 - 4}
                        textAnchor="middle"
                        className="fill-ink-secondary"
                        fontSize={10}
                        style={{ paintOrder: "stroke", stroke: "#f8f9fa", strokeWidth: 3 }}
                      >
                        {r.relation}
                      </text>
                    )}
                  </g>
                );
              })}

              {data.entities.map((e) => {
                const p = positions.get(e.entity_id);
                if (!p) return null;
                const r = radiusOf(e);
                const lit = focus?.nodes.has(e.entity_id) ?? false;
                const dim = focus !== null && !lit;
                return (
                  <g
                    key={e.entity_id}
                    transform={`translate(${p.x},${p.y})`}
                    opacity={dim ? 0.2 : 1}
                    className="cursor-pointer"
                    onClick={(event) => {
                      event.stopPropagation();
                      setAnswer(null);
                      setSelected(e.entity_id === selected ? null : e.entity_id);
                    }}
                  >
                    <circle
                      r={r}
                      fill={colorOf(e.entity_type)}
                      stroke={e.entity_id === selected ? "#171717" : "#ffffff"}
                      strokeWidth={e.entity_id === selected ? 2.5 : 1.5}
                    />
                    <text
                      y={r + 13}
                      textAnchor="middle"
                      fontSize={LABEL_FONT}
                      className="fill-ink"
                      style={{ paintOrder: "stroke", stroke: "#f8f9fa", strokeWidth: 3 }}
                    >
                      {truncate(e.name)}
                    </text>
                    <title>{`${e.name} (${e.entity_type}) — mentioned in ${e.mention_count} passage(s)`}</title>
                  </g>
                );
              })}
            </svg>
            </div>

            <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line px-4 py-2">
              {types.map((type) => (
                <span key={type} className="inline-flex items-center gap-1.5 text-xs text-ink-secondary">
                  <span
                    className="inline-block h-2.5 w-2.5 rounded-full"
                    style={{ background: colorOf(type) }}
                  />
                  {type.toLowerCase()}
                </span>
              ))}
              <span className="ml-auto text-xs text-ink-tertiary">
                {data.entities.length} entities · {data.relationships.length} relationships
              </span>
            </div>
          </div>

          {answer ? (
            <AnswerView answer={answer} />
          ) : selectedEntity ? (
            <EntityView entity={selectedEntity} relationships={data.relationships} />
          ) : (
            <p className="border-t border-line px-4 py-3 text-xs text-ink-tertiary">
              Click an entity to see what it connects to, or ask a question above.
            </p>
          )}
        </>
      )}
    </Panel>
  );
}

function edgeKey(r: GraphRelationship): string {
  return `${r.source_entity_id}|${r.relation}|${r.target_entity_id}`;
}

function EntityView({
  entity,
  relationships,
}: {
  entity: GraphEntity;
  relationships: GraphRelationship[];
}) {
  const touching = relationships.filter(
    (r) => r.source_entity_id === entity.entity_id || r.target_entity_id === entity.entity_id,
  );
  return (
    <div className="border-t border-line px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className="inline-block h-3 w-3 rounded-full"
          style={{ background: colorOf(entity.entity_type) }}
        />
        <h3 className="text-sm font-medium text-ink">{entity.name}</h3>
        <TypeTag value={entity.entity_type} />
        <span className="text-xs text-ink-tertiary">
          mentioned in {entity.mention_count} passage(s) of {entity.source_ids.length} file(s)
        </span>
      </div>
      {entity.description && <p className="mt-1.5 text-sm text-ink-secondary">{entity.description}</p>}
      {entity.aliases.length > 0 && (
        <p className="mt-1 text-xs text-ink-tertiary">Also written as: {entity.aliases.join(", ")}</p>
      )}
      <FactList facts={touching} empty="No relationships to other entities yet." />
    </div>
  );
}

function AnswerView({ answer }: { answer: GraphResult }) {
  if (answer.entities.length === 0) {
    return (
      <p className="border-t border-line px-4 py-3 text-sm text-ink-secondary">
        Nothing in the graph matches that question. Try naming a person, company or product the
        files mention.
      </p>
    );
  }
  return (
    <div className="border-t border-line px-4 py-3">
      <h3 className="text-xs font-medium uppercase tracking-wide text-ink-tertiary">
        Graph evidence
      </h3>
      <FactList facts={answer.relationships} empty="The matching entities have no relationships." />
      {answer.chunks.length > 0 && (
        <>
          <h3 className="mt-4 text-xs font-medium uppercase tracking-wide text-ink-tertiary">
            Source passages ({answer.chunks.length})
          </h3>
          <div className="mt-2 space-y-2">
            {answer.chunks.map((c) => (
              <blockquote
                key={c.chunk_id}
                className="rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink-secondary"
              >
                <div className="mb-1 text-xs text-ink-tertiary">
                  Passage #{c.index}
                  {c.page_start != null &&
                    ` · page ${c.page_start}${c.page_end && c.page_end !== c.page_start ? `–${c.page_end}` : ""}`}
                </div>
                <p className="line-clamp-6 whitespace-pre-wrap">{c.text}</p>
              </blockquote>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function FactList({ facts, empty }: { facts: GraphRelationship[]; empty: string }) {
  if (facts.length === 0) return <p className="mt-2 text-sm text-ink-tertiary">{empty}</p>;
  return (
    <ul className="mt-2 space-y-1.5">
      {facts.map((r) => (
        <li key={edgeKey(r)} className="text-sm">
          <span className="text-ink">{r.source_name}</span>{" "}
          <TypeTag value={r.relation} />{" "}
          <span className="text-ink">{r.target_name}</span>
          {r.description && <span className="text-ink-secondary"> — {r.description}</span>}
        </li>
      ))}
    </ul>
  );
}
