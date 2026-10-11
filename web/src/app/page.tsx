import Link from "next/link";

import { CodeTabs, ResponseBlock } from "@/components/CodeTabs";
import { API_BASE_URL, CONSOLE_URL } from "@/lib/config";

/**
 * The landing page.
 *
 * It explains what Memora is and then gets out of the way: every action a
 * visitor might want -- sign in, create a tenant, issue a credential -- happens
 * in the console, so every call to action here is a link into it rather than a
 * form. The only thing this site does itself is explain the API.
 *
 * The page is ordered the way a reader decides: what Memora does with their
 * knowledge (the pipeline), what one call looks like, and only then the
 * ownership chain that makes it safe to hand over.
 */

const PIPELINE = [
  {
    label: "Ingest",
    endpoint: "POST /subjects/{id}/sources/upload",
    href: "/docs/sources",
    detail:
      "Upload a file or paste raw text or a chat transcript, up to 50 MiB. The bytes are stored and the source is tied to its tenant, application and subject.",
  },
  {
    label: "Extract",
    endpoint: "POST /sources/{id}/extractions",
    href: "/docs/extractions",
    detail:
      "Every page is triaged: plain text is read locally, while scans, slides and figures go to layout and vision models. Each run is a new version, with its tokens and cost recorded.",
  },
  {
    label: "Chunk",
    endpoint: "POST /sources/{id}/rechunk",
    href: "/docs/chunks",
    detail:
      "Structure-aware chunks that keep their section path and page range. Rechunking reuses the stored readings, so it makes no model calls and costs nothing.",
  },
  {
    label: "Embed",
    endpoint: "POST /embedding/sources/{id}/embed",
    href: "/docs/embeddings",
    detail:
      "Vectors in pgvector behind an HNSW index. Strategies are versioned, so changing the model never silently invalidates the vectors you already have.",
  },
  {
    label: "Graph",
    endpoint: "POST /sources/{id}/graph",
    href: "/docs/graph",
    detail:
      "Entities and relationships are written to a knowledge graph. Every fact points back to the chunk it was read from.",
  },
  {
    label: "Ask",
    endpoint: "POST /query",
    href: "/docs/retrieval",
    detail:
      "Takes the 50 nearest chunks, keeps 10 diverse ones with MMR, reranks them down to 5 and writes an answer from them, returned with every chunk it used.",
  },
];

const CHAIN = [
  {
    label: "Tenant",
    detail: "An organization. Everything below belongs to exactly one.",
  },
  {
    label: "Application",
    detail: "A consuming system. Holds its own API credentials.",
  },
  {
    label: "Actor",
    detail: "Who or what operates: a user, a service, an agent, a job.",
  },
  {
    label: "Subject",
    detail: "A workspace, nestable as folders. The boundary sources live in.",
  },
  {
    label: "Source",
    detail: "Registered knowledge: a file, pasted text, a conversation, a URL.",
  },
];

const QUERY_RESPONSE = `{
  "query": "What is the payment term on the Globex invoice?",
  "answer": "Payment is due within 30 days, by 2026-10-01 (Terms, page 2).",
  "strategy_name": "default",
  "chunks": [
    {
      "chunk_id": "9c2e…",
      "source_filename": "INV-2041.pdf",
      "section_path": ["Terms"],
      "page_start": 2,
      "page_end": 2,
      "similarity": 0.83,
      "rerank_score": 0.97,
      "content": "Payment terms: net 30 days from the invoice date…"
    }
  ],
  "retrieval": {
    "hnsw_candidates": 50,
    "mmr_candidates": 10,
    "final_chunks": 5,
    "reranker_used": true
  },
  "usage": { "prompt_tokens": 2140, "completion_tokens": 38, "latency_ms": 1820 }
}`;

export default function LandingPage() {
  return (
    <>
      <section className="mx-auto max-w-7xl px-4 pb-16 pt-20 sm:px-6">
        <div className="max-w-2xl">
          <h1 className="text-4xl font-medium leading-tight tracking-tight text-ink sm:text-5xl">
            Memory for your applications,
            <br />
            with an owner all the way down.
          </h1>
          <p className="mt-5 max-w-xl text-base leading-relaxed text-ink-secondary">
            Give Memora your files, notes and conversations. It reads them,
            including scanned pages, slides and tables. It chunks and embeds
            them, maps the entities they mention, and answers questions with
            citations back to the page. Every step runs inside the tenant and
            application that own the data.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <a
              href={CONSOLE_URL}
              className="inline-flex h-10 items-center justify-center rounded-md border border-brand bg-brand px-4 text-sm font-medium text-brand-ink transition-colors hover:border-brand-hover hover:bg-brand-hover"
            >
              Open the console
            </a>
            <Link
              href="/docs"
              className="inline-flex h-10 items-center justify-center rounded-md border border-line bg-panel px-4 text-sm font-medium text-ink transition-colors hover:bg-surface-strong"
            >
              Read the API reference
            </Link>
          </div>
        </div>
      </section>

      <section className="border-y border-line bg-surface">
        <div className="mx-auto max-w-7xl px-4 py-14 sm:px-6">
          <h2 className="text-xs font-medium uppercase tracking-wider text-ink-tertiary">
            From a file to an answer
          </h2>
          <p className="mt-2 max-w-2xl text-sm text-ink-secondary">
            Six stages, each its own endpoint. Upload with{" "}
            <code className="font-mono text-[12.5px] text-ink">extract=true</code>{" "}
            and the first four run on their own. Every stage can also be
            re-run separately when you change how it works.
          </p>

          <ol className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {PIPELINE.map((stage, index) => (
              <li key={stage.label}>
                <Link
                  href={stage.href}
                  className="block h-full rounded-lg border border-line bg-panel p-4 transition-colors hover:border-accent/40"
                >
                  <span className="font-mono text-xs text-ink-tertiary">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <p className="mt-1 text-sm font-medium text-ink">{stage.label}</p>
                  <code className="mt-1 block truncate font-mono text-[11.5px] text-accent">
                    {stage.endpoint}
                  </code>
                  <p className="mt-2 text-xs leading-relaxed text-ink-secondary">
                    {stage.detail}
                  </p>
                </Link>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
        <div className="grid gap-10 lg:grid-cols-5">
          <div className="lg:col-span-2">
            <h2 className="text-lg font-medium text-ink">Ask in one call</h2>
            <p className="mt-2 text-sm leading-relaxed text-ink-secondary">
              Send a question and get back an answer together with the chunks
              behind it. Each chunk carries its file name, section, page range
              and the score from every stage, so the answer can be checked.
            </p>
            <p className="mt-3 text-sm leading-relaxed text-ink-secondary">
              Narrow the search to one subject or one source. If you would
              rather write the answer with your own model and prompt, pass{" "}
              <code className="font-mono text-[12.5px] text-ink">generate=false</code>{" "}
              to get the ranked chunks only.
            </p>
            <Link
              href="/docs/retrieval"
              className="mt-4 inline-block text-sm font-medium text-accent hover:underline"
            >
              Retrieval reference →
            </Link>
          </div>
          <div className="min-w-0 lg:col-span-3">
            <CodeTabs
              sample={{
                python: `import httpx

answer = httpx.post(
    "${API_BASE_URL}/api/v1/query",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"query": "What is the payment term on the Globex invoice?"},
).json()

print(answer["answer"])`,
                js: `const response = await fetch("${API_BASE_URL}/api/v1/query", {
  method: "POST",
  headers: {
    Authorization: \`Bearer \${TOKEN}\`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    query: "What is the payment term on the Globex invoice?",
  }),
});
const { answer, chunks } = await response.json();`,
                curl: `curl -s -X POST ${API_BASE_URL}/api/v1/query \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"query":"What is the payment term on the Globex invoice?"}'`,
              }}
            />
            <ResponseBlock json={QUERY_RESPONSE} />
          </div>
        </div>
      </section>

      <section className="border-y border-line bg-surface">
        <div className="mx-auto max-w-7xl px-4 py-14 sm:px-6">
          <h2 className="text-xs font-medium uppercase tracking-wider text-ink-tertiary">
            The ownership chain
          </h2>
          <p className="mt-2 max-w-2xl text-sm text-ink-secondary">
            Five levels, each owned by the one above it. Every chunk, vector,
            graph fact and answer belongs to a source, so the same chain
            decides what each query is allowed to read.
          </p>

          <ol className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {CHAIN.map((level, index) => (
              <li
                key={level.label}
                className="rounded-lg border border-line bg-panel p-4"
              >
                <span className="font-mono text-xs text-ink-tertiary">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <p className="mt-1 text-sm font-medium text-ink">{level.label}</p>
                <p className="mt-1 text-xs leading-relaxed text-ink-secondary">
                  {level.detail}
                </p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
        <div className="grid gap-x-8 gap-y-10 sm:grid-cols-2 lg:grid-cols-3">
          <Feature title="Reads what text extraction misses">
            Easy pages are read locally for free. Only hard pages, such as
            scans, slides and charts, are sent to layout and vision models.
            Deep mode sends every page to the layout model.
          </Feature>
          <Feature title="Answers you can check">
            Every answer comes with the chunks it used, their pages and the
            score from each stage. If nothing you own matches, Memora says so
            instead of making an answer up.
          </Feature>
          <Feature title="A graph, not just vectors">
            Entities are merged across spellings and linked by relationships.
            Asking the graph runs no model, so it costs no credits.
          </Feature>
          <Feature title="Scoped by construction">
            A caller&apos;s reach comes from its credential, never from the ids
            in its URL. Retrieval applies the same tenant and application
            filters inside the vector search itself.
          </Feature>
          <Feature title="Every token is billed to someone">
            Each model call is recorded with its tokens, cost, and the
            application, credential or actor that triggered it. One usage
            report breaks it all down.
          </Feature>
          <Feature title="Rebuild without starting over">
            Extractions are versioned and chunks are derived from them. When an
            embedding input changes, a new row is written and the old one is
            kept, so you can re-run any stage without losing history.
          </Feature>
        </div>
      </section>

      <section className="border-t border-line bg-surface">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-12 sm:px-6">
          <div>
            <h2 className="text-lg font-medium text-ink">
              Everything happens in the console
            </h2>
            <p className="mt-1 max-w-xl text-sm text-ink-secondary">
              Create a tenant, issue a credential, upload a document, watch it
              move through the pipeline, then ask it questions. This site only
              explains the API behind it.
            </p>
          </div>
          <a
            href={CONSOLE_URL}
            className="inline-flex h-10 shrink-0 items-center justify-center rounded-md border border-brand bg-brand px-4 text-sm font-medium text-brand-ink transition-colors hover:border-brand-hover hover:bg-brand-hover"
          >
            Open the console
          </a>
        </div>
      </section>
    </>
  );
}

function Feature({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h3 className="text-sm font-medium text-ink">{title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-ink-secondary">
        {children}
      </p>
    </div>
  );
}
