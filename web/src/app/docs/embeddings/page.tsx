import {
  C,
  Callout,
  DocHeader,
  Endpoint,
  Fields,
  P,
  Section,
  Ul,
} from "@/components/docs";
import { CodeTabs, ResponseBlock } from "@/components/CodeTabs";
import { API_BASE_URL } from "@/lib/config";

export const metadata = { title: "Embeddings" };

const STRATEGY_JSON = `{
  "id": "3f1c…",
  "name": "Memora Dense v1",
  "description": "Default strategy over NVIDIA Nemotron 3 Embed 1B",
  "model_id": "9a2e…",
  "input_type": "embedding_text",
  "document_template": "{input}",
  "query_template": "{input}",
  "normalization": "l2",
  "similarity_metric": "cosine",
  "dimension": 2048,
  "configuration_json": {},
  "is_active": true,
  "created_at": "2026-10-01T09:12:44.021Z",
  "updated_at": "2026-10-01T09:12:44.021Z",
  "model_provider": "nvidia",
  "model_name": "Nemotron 3 Embed 1B",
  "model_identifier": "nvidia/nemotron-3-embed-1b"
}`;

const ENQUEUE_JSON = `{
  "enqueued": 12,
  "strategy_id": "3f1c…",
  "strategy_name": "Memora Dense v1"
}`;

const STATS_JSON = `{
  "total_chunks": 48,
  "embedded": 45,
  "pending": 0,
  "processing": 3,
  "failed": 0,
  "stale": 0,
  "coverage_percent": 93.75
}`;

export default function EmbeddingsPage() {
  return (
    <>
      <DocHeader eyebrow="Processing" title="Embeddings">
        Memora turns each retrieval chunk into a vector — a list of numbers
        that captures the meaning of the text — and stores it in PostgreSQL
        with a vector index. A later question becomes a vector too, and the
        chunks closest to it are the evidence used to answer. Everything on
        this page is about producing and tracking those vectors.
      </DocHeader>

      <Callout title="A model is not a strategy">
        A <em>model</em> is an actual embedding model at a provider, with
        physical properties: dimension, normalization, similarity metric. A{" "}
        <em>strategy</em> is how Memora uses one: which chunk field is the
        input, what templates wrap it, and the metric. Multiple strategies can
        coexist — the same chunk may carry vectors under several strategies —
        and exactly one is <C>is_active</C> at a time. Strategies are
        immutable after creation: a materially different configuration is a
        new strategy row, never an edit of an existing one.
      </Callout>

      <Section title="How embedding works">
        <Ul>
          <li>
            Each chunk's <C>embedding_text</C> (its content prefixed with
            the section path, prepared by chunking) is sent to the embedding
            model under a strategy. The returned vector is L2-normalized and
            stored with the strategy that produced it.
          </li>
          <li>
            Embedding is idempotent. Before each call the worker hashes{" "}
            <em>strategy + model + the exact text</em>; re-running over
            unchanged content is a no-op with no model call and no cost. Only
            changed content, a changed strategy, or a changed model version
            produces a new embedding.
          </li>
          <li>
            Embedding runs in the background. The embed endpoints below
            return <C>202</C> with the number of chunks queued; a worker
            drains the queue. Poll the status endpoints to watch coverage.
          </li>
          <li>
            A chunk's embedding moves through{" "}
            <C>pending → processing → completed</C>, or <C>failed</C> with
            the error kept and a bounded retry count. Failed embeddings can be
            reset to <C>pending</C> and retried.
          </li>
        </Ul>
      </Section>

      <Section title="Models and strategies">
        <Endpoint method="GET" path="/api/v1/embedding/models">
          The registered model catalog: provider, identifier, dimension,
          normalization and similarity metric of each model. Global data —
          readable in any scope. Adding a model is data, not a schema change.
        </Endpoint>

        <Endpoint method="GET" path="/api/v1/embedding/strategies">
          Every strategy, active and inactive, with the model fields joined
          in for display.
        </Endpoint>
        <ResponseBlock json={STRATEGY_JSON} />

        <Endpoint method="POST" path="/api/v1/embedding/strategies">
          Create a new strategy over an existing model. This is the entry
          point for introducing a new embedding configuration — for example to
          A/B test a different model or different templates. Dimension,
          normalization and similarity metric are inherited from the model
          row, never supplied: the vector column and index are typed for
          them.
        </Endpoint>
        <Fields
          rows={[
            {
              name: "name",
              type: "string",
              required: true,
              description: <>The strategy's name, e.g. "Memora Dense v2".</>,
            },
            {
              name: "description",
              type: "string",
              description: <>Optional human-readable note.</>,
            },
            {
              name: "model_id",
              type: "uuid",
              required: true,
              description: (
                <>A row from <C>GET /embedding/models</C>.</>
              ),
            },
            {
              name: "input_type",
              type: "string",
              description: (
                <>Which chunk field is embedded. Only{" "}
                <C>embedding_text</C> is supported.</>
              ),
            },
            {
              name: "document_template",
              type: "string",
              description: (
                <>Template wrapping document text; must contain the{" "}
                <C>{"{input}"}</C> placeholder. Default <C>{"{input}"}</C>{" "}
                (identity).</>
              ),
            },
            {
              name: "query_template",
              type: "string",
              description: (
                <>Template wrapping the query; must contain{" "}
                <C>{"{input}"}</C>. Default <C>{"{input}"}</C>.</>
              ),
            },
            {
              name: "configuration_json",
              type: "object",
              description: <>Free-form extras, kept for provenance.</>,
            },
          ]}
        />

        <Endpoint method="GET" path="/api/v1/embedding/strategies/{strategy_id}">
          One strategy.
        </Endpoint>

        <Endpoint method="PATCH" path="/api/v1/embedding/strategies/{strategy_id}">
          Update the mutable fields only: <C>description</C> and{" "}
          <C>is_active</C>. Everything that determines the vector is immutable
          — version by creating a new strategy. Flipping <C>is_active</C> is
          how the active strategy is switched; from then on new embeds and
          default queries use it.
        </Endpoint>
        <Fields
          rows={[
            {
              name: "description",
              type: "string",
              description: <>New description.</>,
            },
            {
              name: "is_active",
              type: "boolean",
              description: (
                <>Make this the active strategy. Only one strategy is active
                at a time.</>
              ),
            },
          ]}
        />
      </Section>

      <Section title="Embedding work">
        <Endpoint method="POST" path="/api/v1/embedding/sources/{source_id}/embed">
          Enqueue the source's active chunks for embedding. Idempotent:
          chunks that already have a current embedding are skipped unless{" "}
          <C>force</C> is true.
        </Endpoint>
        <Fields
          title="Body"
          rows={[
            {
              name: "strategy_id",
              type: "uuid",
              description: (
                <>Embed under this strategy; the active strategy if omitted.</>
              ),
            },
            {
              name: "force",
              type: "boolean",
              description: (
                <>Re-embed even when the input hash is unchanged.</>
              ),
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `res = client.post(f"/embedding/sources/{source_id}/embed", json={}).json()
# res["enqueued"] == 12  → 12 chunks queued, worker drains them`,
            js: `const res = await memora(\`/embedding/sources/\${sourceId}/embed\`, {
  method: "POST",
  body: JSON.stringify({}),
});`,
            curl: `curl -s -X POST ${API_BASE_URL}/api/v1/embedding/sources/$SOURCE_ID/embed \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" -d '{}'`,
          }}
        />
        <ResponseBlock json={ENQUEUE_JSON} status={202} />

        <Endpoint method="POST" path="/api/v1/embedding/subjects/{subject_id}/embed">
          Enqueue every active chunk under a subject — all of its sources.
          Same body and response as the source embed above.
        </Endpoint>

        <Endpoint method="POST" path="/api/v1/embedding/strategies/{strategy_id}/rebuild">
          Force re-embed everything in scope under a strategy. The input hash
          is unchanged, so this reuses each chunk's existing row rather
          than duplicating history.
        </Endpoint>

        <Endpoint method="POST" path="/api/v1/embedding/retry-failed">
          Reset failed embeddings in scope to <C>pending</C> and drain them.
          Returns <C>{"{ reset, strategy_id }"}</C>.
        </Endpoint>
      </Section>

      <Section title="Health and debugging">
        <Endpoint method="GET" path="/api/v1/embedding/stats">
          Embedding health inside the caller's scope: counts by status
          and coverage. The one-request answer to "why doesn't my
          query find X" — usually X was never embedded, or its embedding
          failed.
        </Endpoint>
        <ResponseBlock json={STATS_JSON} />

        <Endpoint method="GET" path="/api/v1/embedding/sources/{source_id}/status">
          The same counts for one source, for its detail page.
        </Endpoint>

        <Endpoint method="GET" path="/api/v1/embedding/chunks/{chunk_id}/embeddings">
          Every embedding row for one chunk: status, input hash, attempt
          count, error, and a vector preview. The debug view.
        </Endpoint>

        <Endpoint method="POST" path="/api/v1/embedding/query">
          Embed a query string under a strategy and return the vector.
          Retrieval preparation only — no search is performed here. Pass the
          same <C>strategy_id</C> to <C>POST /query</C> to search in that
          vector space.
        </Endpoint>
        <Fields
          rows={[
            {
              name: "query",
              type: "string",
              required: true,
              description: <>The text to embed.</>,
            },
            {
              name: "strategy_id",
              type: "uuid",
              description: (
                <>Embed under this strategy; the active strategy if omitted.</>
              ),
            },
          ]}
        />
      </Section>

      <Section title="Introducing a new embedding model">
        <P>
          Because models and strategies are data, bringing in a new embedding
          model is a sequence of API calls, not a migration:
        </P>
        <Ul>
          <li>
            Register the model (a row in the catalog — provider, identifier,
            dimension, normalization, metric).
          </li>
          <li>
            <C>POST /embedding/strategies</C> over it. The old strategy stays
            untouched.
          </li>
          <li>
            <C>POST /embedding/strategies/{"{id}"}/rebuild</C> to embed the
            corpus under the new strategy. Old embeddings are not overwritten;
            both sets coexist.
          </li>
          <li>
            Query with the new <C>strategy_id</C> and compare answers and
            scores against the old one.
          </li>
          <li>
            Flip <C>is_active</C> with <C>PATCH</C> when satisfied. The old
            strategy's vectors remain, with their provenance, for
            comparison or rollback.
          </li>
        </Ul>
      </Section>
    </>
  );
}
