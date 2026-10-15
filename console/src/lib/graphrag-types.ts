/** Types for the Graph RAG service and the Compare dashboard (backend: app/graphrag). */

export type Technique = "spreading" | "ppr" | "dual_level" | "paths" | "community";
export type Pipeline = "vector" | "graph" | "hybrid";

export const TECHNIQUES: {
  id: Technique;
  label: string;
  short: string;
  blurb: string;
  bestFor: string;
}[] = [
  {
    id: "spreading",
    label: "Spreading activation",
    short: "Hop decay",
    blurb:
      "Starts from the entities the question names and lets relevance flow outward, fading with every hop and every low-confidence edge. Several short paths to one entity add up.",
    bestFor: "Relational questions about a named thing",
  },
  {
    id: "ppr",
    label: "Personalized PageRank",
    short: "PPR",
    blurb:
      "A random walk that keeps restarting at the question's entities, over entities and the text that mentions them (HippoRAG style). Evidence reachable many ways rises.",
    bestFor: "Multi-hop questions across documents",
  },
  {
    id: "dual_level",
    label: "Dual-level",
    short: "LightRAG",
    blurb:
      "Splits the question into entity names and themes. Names are matched to entities, themes to relationship facts, so abstract questions find the right relations.",
    bestFor: "Thematic questions about relationships",
  },
  {
    id: "paths",
    label: "Constrained paths",
    short: "Paths",
    blurb:
      "Finds the shortest chains between the entities in the question and returns each link as evidence, so the answer comes with its reasoning path.",
    bestFor: "How is A connected to B?",
  },
  {
    id: "community",
    label: "Communities",
    short: "Communities",
    blurb:
      "Matches the question against summaries of clusters of related entities, built ahead of time. Answers broad questions by synthesis rather than by a few chunks.",
    bestFor: "Broad questions across many documents",
  },
];

export interface GraphParams {
  technique: Technique;
  max_hops?: number;
  decay?: number;
  min_confidence?: number;
  hub_damping?: boolean;
  seed_limit?: number;
  ppr_alpha?: number;
  path_max_len?: number;
}

export interface TraceNode {
  id: string;
  name: string;
  type: string;
  score: number;
  hop: number | null;
  seed: boolean;
}

export interface TraceEdge {
  source: string;
  target: string;
  relation: string;
  score: number;
  confidence: number | null;
  description: string | null;
  chunk_ids: string[];
}

export interface PathStep {
  from: string;
  relation: string;
  to: string;
  from_id: string;
  to_id: string;
}

export interface TracePath {
  from: string;
  to: string;
  length: number;
  score: number;
  steps: PathStep[];
}

export interface TraceCommunity {
  id: string;
  title: string;
  summary: string;
  size: number;
  similarity: number;
  member_ids: string[];
}

export interface LlmCall {
  purpose: string;
  model: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  prompt?: number;
  completion?: number;
  cost_usd: number;
  latency_ms?: number;
}

export interface GraphTrace {
  technique: string;
  anchored?: boolean;
  seed_counts?: Record<string, number>;
  params: Record<string, number | boolean>;
  seeds: { id: string; name: string; type: string; score: number }[];
  nodes: TraceNode[];
  edges: TraceEdge[];
  facts: { text: string; score: number; source_id: string; chunk_ids: string[] }[];
  paths: TracePath[];
  communities: TraceCommunity[];
  notes: string[];
  technique_timings_ms: Record<string, number>;
  technique_llm_calls: LlmCall[];
  bridge: Record<string, number>;
}

export interface VectorTrace {
  stages: Record<string, number | boolean | string | null>;
}

export interface PipelineChunk {
  chunk_id: string;
  source_id: string;
  subject_id: string;
  content: string;
  content_type: string;
  section_path: string[];
  page_start: number | null;
  page_end: number | null;
  token_count: number;
  similarity: number;
  rerank_score: number | null;
  graph_score: number | null;
}

export interface PipelineResult {
  pipeline: Pipeline;
  technique: string | null;
  query: string;
  answer: string;
  chunks: PipelineChunk[];
  timings_ms: Record<string, number>;
  total_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  llm_calls: LlmCall[];
  trace: GraphTrace | VectorTrace | Record<string, never>;
  /** How much to trust what was retrieved. */
  evidence?: {
    /** The reranker's score for the best chunk; null if nothing was retrieved. */
    best_rerank: number | null;
    weak: boolean;
    /** Graph pipelines: did a name in the question match an entity? null for vector. */
    anchored: boolean | null;
  };
  status: "ok" | "error";
  error: string | null;
}

export interface OverlapPair {
  a: string;
  b: string;
  only_a: string[];
  only_b: string[];
  both: string[];
  jaccard: number;
}

export interface CompareResult {
  compare_id: string;
  query: string;
  results: Partial<Record<Pipeline, PipelineResult>>;
  overlap: { pairs: OverlapPair[] };
  /** Graph entities the vector pipeline's chunks cover. */
  lens: { entity_ids: string[] };
}

export interface IndexStatus {
  subject_id: string;
  sources: number;
  coverage: {
    entities: number;
    entities_embedded: number;
    facts: number;
    communities: number;
  };
  ready: boolean;
  /** Files with no knowledge graph: Graph RAG cannot see them. */
  files_without_graph?: { id: string; filename: string | null; extracted: boolean }[];
  build:
    | null
    | {
        status: "running" | "done" | "failed";
        error?: string;
        entities_embedded?: number;
        facts_embedded?: number;
        facts_removed?: number;
        communities?: number;
        embedding_tokens?: number;
        llm_cost_usd?: number;
        timings_ms?: Record<string, number>;
      };
}

export interface RagRun {
  id: string;
  created_at: string;
  compare_id: string | null;
  pipeline: Pipeline;
  technique: string | null;
  query: string;
  status: string;
  total_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  timings_ms: Record<string, number>;
}

export interface ExpandResult {
  nodes: { id: string; name: string; type: string; degree: number }[];
  edges: {
    source: string;
    target: string;
    relation: string;
    confidence: number | null;
    description: string | null;
    chunk_ids: string[];
  }[];
}

export interface BenchmarkQuestion {
  query: string;
  expected_answer?: string | null;
  supporting_chunk_ids?: string[];
  query_type?: string;
}

export interface PipelineAggregate {
  n: number;
  answerable: number;
  questions: number;
  errors: number;
  recall_at_5: number | null;
  recall_at_10: number | null;
  mrr: number | null;
  ndcg_at_10: number | null;
  hit_rate: number | null;
  context_precision: number | null;
  correctness: number | null;
  correctness_ci: [number, number] | null;
  recall_at_5_ci: [number, number] | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  latency_mean_ms: number | null;
  cost_usd_total: number;
  cost_usd_per_question: number | null;
  tokens_total: number;
  vs_vector?: { wins: number; ties: number; losses: number };
}

export interface TypeAggregate {
  n: number;
  correctness: number | null;
  recall_at_5: number | null;
  latency_mean_ms: number | null;
}

export interface BenchmarkRun {
  id: string;
  created_at: string;
  label: string;
  status: "running" | "done" | "failed";
  error: string | null;
  question_count: number;
  config: Record<string, unknown>;
  aggregates: Record<string, PipelineAggregate>;
  by_type: Record<string, Record<string, TypeAggregate>>;
  build_cost: Record<string, unknown>;
}

export interface BenchmarkQuestionResult {
  index: number;
  query: string;
  query_type: string;
  expected_answer: string | null;
  relevant_ids: string[];
  pipelines: Record<
    string,
    {
      status: string;
      error: string | null;
      answer: string;
      retrieved_ids: string[];
      total_ms: number;
      cost_usd: number;
      recall_at_5: number | null;
      correctness: number | null;
    }
  >;
}

export interface BenchmarkDetail extends BenchmarkRun {
  results: BenchmarkQuestionResult[];
}

/* ----------------------------------------------------- source inspector */

export interface InspectChunk {
  id: string;
  index: number;
  page_start: number | null;
  page_end: number | null;
  char_start: number | null;
  char_end: number | null;
  text: string;
  /** Entities the model found in this passage. */
  entity_ids: string[];
}

export interface InspectEntity {
  id: string;
  name: string;
  type: string;
  aliases: string[];
  descriptions: string[];
  chunk_ids: string[];
}

export interface InspectRelationship {
  source: string;
  source_name: string;
  relation: string;
  target: string;
  target_name: string;
  description: string | null;
  confidence: number | null;
  raw_relation: string | null;
  chunk_ids: string[];
}

export interface SourceInspection {
  source_id: string;
  filename: string | null;
  chunks: InspectChunk[];
  entities: InspectEntity[];
  relationships: InspectRelationship[];
}
