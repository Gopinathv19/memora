import { ApiError, request } from "./api";
import type {
  BenchmarkDetail,
  BenchmarkQuestion,
  BenchmarkRun,
  CompareResult,
  ExpandResult,
  GraphParams,
  IndexStatus,
  Pipeline,
  PipelineResult,
  SourceInspection,
} from "./graphrag-types";

const BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

const json = (body: unknown) => JSON.stringify(body);

export interface CompareStreamHandlers {
  onStart?: (pipelines: Pipeline[]) => void;
  onResult: (pipeline: Pipeline, result: PipelineResult) => void;
  onDone?: (done: Pick<CompareResult, "compare_id" | "overlap" | "lens">) => void;
}

/**
 * POST /compare/stream: server-sent events. Each pipeline's result arrives the
 * moment it finishes, so the faster one can be shown while the other is still
 * working. Rejects with an ApiError if the request itself fails.
 */
async function streamCompare(
  body: Record<string, unknown>,
  handlers: CompareStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/api/v1/compare/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: json(body),
      credentials: "include",
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(
      `Cannot reach the Memora API at ${BASE_URL}. Is the backend running?`,
      0,
    );
  }
  if (!response.ok || !response.body) {
    let detail = `Request failed with status ${response.status}`;
    try {
      const payload = JSON.parse(await response.text());
      if (typeof payload?.detail === "string") detail = payload.detail;
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(detail, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (block: string) => {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return;
    const payload = JSON.parse(data.join("\n"));
    if (event === "start") handlers.onStart?.(payload.pipelines);
    else if (event === "result") handlers.onResult(payload.pipeline, payload.result);
    else if (event === "done") handlers.onDone?.(payload);
    else if (event === "error") throw new ApiError(payload.message, 502);
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatch(buffer.slice(0, sep));
      buffer = buffer.slice(sep + 2);
    }
  }
}

export const graphrag = {
  compareStream: (
    subjectId: string,
    query: string,
    params: GraphParams,
    pipelines: Pipeline[],
    handlers: CompareStreamHandlers,
    signal?: AbortSignal,
  ) =>
    streamCompare(
      { subject_id: subjectId, query, params, pipelines },
      handlers,
      signal,
    ),

  expand: (subjectId: string, entityIds: string[], limit = 60) =>
    request<ExpandResult>("/graph-rag/expand", {
      method: "POST",
      body: json({ subject_id: subjectId, entity_ids: entityIds, limit }),
    }),

  /** One source's own graph with the passages it was read from. */
  inspect: (sourceId: string) =>
    request<SourceInspection>(`/sources/${sourceId}/graph/inspect`),

  status: (subjectId: string) =>
    request<IndexStatus>(`/subjects/${subjectId}/graph-rag/status`),

  buildIndex: (subjectId: string, communities = true) =>
    request<{ status: string }>(`/subjects/${subjectId}/graph-rag/index`, {
      method: "POST",
      body: json({ communities }),
    }),

  benchmarks: {
    list: () => request<BenchmarkRun[]>("/compare/benchmarks"),
    get: (id: string) => request<BenchmarkDetail>(`/compare/benchmarks/${id}`),
    start: (body: {
      subjectId: string;
      label: string;
      questions: BenchmarkQuestion[];
      techniques: string[];
      includeHybrid: boolean;
      judge: boolean;
    }) =>
      request<BenchmarkRun>("/compare/benchmarks", {
        method: "POST",
        body: json({
          subject_id: body.subjectId,
          label: body.label,
          questions: body.questions,
          techniques: body.techniques,
          include_hybrid: body.includeHybrid,
          judge: body.judge,
        }),
      }),
  },
};
