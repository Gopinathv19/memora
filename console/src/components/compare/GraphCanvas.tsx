"use client";

/**
 * The Graph RAG visualization.
 *
 * One Cytoscape canvas, used by the Arena (what Graph RAG found for a
 * question) and the Explorer (browse and expand the graph). What it encodes:
 *
 *  - node size and opacity = activation score (how relevant the technique found it)
 *  - node colour = entity type; a bold ring marks the seeds the traversal started from
 *  - hop playback: with `playbackHop` set, entities further than that many hops
 *    from a seed fade out, so relevance can be watched spreading outward
 *  - `paths`: the chains a path technique returned are drawn in the accent colour
 *  - `lens`: entity ids that the *vector* pipeline's chunks mention, ringed in blue,
 *    so you can see what each method saw (overlap = both rings)
 *  - click selects a node, double-click asks the parent to expand it
 *
 * Colours are read from the console's CSS variables so the canvas follows the
 * light/dark theme; Cytoscape styles cannot reference variables themselves.
 */

import { useEffect, useRef } from "react";
import type { Core, ElementDefinition, StylesheetStyle } from "cytoscape";

import type { TracePath } from "@/lib/graphrag-types";

export interface CanvasNode {
  id: string;
  name: string;
  type: string;
  score?: number;
  hop?: number | null;
  seed?: boolean;
  degree?: number;
}

export interface CanvasEdge {
  source: string;
  target: string;
  relation: string;
  score?: number;
  confidence?: number | null;
}

export const TYPE_COLORS: Record<string, string> = {
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

const edgeId = (e: CanvasEdge) => `${e.source}|${e.relation}|${e.target}`;

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function buildStyle(): StylesheetStyle[] {
  const ink = cssVar("--color-ink", "#171717");
  const inkSoft = cssVar("--color-ink-secondary", "#6f6f6f");
  const edge = cssVar("--color-graph-edge", "#c3c8ce");
  const edgeLit = cssVar("--color-graph-edge-lit", "#4b5563");
  const accent = cssVar("--color-brand", "#3ecf8e");
  const info = cssVar("--color-info", "#2a6fb0");
  const canvas = cssVar("--color-panel", "#ffffff");
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "font-size": 10,
        color: ink,
        "text-valign": "bottom",
        "text-margin-y": 4,
        "text-outline-color": canvas,
        "text-outline-width": 2,
        "min-zoomed-font-size": 7,
        "background-color": "data(color)",
        width: "data(size)",
        height: "data(size)",
        opacity: "data(opacity)",
        "border-width": 1,
        "border-color": canvas,
        "transition-property": "opacity, width, height, border-width, border-color",
        "transition-duration": 300,
      },
    },
    { selector: "node[?seed]", style: { "border-width": 4, "border-color": ink } },
    // an outline, not the border: path and seed styling own the border
    { selector: "node.lens", style: { "outline-width": 4, "outline-color": info, "outline-offset": 2, "outline-opacity": 1 } },
    { selector: "node.dim", style: { opacity: 0.1 } },
    { selector: "node.onpath", style: { "border-width": 4, "border-color": accent, opacity: 1 } },
    { selector: "node:selected", style: { "border-width": 5, "border-color": accent } },
    {
      selector: "edge",
      style: {
        width: "data(width)",
        "line-color": edge,
        "target-arrow-color": edge,
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.7,
        "curve-style": "bezier",
        opacity: 0.8,
        label: "",
        "font-size": 8,
        color: inkSoft,
        "text-rotation": "autorotate",
        "text-background-color": canvas,
        "text-background-opacity": 0.85,
        "text-background-padding": "1px",
        "transition-property": "opacity, line-color, width",
        "transition-duration": 300,
      },
    },
    { selector: "edge.dim", style: { opacity: 0.05 } },
    {
      selector: "edge.onpath",
      style: {
        "line-color": accent,
        "target-arrow-color": accent,
        width: 4,
        opacity: 1,
        label: "data(relation)",
        "z-index": 10,
      },
    },
    { selector: "edge:selected", style: { "line-color": edgeLit, "target-arrow-color": edgeLit, label: "data(relation)" } },
    { selector: "node:selected ~ edge", style: {} },
  ] as StylesheetStyle[];
}

function nodeData(n: CanvasNode, activation: boolean) {
  const score = n.score ?? 0;
  const size = activation ? 16 + 40 * Math.min(1, Math.max(0, score)) : 14 + Math.min(26, (n.degree ?? 0) * 2);
  return {
    id: n.id,
    label: n.name.length > 26 ? `${n.name.slice(0, 25)}…` : n.name,
    full: n.name,
    type: n.type,
    color: TYPE_COLORS[n.type] ?? TYPE_COLORS.CONCEPT,
    size,
    opacity: activation ? 0.35 + 0.65 * Math.min(1, score * 1.2) : 1,
    seed: !!n.seed,
    hop: n.hop ?? -1,
    score,
  };
}

export function GraphCanvas({
  nodes,
  edges,
  activation = false,
  paths = [],
  lens,
  playbackHop = null,
  height = 480,
  onSelect,
  onExpand,
}: {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  /** Size/fade nodes by `score` instead of by degree. */
  activation?: boolean;
  paths?: TracePath[];
  /** Entity ids to ring in blue (the entities the vector pipeline's chunks mention). */
  lens?: Set<string>;
  /** Fade entities more than this many hops out; null shows everything. */
  playbackHop?: number | null;
  height?: number;
  onSelect?: (id: string | null) => void;
  onExpand?: (id: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const handlers = useRef({ onSelect, onExpand });
  handlers.current = { onSelect, onExpand };
  const firstLayout = useRef(true);

  // create once, client only
  useEffect(() => {
    let disposed = false;
    let observer: MutationObserver | undefined;
    (async () => {
      const [{ default: cytoscape }, { default: fcose }] = await Promise.all([
        import("cytoscape"),
        import("cytoscape-fcose"),
      ]);
      if (disposed || !host.current) return;
      try {
        cytoscape.use(fcose);
      } catch {
        /* already registered */
      }
      const cy = cytoscape({
        container: host.current,
        style: buildStyle(),
        elements: [],
        wheelSensitivity: 0.25,
        minZoom: 0.15,
        maxZoom: 3,
      });
      cy.on("tap", "node", (evt) => handlers.current.onSelect?.(evt.target.id()));
      cy.on("tap", (evt) => {
        if (evt.target === cy) handlers.current.onSelect?.(null);
      });
      cy.on("dbltap", "node", (evt) => handlers.current.onExpand?.(evt.target.id()));
      cy.on("mouseover", "node", (evt) => {
        const n = evt.target;
        n.connectedEdges().style({ label: "data(relation)" });
        host.current && (host.current.style.cursor = "pointer");
      });
      cy.on("mouseout", "node", (evt) => {
        evt.target.connectedEdges().not(".onpath").style({ label: "" });
        host.current && (host.current.style.cursor = "default");
      });
      cyRef.current = cy;
      observer = new MutationObserver(() => cy.style(buildStyle()));
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      cy.emit("memora-ready");
    })();
    return () => {
      disposed = true;
      observer?.disconnect();
      cyRef.current?.destroy();
      cyRef.current = null;
    };
  }, []);

  // sync elements, then lay out
  useEffect(() => {
    let cancelled = false;
    const apply = () => {
      const cy = cyRef.current;
      if (!cy || cancelled) return;
      const ids = new Set(nodes.map((n) => n.id));
      const wantEdges = edges.filter((e) => ids.has(e.source) && ids.has(e.target));
      const edgeIds = new Set(wantEdges.map(edgeId));

      cy.batch(() => {
        cy.nodes().forEach((n) => {
          if (!ids.has(n.id())) n.remove();
        });
        cy.edges().forEach((e) => {
          if (!edgeIds.has(e.id())) e.remove();
        });
        const add: ElementDefinition[] = [];
        for (const n of nodes) {
          const existing = cy.getElementById(n.id);
          if (existing.nonempty()) existing.data(nodeData(n, activation));
          else add.push({ group: "nodes", data: nodeData(n, activation) });
        }
        for (const e of wantEdges) {
          const id = edgeId(e);
          const width = 1 + 3 * Math.min(1, Math.max(0, e.score ?? e.confidence ?? 0.4));
          const existing = cy.getElementById(id);
          if (existing.nonempty()) existing.data({ width });
          else
            add.push({
              group: "edges",
              data: { id, source: e.source, target: e.target, relation: e.relation.replace(/_/g, " ").toLowerCase(), width },
            });
        }
        if (add.length) cy.add(add);
      });
      const added = cy.elements().length;
      if (!added) return;
      cy.layout({
        name: "fcose",
        animate: true,
        animationDuration: 600,
        randomize: firstLayout.current,
        fit: firstLayout.current,
        padding: 30,
        nodeRepulsion: () => 9000,
        idealEdgeLength: () => 90,
        quality: "default",
      } as never).run();
      firstLayout.current = false;
    };
    if (cyRef.current) apply();
    else {
      const t = setInterval(() => {
        if (cyRef.current) {
          clearInterval(t);
          apply();
        }
      }, 50);
      return () => {
        cancelled = true;
        clearInterval(t);
      };
    }
    return () => {
      cancelled = true;
    };
  }, [nodes, edges, activation]);

  // overlays: lens, paths, playback
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("lens onpath dim");
      if (lens) lens.forEach((id) => cy.getElementById(id).addClass("lens"));
      for (const path of paths) {
        for (const step of path.steps) {
          cy.getElementById(step.from_id).addClass("onpath");
          cy.getElementById(step.to_id).addClass("onpath");
          cy.edges()
            .filter(
              (e) =>
                (e.source().id() === step.from_id && e.target().id() === step.to_id) ||
                (e.source().id() === step.to_id && e.target().id() === step.from_id),
            )
            .addClass("onpath");
        }
      }
      if (playbackHop !== null) {
        cy.nodes().forEach((n) => {
          const hop = n.data("hop") as number;
          if (hop < 0 || hop > playbackHop) n.addClass("dim");
        });
        cy.edges().forEach((e) => {
          if (e.source().hasClass("dim") || e.target().hasClass("dim")) e.addClass("dim");
        });
      }
    });
  }, [lens, paths, playbackHop, nodes, edges]);

  return (
    <div
      ref={host}
      style={{ height }}
      className="w-full rounded-lg border border-line bg-panel"
      role="img"
      aria-label="Knowledge graph visualization"
    />
  );
}
