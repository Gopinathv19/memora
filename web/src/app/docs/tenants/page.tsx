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

export const metadata = { title: "Tenants" };

export default function TenantsPage() {
  return (
    <>
      <DocHeader eyebrow="Ownership chain" title="Tenants">
        A tenant is an organization. It is the top of the chain: every
        application, actor, subject and source belongs to exactly one, and no
        query crosses between them.
      </DocHeader>

      <Callout title="Console session required">
        Tenants are managed by a signed-in person, not by an application. An API
        credential is issued <em>inside</em> a tenant and cannot create or list
        them — it sees only its own, through the resources below it.
      </Callout>

      <Section title="Create a tenant">
        <Endpoint method="POST" path="/api/v1/tenants" />
        <Fields
          rows={[
            {
              name: "name",
              type: "string",
              required: true,
              description: "Display name of the organization.",
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
            python: `import httpx

session = httpx.Client(base_url="${API_BASE_URL}/api/v1")
session.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})

tenant = session.post("/tenants", json={"name": "Pranav Tools"}).json()`,
            js: `// The session cookie is set by the login call; \`credentials: "include"\`
// is what sends it back on every subsequent request.
await fetch("${API_BASE_URL}/api/v1/auth/login", {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, password }),
});

const tenant = await fetch("${API_BASE_URL}/api/v1/tenants", {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ name: "Pranav Tools" }),
}).then((r) => r.json());`,
          }}
        />
        <ResponseBlock
          status={201}
          json={`{
  "id": "a41e…",
  "name": "Pranav Tools",
  "status": "active",
  "created_at": "2026-09-22T17:02:11.418Z",
  "application_count": 0,
  "subject_count": 0,
  "source_count": 0
}`}
        />
      </Section>

      <Section title="List tenants">
        <Endpoint method="GET" path="/api/v1/tenants">
          Every tenant the caller owns, each with live counts of what sits
          beneath it.
        </Endpoint>
        <CodeTabs
          sample={{
            python: `session.get("/tenants").json()`,
            js: `await fetch("${API_BASE_URL}/api/v1/tenants", {
  credentials: "include",
}).then((r) => r.json());`,
          }}
        />
      </Section>

      <Section title="Get one tenant">
        <Endpoint method="GET" path="/api/v1/tenants/{tenant_id}" />
      </Section>

      <Section title="Update a tenant">
        <Endpoint method="PATCH" path="/api/v1/tenants/{tenant_id}" />
        <Fields
          rows={[
            { name: "name", type: "string", description: "New display name." },
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
        <P>
          Only the fields you send are changed. There is no delete: a tenant is
          suspended rather than removed, because everything below it would go
          with it.
        </P>
      </Section>

      <Section title="Applications inside a tenant">
        <Endpoint method="POST" path="/api/v1/tenants/{tenant_id}/applications" />
        <Endpoint method="GET" path="/api/v1/tenants/{tenant_id}/applications" />
        <P>
          Both are covered in{" "}
          <Link href="/docs/applications" className="text-accent hover:underline">
            Applications
          </Link>
          .
        </P>
      </Section>
    </>
  );
}
