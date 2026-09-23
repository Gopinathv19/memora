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
            registered knowledge: an uploaded file, a URL, a conversation.
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
          — it goes from an empty console to a registered source in six calls.
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

      <Section title="What Memora does not do yet">
        <P>
          Registering a source records it and stores its bytes. Extraction,
          chunking, embeddings and retrieval are a later phase — a new source
          stays <C>pending</C>, and nothing currently advances it. Build against
          the ownership model now; the processing pipeline hangs below a source
          without changing anything above it.
        </P>
      </Section>
    </>
  );
}
