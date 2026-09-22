import Link from "next/link";

import {
  C,
  Callout,
  DocHeader,
  Endpoint,
  Fields,
  P,
  Section,
} from "@/components/docs";
import { CodeTabs, ResponseBlock } from "@/components/CodeTabs";
import { API_BASE_URL } from "@/lib/config";

export const metadata = { title: "Applications" };

export default function ApplicationsPage() {
  return (
    <>
      <DocHeader eyebrow="Ownership chain" title="Applications">
        An application is a consuming system inside one tenant — the thing that
        holds API credentials and, through them, does the day-to-day work. Its
        actors, subjects and sources never mix with another application&apos;s.
      </DocHeader>

      <Section title="Create an application">
        <Endpoint method="POST" path="/api/v1/tenants/{tenant_id}/applications" />
        <Fields
          rows={[
            {
              name: "name",
              type: "string",
              required: true,
              description: "Display name, e.g. ThinkFill.",
            },
            {
              name: "slug",
              type: "string",
              required: true,
              description:
                "Lowercase words separated by hyphens. Unique within the tenant.",
            },
            {
              name: "status",
              type: "string",
              description: (
                <>
                  <C>active</C> or <C>suspended</C>. Defaults to <C>active</C>.
                </>
              ),
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `application = session.post(
    f"/tenants/{tenant['id']}/applications",
    json={"name": "ThinkFill", "slug": "thinkfill"},
).json()`,
            js: `const application = await fetch(
  \`${API_BASE_URL}/api/v1/tenants/\${tenant.id}/applications\`,
  {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "ThinkFill", slug: "thinkfill" }),
  },
).then((r) => r.json());`,
          }}
        />
        <ResponseBlock
          status={201}
          json={`{
  "id": "6f1c…",
  "tenant_id": "a41e…",
  "name": "ThinkFill",
  "slug": "thinkfill",
  "status": "active",
  "created_at": "2026-09-22T17:04:38.913Z",
  "tenant_name": "Pranav Tools",
  "credential_count": 0,
  "actor_count": 0,
  "subject_count": 0,
  "source_count": 0
}`}
        />
        <Callout tone="warn" title="A duplicate slug is a 409">
          Slugs are unique per tenant, so re-running a setup script returns{" "}
          <C>409 conflict</C> rather than a second application. Treat that as
          &quot;already done&quot;, not as a failure.
        </Callout>
      </Section>

      <Section title="List applications">
        <Endpoint method="GET" path="/api/v1/applications">
          Flat list, optionally filtered by <C>?tenant_id=</C>.
        </Endpoint>
        <Endpoint method="GET" path="/api/v1/tenants/{tenant_id}/applications">
          The same rows, nested. Use whichever reads better in your code.
        </Endpoint>
        <CodeTabs
          sample={{
            python: `session.get("/applications", params={"tenant_id": tenant["id"]}).json()`,
            js: `await fetch(
  \`${API_BASE_URL}/api/v1/applications?tenant_id=\${tenant.id}\`,
  { credentials: "include" },
).then((r) => r.json());`,
            curl: `curl -s "${API_BASE_URL}/api/v1/applications?tenant_id=$TENANT_ID" \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
        />
        <P>
          Called with an API credential, this returns exactly one row — the
          application that credential belongs to — whatever <C>tenant_id</C> is
          passed.
        </P>
      </Section>

      <Section title="Get one application">
        <Endpoint method="GET" path="/api/v1/applications/{application_id}" />
      </Section>

      <Section title="Update an application">
        <Endpoint method="PATCH" path="/api/v1/applications/{application_id}" />
        <Fields
          rows={[
            { name: "name", type: "string", description: "New display name." },
            {
              name: "slug",
              type: "string",
              description: "New slug. Still unique within the tenant.",
            },
            {
              name: "status",
              type: "string",
              description: (
                <>
                  <C>active</C> or <C>suspended</C>.
                </>
              ),
            },
          ]}
        />
      </Section>

      <Section title="What hangs below an application">
        <P>
          <Link href="/docs/credentials" className="text-accent hover:underline">
            Credentials
          </Link>
          ,{" "}
          <Link href="/docs/actors" className="text-accent hover:underline">
            actors
          </Link>{" "}
          and{" "}
          <Link href="/docs/subjects" className="text-accent hover:underline">
            subjects
          </Link>{" "}
          are all created under{" "}
          <C>/applications/{"{application_id}"}/…</C>.
        </P>
      </Section>
    </>
  );
}
