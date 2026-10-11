import Link from "next/link";

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
  "id": "1d7e…",
  "name": "default",
  "description": "Nemotron embed, document/query templates",
  "model_id": "5c0a…",
  "input_type": "embedding_text",
  "document_template": "{input}",
  "query_template": "{input}",
  "normalization": "l2",
  "similarity_metric": "cosine",
  "dimension": 2048,
  "configuration_json": {},
  "is_active": true,
  "model_provider": "nvidia",
  "model_name": "nemotron-3-embed-1b",
  "model_identifier": "nvidia/nemotron-3-embed-1b",
  "created_at": "2026-10-02T08:11:40.512Z",
  "updated_at": "2026-10-02T08:11:40.512Z"
}`;

const STATUS_JSON = `{
  "source_id": "4b19…",
  "strategy_id": "1d7e…",
  "strategy_name": "default",
  "total_chunks": 42,
  "embedded": 40,
  "pending": 2,
  "processing": 0,
  "failed": 0,
  "stale": 3,
  "coverage_percent": 95.24
}`;

export default function EmbeddingsPage() {
  return (
    <>
      <DocHeader eyebrow="Processing" title="Embeddings">
        Every active chunk is turned into a vector and stored in PostgreSQL
        with pgvector, behind an HNSW index. These vectors are what{" "}
        <Link href="/docs/retrieval" className="text-accent hover:underline">
          <C>POST /query</C>
        </Link>{" "}
        searches.
      </DocHeader>

      <Callout title="Usually automatic">
        When a source is chunked, whether after an extraction or by a{" "}
        <Link href="/docs/chunks" className="text-accent hover:underline">
          rechunk
        </Link>
        , its new chunks are queued and embedded in the background. The
        endpoints below are for embedding under a different strategy, forcing
        a rebuild, retrying failures and checking progress.
      </Callout>

      <Section title="How embedding works">
        <Ul>
          <li>
            <strong className="font-medium text-ink">Queued, not blocking.</strong>{" "}
            The embed endpoints record the work and return <C>202</C> at once.
            A background worker then embeds the queued chunks. Poll the status
            endpoints to follow progress.
          </li>
          <li>
            <strong className="font-medium text-ink">Idempotent.</strong> Each
            embedding stores a hash of its input. A chunk whose input hasn&apos;t
            changed is skipped, so re-embedding a subject only does the work
            that&apos;s needed.
          </li>
          <li>
            <strong className="font-medium text-ink">History is kept.</strong>{" "}
            When a chunk&apos;s input changes, a new embedding is written and the
            old one is marked <C>stale</C> rather than overwritten.
          </li>
          <li>
            <strong className="font-medium text-ink">Scoped.</strong> Every
            enqueue, retry and report covers only the chunks your credential
            can see.
          </li>
        </Ul>
        <P>An embedding&apos;s status is one of:</P>
        <Ul>
          <li>
            <C>pending</C>: queued and waiting for the worker.
          </li>
          <li>
            <C>processing</C>: being embedded now.
          </li>
          <li>
            <C>completed</C>: a current vector exists and the chunk is
            searchable.
          </li>
          <li>
            <C>failed</C>: the model call failed. Use{" "}
            <C>POST /embedding/retry-failed</C> to queue it again.
          </li>
          <li>
            <C>stale</C>: an older vector whose chunk input has since changed.
            It is kept for history and never searched.
          </li>
        </Ul>
      </Section>

      <Section title="Models and strategies">
        <P>
          A <strong className="font-medium text-ink">model</strong> is an
          embedding model the server can call. A{" "}
          <strong className="font-medium text-ink">strategy</strong> is a model
          plus the templates that wrap each chunk and each query before they
          are embedded. Every vector records the strategy that produced it,
          and queries are always embedded with the same strategy as the
          vectors they are compared to.
        </P>
        <Endpoint method="GET" path="/api/v1/embedding/models">
          The registered models with their dimension, normalization and
          similarity metric.
        </Endpoint>
        <Endpoint method="GET" path="/api/v1/embedding/strategies">
          Every strategy. When an endpoint takes an optional{" "}
          <C>strategy_id</C> and you leave it out, the active strategy is used.
        </Endpoint>
        <Endpoint method="GET" path="/api/v1/embedding/strategies/{strategy_id}" />
        <ResponseBlock json={STRATEGY_JSON} />

        <Endpoint method="POST" path="/api/v1/embedding/strategies">
          Creates a strategy over an existing, active model. Its dimension,
          normalization and metric are copied from the model. Returns{" "}
          <C>201</C>.
        </Endpoint>
        <Fields
          rows={[
            { name: "name", type: "string", required: true, description: "Unique. A duplicate name returns 409." },
            { name: "model_id", type: "uuid", required: true, description: "An active model from GET /embedding/models." },
            { name: "description", type: "string | null", description: "Free text." },
            {
              name: "document_template",
              type: "string",
              description: (
                <>
                  How each chunk is wrapped before embedding. Must contain{" "}
                  <C>{"{input}"}</C>. Defaults to <C>{"{input}"}</C>.
                </>
              ),
            },
            {
              name: "query_template",
              type: "string",
              description: (
                <>
                  How each query is wrapped. Must contain <C>{"{input}"}</C>.
                  Defaults to <C>{"{input}"}</C>.
                </>
              ),
            },
            { name: "input_type", type: "string", description: <>Only <C>embedding_text</C> is supported.</> },
            { name: "configuration_json", type: "object", description: "Extra settings stored with the strategy." },
          ]}
        />

        <Endpoint method="PATCH" path="/api/v1/embedding/strategies/{strategy_id}">
          Changes <C>description</C> and <C>is_active</C> only.
        </Endpoint>
        <Callout tone="warn" title="Strategies are immutable">
          The model, templates and dimension decide what a vector means.
          Changing them would silently invalidate every vector the strategy
          already produced. To change them, create a new strategy and embed
          under it. The old vectors keep pointing at the strategy that made
          them.
        </Callout>
        <P>
          Models and strategies are shared by the whole Memora server, not
          owned by a tenant.
        </P>
      </Section>

      <Section title="Embed a source or a subject">
        <Endpoint method="POST" path="/api/v1/embedding/sources/{source_id}/embed">
          Queues the source&apos;s active chunks.
        </Endpoint>
        <Endpoint method="POST" path="/api/v1/embedding/subjects/{subject_id}/embed">
          Queues the active chunks of every source in the subject.
        </Endpoint>
        <Fields
          title="Body (optional)"
          rows={[
            { name: "strategy_id", type: "uuid | null", description: "Defaults to the active strategy." },
            {
              name: "force",
              type: "boolean",
              description: (
                <>
                  <C>true</C> re-embeds chunks that already have a current
                  vector. Defaults to <C>false</C>.
                </>
              ),
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `queued = client.post(f"/embedding/sources/{source_id}/embed", json={}).json()
# {"enqueued": 42, "strategy_id": "1d7e…", "strategy_name": "default"}`,
            js: `const queued = await memora(\`/embedding/sources/\${sourceId}/embed\`, {
  method: "POST",
  body: JSON.stringify({}),
});`,
            curl: `curl -s -X POST ${API_BASE_URL}/api/v1/embedding/sources/$SOURCE_ID/embed \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
        />
        <P>
          The response returns <C>202</C> with <C>enqueued</C>, the number of
          chunks queued by this call. <C>0</C> means everything was already
          embedded.
        </P>

        <Endpoint method="POST" path="/api/v1/embedding/strategies/{strategy_id}/rebuild">
          Re-embeds every chunk in your scope under this strategy, as if{" "}
          <C>force</C> were set. Existing rows are reset to <C>pending</C>{" "}
          rather than duplicated.
        </Endpoint>
        <Endpoint method="POST" path="/api/v1/embedding/retry-failed">
          Resets every <C>failed</C> embedding in your scope to{" "}
          <C>pending</C> and processes them again. Takes an optional{" "}
          <C>strategy_id</C>. Returns <C>{`{"reset": n}`}</C>.
        </Endpoint>
      </Section>

      <Section title="Progress and health">
        <Endpoint method="GET" path="/api/v1/embedding/sources/{source_id}/status">
          Counts for one source under the active strategy. A source is fully
          searchable once <C>coverage_percent</C> reaches <C>100</C>.
        </Endpoint>
        <ResponseBlock json={STATUS_JSON} />
        <Endpoint method="GET" path="/api/v1/embedding/stats">
          The same counts across everything your credential can see.
        </Endpoint>
        <Endpoint method="GET" path="/api/v1/embedding/chunks/{chunk_id}/embeddings">
          Every embedding row for one chunk, including stale ones, with
          strategy, status, input hash, attempt count, error and the first few
          values of the vector. Use it for debugging.
        </Endpoint>
      </Section>

      <Section title="Embed a query yourself">
        <Endpoint method="POST" path="/api/v1/embedding/query">
          Embeds a string with a strategy&apos;s query template and returns
          the vector. No search is run. Use it if you search your own vector
          store. To search Memora, use <C>POST /query</C>.
        </Endpoint>
        <Fields
          rows={[
            { name: "query", type: "string", required: true, description: "The text to embed." },
            { name: "strategy_id", type: "uuid | null", description: "Defaults to the active strategy." },
          ]}
        />
        <P>
          Returns <C>vector</C>, <C>dimension</C>, <C>similarity_metric</C>,
          the strategy used and the token <C>usage</C>.
        </P>
      </Section>

      <Section title="Errors">
        <Ul>
          <li>
            <C>404</C>: the source, subject, chunk or strategy is outside your
            scope or doesn&apos;t exist, or no strategy is active.
          </li>
          <li>
            <C>409</C>: a strategy with that name already exists.
          </li>
          <li>
            <C>422</C>: the model is inactive, a template is missing{" "}
            <C>{"{input}"}</C>, or the <C>input_type</C> is unsupported.
          </li>
        </Ul>
      </Section>
    </>
  );
}
