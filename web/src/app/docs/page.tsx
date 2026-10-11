import Link from "next/link";

import { C, Callout, DocHeader, P, Section, Ul } from "@/components/docs";
import { CodeTabs } from "@/components/CodeTabs";
import { API_BASE_URL } from "@/lib/config";

export const metadata = { title: "API overview" };

export default function OverviewPage() {
  return (
    <>
      <DocHeader eyebrow="Reference" title="Overview">
        Memora is a JSON API over one ownership chain. Every endpoint creates,
        reads or updates a level of that chain, and every response is scoped to
        what the caller&apos;s credential is allowed to see.
      </DocHeader>

      <Section title="Base URL">
        <P>
          All endpoints live under <C>/api/v1</C>. Responses are JSON, request
          bodies are JSON unless a file is being uploaded, and timestamps are
          ISO-8601 with an offset.
        </P>
        <CodeTabs
          sample={{
            python: `BASE = "${API_BASE_URL}/api/v1"`,
            js: `const BASE = "${API_BASE_URL}/api/v1";`,
            curl: `${API_BASE_URL}/api/v1`,
          }}
        />
      </Section>

      <Section title="The chain">
        <P>
          Five resources, each owned by the one above it. The nesting is not
          cosmetic — it is what scoping is enforced on.
        </P>
        <Ul>
          <li>
            <strong className="font-medium text-ink">Tenant</strong> — an
            organization. Everything else belongs to exactly one.
          </li>
          <li>
            <strong className="font-medium text-ink">Application</strong> — a
            consuming system inside a tenant. Holds the API credentials.
          </li>
          <li>
            <strong className="font-medium text-ink">Actor</strong> — who or
            what operates inside an application: a user, a service, an agent, a
            system job.
          </li>
          <li>
            <strong className="font-medium text-ink">Subject</strong> — a
            workspace. The boundary sources are scoped to.
          </li>
          <li>
            <strong className="font-medium text-ink">Source</strong> —
            registered knowledge: an uploaded file, pasted text, a
            conversation, a URL.
          </li>
        </Ul>
        <P>
          Creation is always nested under the parent —{" "}
          <C>POST /tenants/{"{tenant_id}"}/applications</C> — while listing is
          flat with filters — <C>GET /applications?tenant_id=…</C>. Use whichever
          reads better; both are scoped identically.
        </P>
      </Section>

      <Section title="Two kinds of caller">
        <P>
          Memora authenticates two things, and they are never interchangeable.
        </P>
        <Ul>
          <li>
            An <strong className="font-medium text-ink">API credential</strong> —
            a bearer token prefixed <C>memora_</C>, issued to one application. It
            can only ever see that application, inside that tenant.
          </li>
          <li>
            A <strong className="font-medium text-ink">console session</strong> —
            a signed-in human, holding a cookie. Reaches every tenant that person
            owns. This is what the console uses; it is not for programmatic
            integration.
          </li>
        </Ul>
        <Callout title="Scope is never taken from the request">
          An API credential that asks for a tenant it does not own gets{" "}
          <C>404</C>, not that tenant&apos;s data. Ids in a URL narrow a query;
          they never widen one. See{" "}
          <Link href="/docs/authentication" className="text-accent hover:underline">
            Authentication
          </Link>
          .
        </Callout>
      </Section>

      <Section title="Where to start">
        <P>
          If you are integrating an application, read{" "}
          <Link href="/docs/quickstart" className="text-accent hover:underline">
            Quickstart
          </Link>{" "}
          — it goes from an empty console to an answered question in seven
          steps.
          If you already have a token, go straight to{" "}
          <Link href="/docs/subjects" className="text-accent hover:underline">
            Subjects
          </Link>{" "}
          and{" "}
          <Link href="/docs/sources" className="text-accent hover:underline">
            Sources
          </Link>
          , which is where day-to-day traffic goes.
        </P>
      </Section>

      <Section title="What happens to a source">
        <P>
          Below the chain, every source can move through a processing pipeline.
          Each stage is its own endpoint and can be re-run on its own.
        </P>
        <Ul>
          <li>
            <Link href="/docs/extractions" className="text-accent hover:underline">
              Extraction
            </Link>{" "}
            reads the document page by page, sending only the hard pages to
            layout and vision models. Each run is versioned and its cost is
            recorded.
          </li>
          <li>
            <Link href="/docs/chunks" className="text-accent hover:underline">
              Chunking
            </Link>{" "}
            splits the extracted text into retrieval-sized chunks that keep
            their section and page. It makes no model calls.
          </li>
          <li>
            <Link href="/docs/embeddings" className="text-accent hover:underline">
              Embedding
            </Link>{" "}
            turns each chunk into a vector behind an HNSW index, under a
            versioned strategy.
          </li>
          <li>
            <Link href="/docs/retrieval" className="text-accent hover:underline">
              Retrieval
            </Link>{" "}
            — <C>POST /query</C> — answers a question from your chunks and
            returns the evidence alongside the answer.
          </li>
          <li>
            The{" "}
            <Link href="/docs/graph" className="text-accent hover:underline">
              knowledge graph
            </Link>{" "}
            maps entities and relationships across documents. Building it is
            optional.
          </li>
        </Ul>
        <Callout title="One call to start it all">
          Upload with <C>extract=true</C> (and <C>build_graph=true</C> if you
          want the graph) and extraction, chunking and embedding run in the
          background. When the source is <C>completed</C> and its embedding
          coverage reaches 100%, it can be queried.
        </Callout>
      </Section>
    </>
  );
}
