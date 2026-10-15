/**
 * The Advisor: which pipeline should this workload use?
 *
 * Deterministic and computed only from a benchmark's measured numbers, never
 * from a model's opinion. Each pipeline gets four scores in [0, 1]:
 *
 *  - accuracy:  measured correctness (or Recall@5 when no judge ran), averaged
 *               over the user's workload mix of question kinds
 *  - speed:     fastest mean latency = 1, slowest = 0
 *  - economy:   cheapest cost per question = 1, dearest = 0
 *  - explainability: a property of the pipeline family, NOT a measurement
 *               (vector cites chunks; hybrid adds facts; graph adds paths too)
 *
 * and the total is their average weighted by the user's priorities.
 */

import type { BenchmarkRun, PipelineAggregate } from "./graphrag-types";

export type Priorities = {
  accuracy: number;
  speed: number;
  economy: number;
  explainability: number;
};

/** Share of questions of each kind; need not sum to 1. */
export type Mix = { lookup: number; relational: number; broad: number };

export const QUERY_TYPE_GROUP: Record<string, keyof Mix> = {
  factoid: "lookup",
  exact_lookup: "lookup",
  table: "lookup",
  conversational: "lookup",
  image: "lookup",
  multi_hop: "relational",
  multi_document: "relational",
  comparison: "relational",
  temporal: "relational",
  unanswerable: "lookup",
};
// Questions asking for themes or summaries have no dedicated type in the eval
// schema; "broad" is filled from whatever the user tags as such.
export const BROAD_TYPES = ["broad", "summary", "global"];

export const EXPLAINABILITY: Record<string, number> = {
  vector: 0.3,
  hybrid: 0.7,
  graph: 1.0,
};

export interface PipelineScore {
  key: string;
  label: string;
  total: number;
  accuracy: number | null;
  speed: number;
  economy: number;
  explainability: number;
}

export interface TypeVerdict {
  queryType: string;
  n: number;
  winner: string | null;
  runnerUp: string | null;
  margin: number;
  /** "clear" when the gap is large enough for the sample size, else "inconclusive". */
  confidence: "clear" | "inconclusive";
  metric: "correctness" | "recall@5";
}

export interface Advice {
  ranking: PipelineScore[];
  winner: PipelineScore | null;
  perType: TypeVerdict[];
  reasons: string[];
}

export function pipelineLabel(key: string): string {
  if (key === "vector") return "Vector RAG";
  const [family, technique] = key.split(":");
  const name = technique ? ` (${technique.replace("_", "-")})` : "";
  return family === "hybrid" ? `Hybrid${name}` : `Graph RAG${name}`;
}

function family(key: string): string {
  return key.split(":")[0];
}

function groupOf(queryType: string): keyof Mix {
  if (BROAD_TYPES.includes(queryType)) return "broad";
  return QUERY_TYPE_GROUP[queryType] ?? "lookup";
}

function normalized(values: number[], invert: boolean): (v: number) => number {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  if (!isFinite(lo) || hi === lo) return () => 1;
  return (v) => (invert ? (hi - v) / (hi - lo) : (v - lo) / (hi - lo));
}

function quality(
  run: BenchmarkRun,
  key: string,
  type: string,
): { value: number | null; metric: "correctness" | "recall@5" } {
  const t = run.by_type[key]?.[type];
  if (t?.correctness != null) return { value: t.correctness, metric: "correctness" };
  return { value: t?.recall_at_5 ?? null, metric: "recall@5" };
}

export function advise(run: BenchmarkRun, priorities: Priorities, mix: Mix): Advice {
  const keys = Object.keys(run.aggregates);
  const agg = run.aggregates as Record<string, PipelineAggregate>;
  const types = Array.from(
    new Set(keys.flatMap((k) => Object.keys(run.by_type[k] ?? {}))),
  );

  // accuracy per pipeline: the mix-weighted mean of its per-kind quality
  const accuracy: Record<string, number | null> = {};
  for (const key of keys) {
    let sum = 0;
    let weight = 0;
    for (const group of ["lookup", "relational", "broad"] as const) {
      const kinds = types.filter((t) => groupOf(t) === group);
      const values: number[] = [];
      const counts: number[] = [];
      for (const kind of kinds) {
        const q = quality(run, key, kind);
        if (q.value != null) {
          values.push(q.value);
          counts.push(run.by_type[key][kind].n);
        }
      }
      if (!values.length || mix[group] <= 0) continue;
      const n = counts.reduce((a, b) => a + b, 0);
      const mean = values.reduce((acc, v, i) => acc + v * counts[i], 0) / n;
      sum += mean * mix[group];
      weight += mix[group];
    }
    accuracy[key] =
      weight > 0
        ? sum / weight
        : (agg[key].correctness ?? agg[key].recall_at_5 ?? null);
  }

  const latencies = keys.map((k) => agg[k].latency_mean_ms ?? 0);
  const costs = keys.map((k) => agg[k].cost_usd_per_question ?? 0);
  const speedOf = normalized(latencies, true);
  const economyOf = normalized(costs, true);

  const totalWeight =
    priorities.accuracy + priorities.speed + priorities.economy + priorities.explainability;
  const ranking: PipelineScore[] = keys
    .map((key) => {
      const score = {
        key,
        label: pipelineLabel(key),
        accuracy: accuracy[key],
        speed: speedOf(agg[key].latency_mean_ms ?? 0),
        economy: economyOf(agg[key].cost_usd_per_question ?? 0),
        explainability: EXPLAINABILITY[family(key)] ?? 0.3,
        total: 0,
      };
      score.total =
        totalWeight > 0
          ? ((score.accuracy ?? 0) * priorities.accuracy +
              score.speed * priorities.speed +
              score.economy * priorities.economy +
              score.explainability * priorities.explainability) /
            totalWeight
          : 0;
      return score;
    })
    .sort((a, b) => b.total - a.total);

  // Per question kind: vector against the best graph-based pipeline. Comparing
  // the top two overall would often pit two graph variants against each other
  // and hide the question that matters (is the graph worth it for this kind of
  // question?), so the lead is the gap between the best of each family.
  const perType: TypeVerdict[] = types.map((queryType) => {
    const rows = keys
      .map((key) => ({ key, ...quality(run, key, queryType) }))
      .filter((r): r is { key: string; value: number; metric: "correctness" | "recall@5" } => r.value != null)
      .sort((a, b) => b.value - a.value);
    const n = Math.max(...keys.map((k) => run.by_type[k]?.[queryType]?.n ?? 0), 0);
    const vector = rows.find((r) => r.key === "vector");
    const bestOther = rows.find((r) => r.key !== "vector");
    let winner: string | null = rows[0]?.key ?? null;
    let runnerUp: string | null = rows[1]?.key ?? null;
    let margin = rows.length > 1 ? rows[0].value - rows[1].value : 0;
    if (vector && bestOther) {
      const diff = vector.value - bestOther.value;
      margin = Math.abs(diff);
      winner = margin < 1e-9 ? null : diff > 0 ? "vector" : bestOther.key;
      runnerUp = diff > 0 ? bestOther.key : "vector";
    }
    // With few questions a gap smaller than ~0.15 is within noise (one question
    // flipping moves the mean by 1/n).
    const threshold = Math.max(0.1, 1.5 / Math.max(n, 1));
    return {
      queryType,
      n,
      winner,
      runnerUp,
      margin: Math.round(margin * 1000) / 1000,
      confidence: winner !== null && margin >= threshold && n >= 3 ? "clear" : "inconclusive",
      metric: rows[0]?.metric ?? "recall@5",
    };
  });

  const winner = ranking[0] ?? null;
  const reasons: string[] = [];
  const vector = ranking.find((r) => r.key === "vector");
  if (winner && vector && winner.key !== "vector") {
    const w = agg[winner.key];
    const v = agg.vector;
    if (w.latency_mean_ms && v.latency_mean_ms) {
      reasons.push(
        `${winner.label} takes ${(w.latency_mean_ms / v.latency_mean_ms).toFixed(1)}x the latency of Vector RAG (${Math.round(w.latency_mean_ms)} ms vs ${Math.round(v.latency_mean_ms)} ms mean).`,
      );
    }
    const wa = winner.accuracy;
    const va = vector.accuracy;
    if (wa != null && va != null) {
      reasons.push(
        `On your workload mix it scores ${(wa * 100).toFixed(0)}% against ${(va * 100).toFixed(0)}% for Vector RAG.`,
      );
    }
  } else if (winner?.key === "vector") {
    reasons.push("Vector RAG is the best fit for these priorities: it is the fastest and cheapest, and no graph pipeline beat it enough to justify its cost.");
  }
  const clearWins = perType.filter((t) => t.confidence === "clear" && t.winner && t.winner !== "vector");
  if (clearWins.length) {
    reasons.push(
      `Graph-based retrieval wins clearly on: ${clearWins.map((t) => t.queryType.replace("_", " ")).join(", ")}.`,
    );
  }
  const inconclusive = perType.filter((t) => t.confidence === "inconclusive");
  if (inconclusive.length) {
    reasons.push(
      `Too close to call (or too few questions) for: ${inconclusive.map((t) => t.queryType.replace("_", " ")).join(", ")}.`,
    );
  }
  return { ranking, winner, perType, reasons };
}
